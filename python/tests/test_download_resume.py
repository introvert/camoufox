"""`webdl()` resumes a download whose connection breaks partway through.

A browser archive is over a gigabyte, and GitHub's asset hosts sometimes close
the connection mid-body ("Connection broken: IncompleteRead"). The download
continues from the bytes it already has with a Range request, and only from
exactly those bytes, so nothing is written to the buffer twice. A read cut
off mid-block yields nothing of that block, so a resume can start before the
byte where the server stopped sending.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
import requests

from camoufox import pkgman

PAYLOAD = bytes(range(256)) * 4096
BREAK_AT = 300_000


def serve(answer):
    """A local server whose GET handler is `answer(handler, range_start)`.
    Records the start of every Range request it receives."""
    ranges = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            header = self.headers.get('Range')
            start = int(header.removeprefix('bytes=').rstrip('-')) if header else None
            ranges.append(start)
            answer(self, start)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}/camoufox.zip', ranges


def send(handler, status, start, end, headers=()):
    """Answer with PAYLOAD[start:] announced, but only PAYLOAD[start:end] sent."""
    handler.send_response(status)
    handler.send_header('Content-Length', str(len(PAYLOAD) - start))
    for name, value in headers:
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(PAYLOAD[start:end])


def partial(start):
    return [('Content-Range', f'bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}')]


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(pkgman, 'RESUME_DELAYS', (0, 0, 0))


@pytest.fixture
def server():
    servers = []

    def start(answer):
        server, url, ranges = serve(answer)
        servers.append(server)
        return url, ranges

    yield start
    for server in servers:
        server.shutdown()


def download(url):
    return pkgman.webdl(url, buffer=BytesIO(), progress_callback=lambda done, total: None)


def test_a_broken_connection_resumes_where_it_stopped(server):
    def answer(handler, start):
        if start is None:
            send(handler, 200, 0, BREAK_AT)
        else:
            send(handler, 206, start, len(PAYLOAD), partial(start))

    url, ranges = server(answer)
    assert download(url).read() == PAYLOAD
    assert len(ranges) == 2 and 0 < ranges[1] <= BREAK_AT


def test_each_resume_continues_from_the_bytes_received_so_far(server):
    """A connection that keeps breaking still completes while each one adds
    bytes: only consecutive breaks that receive nothing end the download."""
    step = len(PAYLOAD) // 6

    def answer(handler, start):
        begin = start or 0
        send(handler, 206 if start else 200, begin, begin + step, partial(begin) if start else ())

    url, ranges = server(answer)
    assert download(url).read() == PAYLOAD
    assert len(ranges) > 6 and ranges[1:] == sorted(set(ranges[1:]))


def test_a_server_that_ignores_the_range_fails_loudly(server):
    """A 200 to a Range request is the whole file again: appending it would
    corrupt the archive, so the download stops instead."""

    def answer(handler, start):
        send(handler, 200, 0, BREAK_AT if start is None else len(PAYLOAD))

    url, _ = server(answer)
    with pytest.raises(requests.HTTPError, match='Could not resume'):
        download(url)


def test_breaks_that_receive_nothing_end_the_download(server):
    def answer(handler, start):
        if start is None:
            send(handler, 200, 0, BREAK_AT)
        else:
            send(handler, 206, start, start, partial(start))

    url, ranges = server(answer)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        download(url)
    assert len(ranges) == 1 + len(pkgman.RESUME_DELAYS) and len(set(ranges[1:])) == 1
