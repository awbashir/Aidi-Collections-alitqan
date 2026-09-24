"""The HTTP transport used by the provider implementations.

Production uses `UrllibTransport` (standard library, no extra dependency). Tests inject their own
object with the same `send(request) -> HttpResponse` method (Flask config `AI_PROVIDER_TRANSPORT`
or the provider constructor), which records the exact request we send and replays canned
answers. Nothing in production code simulates a provider.

Errors: an HTTP status (2xx-5xx) is a normal `HttpResponse`; only a failure to get any answer
raises `TransportError` ("timeout" or "connection"). Neither carries request headers or bodies,
so the key can never end up in an exception message or a log line.
"""
from __future__ import annotations

import dataclasses
import socket
import urllib.error
import urllib.request


@dataclasses.dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict = dataclasses.field(repr=False)
    body: bytes = dataclasses.field(repr=False)
    timeout: float = 60.0

    def __repr__(self):  # never print headers (Authorization) or the body (content)
        return f"HttpRequest({self.method} {self.url}, {len(self.body)} bytes, timeout={self.timeout})"


@dataclasses.dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict                       # lower-case names
    body: bytes = dataclasses.field(repr=False)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class TransportError(Exception):
    """No HTTP answer. `kind` is "timeout" or "connection"."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the Authorization header elsewhere: treat it as an answer."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


class UrllibTransport:
    max_response_bytes = 20 * 1024 * 1024

    def __init__(self):
        self._opener = urllib.request.build_opener(_NoRedirect())

    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(request.url, data=request.body, method=request.method,
                                     headers=dict(request.headers))
        try:
            with self._opener.open(req, timeout=request.timeout) as resp:
                return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()},
                                    resp.read(self.max_response_bytes + 1)[: self.max_response_bytes + 1])
        except urllib.error.HTTPError as e:
            try:
                body = e.read(64 * 1024)
            except Exception:  # noqa: BLE001
                body = b""
            return HttpResponse(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body)
        except (socket.timeout, TimeoutError):
            raise TransportError("timeout") from None
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                raise TransportError("timeout") from None
            raise TransportError("connection") from None
        except (ConnectionError, OSError):
            raise TransportError("connection") from None
