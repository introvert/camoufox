"""
Camoufox's TLS ClientHello is stock Firefox's.

A server sees the ClientHello before any page script runs, and JA3/JA4-style
fingerprints compare it with what the claimed browser sends. Camoufox claims
Firefox, so every field a fingerprint reads has to be what stock Firefox of the
version in browser/upstream.sh sends: a pref, a patch or a Firefox upgrade that
changes one would show on every TLS connection.

Both browsers open https://localhost:<port>/ against a socket that reads the
ClientHello and closes. Stock runs as stock-gpu-parity downloads it (or
--stock <firefox binary>); Camoufox runs through the launcher, as a user runs it.

What PASS means: the protocol versions, cipher suites, extensions (as a set:
NSS may permute their order), supported groups, key-share groups, signature
algorithms, ALPN and the other negotiated lists are identical.

    python browser/tests/playwright/patches/tls-client-hello-parity.py
    python browser/tests/playwright/patches/tls-client-hello-parity.py --stock FIREFOX
"""

import importlib.util
import json
import socket
import struct
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from helpers import launch_kwargs  # noqa: E402

TIMEOUT_S = 60

# Extensions whose body is a list a fingerprint reads, and how to read it.
LISTS = {
    0x000A: ("supported_groups", 2, 2),
    0x000B: ("ec_point_formats", 1, 1),
    0x000D: ("signature_algorithms", 2, 2),
    0x001B: ("compress_certificate", 1, 2),
    0x0022: ("delegated_credentials", 2, 2),
    0x002B: ("supported_versions", 1, 2),
    0x002D: ("psk_key_exchange_modes", 1, 1),
}


def _u16_list(data: bytes, prefix: int, width: int) -> list:
    size = int.from_bytes(data[:prefix], "big")
    body = data[prefix:prefix + size]
    return [int.from_bytes(body[i:i + width], "big") for i in range(0, len(body), width)]


def parse_client_hello(hello: bytes) -> dict:
    """The fields a TLS fingerprint reads, from a ClientHello handshake body."""
    pos = 2 + 32  # legacy_version, random
    legacy_version = struct.unpack_from(">H", hello, 0)[0]
    pos += 1 + hello[pos]  # session id
    (suites_len,) = struct.unpack_from(">H", hello, pos)
    suites = list(struct.unpack_from(f">{suites_len // 2}H", hello, pos + 2))
    pos += 2 + suites_len
    pos += 1 + hello[pos]  # compression methods
    (ext_total,) = struct.unpack_from(">H", hello, pos)
    pos += 2
    end = pos + ext_total
    fields = {"legacy_version": legacy_version, "cipher_suites": suites, "extensions": []}
    while pos < end:
        ext_type, ext_len = struct.unpack_from(">HH", hello, pos)
        body = hello[pos + 4:pos + 4 + ext_len]
        pos += 4 + ext_len
        fields["extensions"].append(ext_type)
        if ext_type in LISTS:
            name, prefix, width = LISTS[ext_type]
            fields[name] = _u16_list(body, prefix, width)
        elif ext_type == 0x0010:  # ALPN
            names, i = [], 2
            while i < len(body):
                names.append(body[i + 1:i + 1 + body[i]].decode())
                i += 1 + body[i]
            fields["alpn"] = names
        elif ext_type == 0x0033:  # key_share: the groups, not the keys
            groups, i = [], 2
            while i < len(body):
                group, size = struct.unpack_from(">HH", body, i)
                groups.append(group)
                i += 4 + size
            fields["key_share"] = groups
        elif ext_type == 0x001C:  # record_size_limit
            fields["record_size_limit"] = struct.unpack(">H", body)[0]
    fields["extensions"].sort()
    return fields


class HelloCatcher:
    """A TCP listener that keeps the first ClientHello sent to it."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.hello = None
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self.done.is_set():
            conn, _ = self.sock.accept()
            with conn:
                conn.settimeout(10)
                data = b""
                try:
                    while len(data) < 5 or len(data) < 5 + struct.unpack_from(">H", data, 3)[0]:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    continue
            # A record of type handshake (22) holding a ClientHello (1).
            if len(data) > 9 and data[0] == 22 and data[5] == 1:
                size = int.from_bytes(data[6:9], "big")
                self.hello = parse_client_hello(data[9:9 + size])
                self.done.set()

    @property
    def url(self):
        return f"https://localhost:{self.port}/"

    def wait(self, who: str) -> dict:
        if not self.done.wait(TIMEOUT_S):
            raise SystemExit(f"{who} sent no ClientHello within {TIMEOUT_S}s")
        return self.hello


def getter_cost_parity():
    """getter-cost-parity's stock Firefox launcher, which this guard shares."""
    spec = importlib.util.spec_from_file_location("getter_cost_parity", HERE / "getter-cost-parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stock_hello(stock) -> dict:
    catcher = HelloCatcher()
    profile, proc = stock.run_stock(stock.stock_firefox(), catcher.url)
    try:
        return catcher.wait("stock Firefox")
    finally:
        stock.stop_stock(profile, proc)


def camoufox_hello() -> dict:
    from camoufox.sync_api import Camoufox

    catcher = HelloCatcher()
    with Camoufox(**launch_kwargs()) as browser:
        page = browser.new_page()
        try:
            page.goto(catcher.url, timeout=TIMEOUT_S * 1000)
        except Exception:  # noqa: BLE001 -- the socket closes; only the hello matters
            pass
        return catcher.wait("Camoufox")


def main() -> int:
    stock = stock_hello(getter_cost_parity())
    camoufox = camoufox_hello()
    print("stock:   ", json.dumps(stock, sort_keys=True))
    print("camoufox:", json.dumps(camoufox, sort_keys=True))
    differ = sorted(k for k in stock.keys() | camoufox.keys() if stock.get(k) != camoufox.get(k))
    if differ:
        print(f"FAIL: the ClientHello differs from stock Firefox in {', '.join(differ)}")
        return 1
    print("PASS: the ClientHello is stock Firefox's")
    return 0


if __name__ == "__main__":
    sys.exit(main())
