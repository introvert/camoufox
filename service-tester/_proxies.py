import asyncio
import sys
from pathlib import Path


def load_proxies(path: Path) -> list:
    """
    Load proxies from a file. Each line must be: [scheme://]user:pass@domain:port
    The scheme defaults to http; socks5:// is also supported.
    Returns a list of Playwright-format proxy dicts.
    """
    if not path.exists():
        print(f"ERROR: Proxies file not found: {path}", file=sys.stderr)
        print("  Create a proxies.txt file with one proxy per line: user:pass@domain:port", file=sys.stderr)
        sys.exit(1)

    proxies = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        scheme = "http"
        if "://" in line:
            scheme, line = line.split("://", 1)
        try:
            creds, hostport = line.rsplit("@", 1)
            user, password = creds.split(":", 1)
            domain, port = hostport.rsplit(":", 1)
        except ValueError:
            print(f"ERROR: proxies.txt line {lineno}: expected user:pass@domain:port, got: {line!r}", file=sys.stderr)
            sys.exit(1)
        proxies.append({
            "server": f"{scheme}://{domain}:{port}",
            "username": user,
            "password": password,
            "bypass": "127.0.0.1,localhost",
        })

    if not proxies:
        print("ERROR: proxies.txt contains no valid proxy entries.", file=sys.stderr)
        sys.exit(1)

    return proxies


async def resolve_proxy_geo(proxy: dict) -> dict:
    """Queries ip-api.com through the proxy for IP, city, country, and timezone."""
    # urllib cannot speak SOCKS; requests (with PySocks, a camoufox dependency) can.
    import requests
    from camoufox.socks import requests_proxy_url

    proxy_url = requests_proxy_url(proxy)

    def _fetch() -> dict:
        try:
            resp = requests.get(
                "http://ip-api.com/json?fields=query,city,country,timezone",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=10,
            )
            return resp.json()
        except Exception:
            return {}

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)
