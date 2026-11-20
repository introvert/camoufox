"""
Camoufox opens an HTTP/2 connection as stock Firefox does.

Before any page script runs, a server reads the frames a browser sends on a new
HTTP/2 connection: the SETTINGS values in order, the connection's WINDOW_UPDATE,
any PRIORITY frames, and the order of the pseudo-headers in the first request.
Akamai-style fingerprints compare these with what the claimed browser sends,
alongside the TLS ClientHello (tls-client-hello-parity), so a pref, a patch or
a Firefox upgrade that changes one shows on every HTTPS request.

Both browsers load https://localhost:<port>/ from a TLS server that offers only
h2, signed by a throwaway CA. Stock Firefox (as stock-gpu-parity downloads it,
or --stock <firefox binary>) trusts that CA through the Certificates policy,
written into its distribution directory for the run and removed after;
Camoufox runs through the launcher with ignore_https_errors.

What PASS means: every frame before the first HEADERS frame (type, flags,
stream and payload), the HEADERS frame's flags and priority fields, and its
pseudo-header order are identical.

    python browser/tests/playwright/patches/http2-frames-parity.py
    python browser/tests/playwright/patches/http2-frames-parity.py --stock FIREFOX
"""

import importlib.util
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from helpers import launch_kwargs  # noqa: E402

TIMEOUT_S = 60
PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
HEADERS, PRIORITY, SETTINGS, WINDOW_UPDATE = 0x1, 0x2, 0x4, 0x8
# RFC 7541 Appendix A: the static table's pseudo-header entries.
PSEUDO = {1: ":authority", 2: ":method", 3: ":method", 4: ":path", 5: ":path", 6: ":scheme", 7: ":scheme"}


def make_certificates(directory: Path) -> tuple:
    """A CA and a localhost certificate it signed; returns (ca, cert, key) paths."""
    ca_key, ca, key, csr, cert = (directory / n for n in ("ca.key", "ca.pem", "key.pem", "csr.pem", "cert.pem"))
    ext = directory / "ext.cnf"
    ext.write_text("subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\n")

    def openssl(*args):
        subprocess.run(["openssl", *args], check=True, capture_output=True)

    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=http2-frames-parity CA",
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign",
            "-keyout", str(ca_key), "-out", str(ca))
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(csr))
    openssl("x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial",
            "-days", "2", "-extfile", str(ext), "-out", str(cert))
    return ca, cert, key


