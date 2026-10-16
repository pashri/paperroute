"""A local HTTP server that stands in for the TfNSW timetable API."""

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

REDIRECT: Final[str] = '/redirect'
TARGET: Final[str] = '/target'
POLL_S: Final[float] = 0.05


@dataclass
class Served:
    """One path's body and validators."""

    body: bytes
    etag: str | None = '"1"'
    last_modified: str | None = 'Thu, 15 Oct 2026 10:00:00 GMT'


@dataclass
class FeedState:
    """What the server serves and what it has seen."""

    files: dict[str, Served] = field(default_factory=dict)
    hits: Counter[str] = field(default_factory=Counter)
    fail_midway: set[str] = field(default_factory=set)
    status: HTTPStatus | None = None
    auth_seen: dict[str, str | None] = field(default_factory=dict)


class FeedServer(ThreadingHTTPServer):
    """HTTP server carrying a FeedState."""

    state: FeedState

    @property
    def base_url(self) -> str:
        """
        URL of the server root.

        Returns
        -------
        str
            'http://127.0.0.1:<port>/'.
        """
        return f'http://127.0.0.1:{self.server_address[1]}/'


class FeedHandler(BaseHTTPRequestHandler):
    """Serve FeedState files, redirects and failures."""

    server: FeedServer

    def log_message(  # pylint: disable=arguments-differ
        self,
        format: str,  # pylint: disable=redefined-builtin
        *args: object,
    ) -> None:
        """
        Keep request lines out of the test output.

        Parameters
        ----------
        format : str
            Unused.
        *args : object
            Unused.
        """

    def do_HEAD(self) -> None:  # pylint: disable=invalid-name
        """Answer with headers only."""
        self.answer(send_body=False)

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Answer with headers and body."""
        self.answer(send_body=True)

    def answer(self, *, send_body: bool) -> None:
        """
        Count the request, then reply for its path.

        Parameters
        ----------
        send_body : bool
            Whether to write the body.
        """
        state = self.server.state
        state.hits[self.command] += 1
        state.auth_seen[self.path] = self.headers.get('Authorization')
        if state.status is not None:
            self.send_error(state.status)
        elif self.path == REDIRECT:
            self.redirect()
        else:
            self.serve(served=state.files[self.path], send_body=send_body)

    def redirect(self) -> None:
        """Send a redirect to the same port on a different host name."""
        port = self.server.server_address[1]
        self.send_response(HTTPStatus.FOUND)
        self.send_header('Location', f'http://localhost:{port}{TARGET}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def serve(self, *, served: Served, send_body: bool) -> None:
        """
        Send one file, or half of it and then hang up.

        Parameters
        ----------
        served : Served
            File to send.
        send_body : bool
            Whether to write the body.
        """
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Length', str(len(served.body)))
        if served.etag is not None:
            self.send_header('ETag', served.etag)
        if served.last_modified is not None:
            self.send_header('Last-Modified', served.last_modified)
        self.end_headers()
        if not send_body:
            return
        if self.path in self.server.state.fail_midway:
            self.wfile.write(served.body[:len(served.body) // 2])
            self.close_connection = True
            return
        self.wfile.write(served.body)


@contextmanager
def feed_server() -> Iterator[FeedServer]:
    """
    Run a FeedServer on 127.0.0.1 in a thread.

    Yields
    ------
    FeedServer
        Running server; shut down and closed on exit.
    """
    server = FeedServer(('127.0.0.1', 0), FeedHandler)
    server.state = FeedState()
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={'poll_interval': POLL_S},
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
