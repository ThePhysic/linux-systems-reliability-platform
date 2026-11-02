"""Fixed loopback HTTP transport with one deadline and no background workers."""
from __future__ import annotations

from http.client import HTTPException, HTTPResponse
from collections.abc import Callable
import io
import socket
import time


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("Functional probe deadline exceeded")
    return seconds


class _DeadlineReader(io.RawIOBase):
    """Refresh the *remaining* budget before every underlying recv.

    BufferedReader/HTTPResponse may issue many recv calls for a single header,
    chunk or read(). A timeout applied only to the outer call is insufficient.
    The enclosing socket context owns and closes the actual connection.
    """

    def __init__(self, connection: socket.socket, deadline: float):
        super().__init__()
        self.connection = connection
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        self.connection.settimeout(remaining(self.deadline))
        return self.connection.recv_into(buffer)


class _ResponseSocket:
    """Provide HTTPResponse's documented socket file interface."""

    def __init__(self, connection: socket.socket, deadline: float):
        self.connection = connection
        self.deadline = deadline

    def makefile(self, mode):
        if mode != "rb":
            raise ValueError("Probe response is read-only binary HTTP")
        return io.BufferedReader(_DeadlineReader(self.connection, self.deadline))


def get_work(port: int, deadline: float, limit: int = 8192,
             on_status: Callable[[int], None] | None = None) -> tuple[int, bytes]:
    """GET only the fixed /work path on numeric IPv4 loopback.

    No DNS, environment proxy lookup, redirects, retries, threads or subprocesses.
    Every blocking network operation consumes the same absolute deadline.
    HTTPResponse retains standard bounded-header and HTTP chunk parsing.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining(deadline))
        connection.connect(("127.0.0.1", port))
        connection.settimeout(remaining(deadline))
        connection.sendall((f"GET /work HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                            "Accept: application/json\r\nConnection: close\r\n\r\n").encode("ascii"))
        with HTTPResponse(_ResponseSocket(connection, deadline), method="GET") as response:
            response.begin()
            if on_status is not None:
                on_status(response.status)
            remaining(deadline)
            if response.status != 200:
                return response.status, b""
            payload = response.read(limit + 1)
            remaining(deadline)
            if len(payload) > limit:
                raise ValueError("Oversized service response")
            if response.length is not None and response.length > 0:
                raise HTTPException("Incomplete service response")
            return response.status, payload
