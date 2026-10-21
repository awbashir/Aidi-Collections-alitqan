"""Client of the PBX extension provisioning API (Issabel, port 8092; customer request 2026-10-20).

The PBX side is CAWP-API/calling/issabel/ext-provision/ (Apache vhost `cawp-provision.conf`, the
root script `cawp-ext-provision` behind a narrow sudoers rule). It admits only the app server's
address with the header X-CAWP-Token. Like the recordings reader (services/calling_cdr._read_http):
stdlib urllib, no proxy, no redirects (the token never follows one), a bounded answer.

    PBX_PROVISION_URL      http://10.0.40.7:8092/       (.env.docker; empty = provisioning disabled)
    PBX_PROVISION_TOKEN    the shared secret             (.env.docker; never logged)
    PBX_PROVISION_TIMEOUT  seconds per call (default 60: a create runs retrieve_conf + core reload)

Routes (JSON): GET v1/extensions, GET v1/extensions/<ext>, POST v1/extensions {extension, name},
POST v1/extensions/<ext>/rename {name}, DELETE v1/extensions/<ext>, POST v1/extensions/<ext>/secret
(chan_sip PBX only, Alitqan 2026-10-21: a new random SIP password for an app-created desk-phone
extension, in the answer once; never logged here: no answer body is ever logged).

* `ProvisionUnavailable`: the PBX could not be asked (not configured, unreachable, timeout, a 5xx
  without an answer). The change may or may not have happened only for a timeout on a write; the
  caller treats every unavailable write as "pending" and re-checks with the list before retrying.
* `ProvisionRefused(status, code, message)`: the PBX answered and refused (422 invalid, 404
  not_found, 409 exists / not_managed / in_call / in_ring_group, 503 busy, 500 create_failed...).

Item fields (per extension): extension, name, tech, managed (created by the app), endpoint
(true/false/None = Asterisk unreachable: unknown), state, registered, in_call, channels.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("cawp.calling.pbx_provision")

MAX_ANSWER = 2 * 1024 * 1024


class ProvisionUnavailable(Exception):
    """The provisioning API could not be asked (or gave no usable answer)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ProvisionRefused(Exception):
    """The provisioning API answered with a refusal."""

    def __init__(self, status: int, code: str, message: str, body: dict | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.body = status, code, message, body or {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002 - never follow
        return None


def _base() -> str:
    return (os.getenv("PBX_PROVISION_URL") or "").strip()


def configured() -> bool:
    return bool(_base() and os.getenv("PBX_PROVISION_TOKEN"))


def _timeout() -> float:
    try:
        return max(1.0, float(os.getenv("PBX_PROVISION_TIMEOUT") or 60))
    except ValueError:
        return 60.0


def _call(method: str, path: str, body: dict | None = None) -> dict:
    if not configured():
        raise ProvisionUnavailable("not_configured")
    url = _base().rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"X-CAWP-Token": os.getenv("PBX_PROVISION_TOKEN") or "", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=_timeout()) as resp:
            raw = resp.read(MAX_ANSWER + 1)
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(MAX_ANSWER + 1)
        except Exception:  # noqa: BLE001
            raw = b""
    except Exception as e:  # noqa: BLE001 - unreachable, refused, timeout, reset
        log.warning("PBX provisioning %s %s failed: %s", method, path, type(e).__name__)
        raise ProvisionUnavailable("unreachable") from e
    if len(raw) > MAX_ANSWER:
        raise ProvisionUnavailable("answer_too_large")
    try:
        answer = json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        log.warning("PBX provisioning %s %s: HTTP %s without a JSON answer", method, path, status)
        if status == 403:
            raise ProvisionUnavailable("forbidden")           # wrong token / address: a setup problem
        raise ProvisionUnavailable("bad_answer")
    if 200 <= status < 300 and answer.get("ok") is True:
        return answer
    code = str(answer.get("code") or "refused")[:64]
    message = str(answer.get("message") or "")[:1000]
    log.info("PBX provisioning %s %s refused: %s %s", method, path, status, code)
    raise ProvisionRefused(status, code, message, answer)


def _ext(extension: str) -> str:
    return urllib.parse.quote(extension, safe="")


def list_extensions() -> dict:
    """{asterisk: bool, range: [lo, hi], include_installed, items: [...]}"""
    return _call("GET", "v1/extensions")


def status(extension: str) -> dict:
    return _call("GET", f"v1/extensions/{_ext(extension)}")["item"]


def create(extension: str, name: str) -> dict:
    return _call("POST", "v1/extensions", {"extension": extension, "name": name})


def rename(extension: str, name: str) -> dict:
    return _call("POST", f"v1/extensions/{_ext(extension)}/rename", {"name": name})


def delete(extension: str) -> dict:
    return _call("DELETE", f"v1/extensions/{_ext(extension)}")


def new_secret(extension: str) -> dict:
    """{extension, sip_username, sip_secret}: the PBX sets a new random SIP password (app-created
    extensions only) and returns it once."""
    return _call("POST", f"v1/extensions/{_ext(extension)}/secret", {})


def webrtc(extension: str, enabled: bool) -> dict:
    """chan_sip PBX (0083): convert an extension to a WebRTC (browser) extension, or restore the
    desk-phone settings it had before (its original password included). `on` answers the new
    WebRTC password once (`sip_secret`); `off` answers none."""
    return _call("POST", f"v1/extensions/{_ext(extension)}/webrtc", {"enabled": bool(enabled)})


def sip_server() -> str | None:
    """The address desk phones register to: PBX_SIP_SERVER, else the provisioning URL's host."""
    explicit = (os.getenv("PBX_SIP_SERVER") or "").strip()
    if explicit:
        return explicit
    host = urllib.parse.urlsplit(_base()).hostname if _base() else None
    return host or None