def _hpack_int(block: bytes, pos: int, prefix: int) -> tuple:
    value = block[pos] & ((1 << prefix) - 1)
    pos += 1
    if value < (1 << prefix) - 1:
        return value, pos
    shift = 0
    while True:
        byte = block[pos]
        pos += 1
        value += (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def pseudo_header_order(block: bytes) -> list:
    """The pseudo-headers at the start of an HPACK block, in order.

    Their names are always static-table entries, so the order is read from the
    name indexes without decoding any value.
    """
    order, pos = [], 0
    while pos < len(block):
        byte = block[pos]
        if byte & 0x80:  # indexed field
            index, pos = _hpack_int(block, pos, 7)
        elif byte & 0xE0 == 0x20:  # dynamic table size update
            _, pos = _hpack_int(block, pos, 5)
            continue
        else:  # literal, name by index or literal name, then a value
            index, pos = _hpack_int(block, pos, 6 if byte & 0x40 else 4)
            if index == 0:
                break
            length, pos = _hpack_int(block, pos, 7)
            pos += length
        if index not in PSEUDO:
            break
        order.append(PSEUDO[index])
    return order


def parse_connection(data: bytes) -> dict:
    """What an HTTP/2 fingerprint reads, from the bytes after the client preface."""
    frames, pos = [], 0
    while pos + 9 <= len(data):
        length = int.from_bytes(data[pos:pos + 3], "big")
        kind, flags = data[pos + 3], data[pos + 4]
        stream = int.from_bytes(data[pos + 5:pos + 9], "big") & 0x7FFFFFFF
        payload = data[pos + 9:pos + 9 + length]
        pos += 9 + length
        if kind == HEADERS:
            priority = None
            body = payload
            if flags & 0x8:  # PADDED
                body = body[1:len(body) - body[0]]
            if flags & 0x20:  # PRIORITY
                dependency = int.from_bytes(body[:4], "big")
                priority = {"exclusive": bool(dependency >> 31), "depends_on": dependency & 0x7FFFFFFF,
                            "weight": body[4] + 1}
                body = body[5:]
            return {
                "frames": frames,
                "headers": {"flags": flags, "stream": stream, "priority": priority,
                            "pseudo_headers": pseudo_header_order(body)},
            }
        if kind == SETTINGS and flags & 0x1:
            # The ACK of the server's SETTINGS races the request; it says nothing of the client.
            continue
        frame = {"type": kind, "flags": flags, "stream": stream}
        if kind == SETTINGS:
            frame["settings"] = [[int.from_bytes(payload[i:i + 2], "big"), int.from_bytes(payload[i + 2:i + 6], "big")]
                                 for i in range(0, len(payload), 6)]
        elif kind == WINDOW_UPDATE:
            frame["increment"] = int.from_bytes(payload[:4], "big") & 0x7FFFFFFF
        else:
            frame["payload"] = payload.hex()
        frames.append(frame)
    return {"frames": frames, "headers": None}


class FrameCatcher:
    """A TLS listener offering only h2 that keeps the first connection's opening frames."""

    def __init__(self, cert: Path, key: Path):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(cert), str(key))
        self.context.set_alpn_protocols(["h2"])
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.result = None
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self.done.is_set():
            conn, _ = self.sock.accept()
            try:
                with self.context.wrap_socket(conn, server_side=True) as tls:
                    tls.settimeout(10)
                    if tls.selected_alpn_protocol() != "h2":
                        continue
                    # The server's own (empty) SETTINGS, so the client goes on.
                    tls.sendall(bytes([0, 0, 0, SETTINGS, 0, 0, 0, 0, 0]))
                    data = b""
                    while True:
                        chunk = tls.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > len(PREFACE):
                            parsed = parse_connection(data[len(PREFACE):])
                            if parsed["headers"] is not None:
                                self.result = parsed
                                self.done.set()
                                break
            except (OSError, ssl.SSLError):
                continue

    @property
    def url(self):
        return f"https://localhost:{self.port}/"

    def wait(self, who: str) -> dict:
        if not self.done.wait(TIMEOUT_S):
            raise SystemExit(f"{who} opened no HTTP/2 connection within {TIMEOUT_S}s")
        return self.result


def getter_cost_parity():
    """getter-cost-parity's stock Firefox launcher, which this guard shares."""
    spec = importlib.util.spec_from_file_location("getter_cost_parity", HERE / "getter-cost-parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stock_connection(stock, ca: Path, cert: Path, key: Path) -> dict:
    binary = stock.stock_firefox()
    policies = binary.parent / "distribution" / "policies.json"
    if policies.exists():
        raise SystemExit(f"{policies} already exists; this guard writes its own and removes it")
    policies.parent.mkdir(exist_ok=True)
    policies.write_text(json.dumps({"policies": {"Certificates": {"Install": [str(ca)]}}}))
    try:
        catcher = FrameCatcher(cert, key)
        profile, proc = stock.run_stock(binary, catcher.url)
        try:
            return catcher.wait("stock Firefox")
        finally:
            stock.stop_stock(profile, proc)
    finally:
        policies.unlink()


def camoufox_connection(cert: Path, key: Path) -> dict:
    from camoufox.sync_api import Camoufox

    catcher = FrameCatcher(cert, key)
    with Camoufox(**launch_kwargs()) as browser:
        page = browser.new_page(ignore_https_errors=True)
        try:
            page.goto(catcher.url, timeout=TIMEOUT_S * 1000)
        except Exception:  # noqa: BLE001 -- the server never answers; only the frames matter
            pass
        return catcher.wait("Camoufox")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="http2-frames-parity-") as tmp:
        ca, cert, key = make_certificates(Path(tmp))
        stock = stock_connection(getter_cost_parity(), ca, cert, key)
        camoufox = camoufox_connection(cert, key)
    print("stock:   ", json.dumps(stock, sort_keys=True))
    print("camoufox:", json.dumps(camoufox, sort_keys=True))
    differ = sorted(k for k in ("frames", "headers") if stock[k] != camoufox[k])
    if differ:
        print(f"FAIL: the HTTP/2 connection differs from stock Firefox in {', '.join(differ)}")
        return 1
    print("PASS: the HTTP/2 connection opens as stock Firefox's does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
