"""Softphone (Phase 5 P3) settings, read from the environment at call time.

Nothing here is secret apart from SIP_TURN_SECRET (the coturn REST shared secret), which never
leaves the server: browsers only get short-lived TURN credentials derived from it.

    SIP_WSS_URL                  wss://<pbx name>:8089/ws  (required; wss only)
    SIP_DOMAIN                   SIP URI host (default: the host of SIP_WSS_URL)
    SIP_REALM                    digest realm written with each credential (default: cawp)
    SIP_CREDENTIAL_TTL_SECONDS   credential lifetime, 60..3600 (default 600)
    SIP_REGISTER_EXPIRES         REGISTER expiry the softphone asks for, 60..TTL (default min(300, TTL))
    SIP_ISSUE_LIMIT              credentials one user may be issued per 10 minutes (default 30)
    SIP_STUN_URLS                comma list of stun: URLs (optional)
    SIP_TURN_URLS                comma list of turn:/turns: URLs (optional; needs SIP_TURN_SECRET)
    SIP_TURN_SECRET              coturn `static-auth-secret` (use-auth-secret REST scheme)
    SIP_INTERNAL_DIAL_PATTERN    what the dialpad and transfer accept (default ^[0-9]{3,6}$: internal
                                 extensions only; an E.164 or national number never matches)
    SIP_ECHO_TEST_EXTENSION      an echo-test extension for the diagnostics panel (optional)
    SIP_CREDENTIAL_MODE          realtime (default: per-issue credentials checked by the PBX through the
                                 PJSIP realtime view, 0056) | static (chan_sip PBX, e.g. Issabel 4 /
                                 Asterisk 11: the extension's own WebRTC password, kept encrypted by the
                                 app, 0083; needs SIP_STATIC_SECRET_KEY and the PBX provisioning API)
    SIP_RTCP_MUX_POLICY          require (default) | negotiate: the browser's RTCPeerConnection rtcpMuxPolicy.
                                 Asterisk 11 has no rtcp-mux; Chrome's default `require` then never
                                 connects the media (measured on the Alitqan Issabel, Chrome 155), while
                                 `negotiate` gives two-way Opus over DTLS 1.2 (the softphone then also uses
                                 bundlePolicy `balanced`: `max-bundle` makes Chrome reject Asterisk 11's answer)
    SIP_EXTERNAL_DIAL_PATTERN    opt-in: outside numbers the dialpad may call directly through the PBX's
                                 outbound routes (unset = click-to-call only, the default). Such calls
                                 are not case-linked attempts: no calling-window check, no wrap-up;
                                 the PBX still records them. A pattern that does not compile is ignored.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.api_response import ApiError

NOT_CONFIGURED = "softphone_not_configured"
_REALM_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_ICE_RE = re.compile(r"^(stun|turn|turns):[A-Za-z0-9.\-\[\]:]+(\?transport=(udp|tcp))?$")
_EXT_RE = re.compile(r"^[0-9]{2,6}$")


@dataclass(frozen=True)
class SipSettings:
    wss_url: str
    domain: str
    realm: str
    ttl_seconds: int
    register_expires: int
    issue_limit: int
    stun_urls: tuple[str, ...]
    turn_urls: tuple[str, ...]
    turn_secret: str | None
    internal_dial_pattern: str
    echo_test_extension: str | None
    external_dial_pattern: str | None = None
    credential_mode: str = "realtime"
    rtcp_mux_policy: str = "require"


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError:
        value = default
    return max(lo, min(hi, value))


def _urls(name: str) -> tuple[str, ...]:
    out = []
    for part in (os.getenv(name) or "").split(","):
        part = part.strip()
        if part and _ICE_RE.match(part):
            out.append(part)
    return tuple(out)


def load() -> SipSettings:
    """The current settings, or 503 `softphone_not_configured` when the WSS URL is missing/invalid."""
    wss = (os.getenv("SIP_WSS_URL") or "").strip()
    parts = urlsplit(wss)
    if parts.scheme != "wss" or not parts.hostname or not _HOST_RE.match(parts.hostname):
        raise ApiError(503, NOT_CONFIGURED, "the softphone is not configured on this server (SIP_WSS_URL)",
                       {"setting": "SIP_WSS_URL"})
    domain = (os.getenv("SIP_DOMAIN") or parts.hostname).strip()
    if not _HOST_RE.match(domain):
        raise ApiError(503, NOT_CONFIGURED, "SIP_DOMAIN is invalid", {"setting": "SIP_DOMAIN"})
    realm = (os.getenv("SIP_REALM") or "cawp").strip()
    if not _REALM_RE.match(realm):
        raise ApiError(503, NOT_CONFIGURED, "SIP_REALM is invalid", {"setting": "SIP_REALM"})
    ttl = _int("SIP_CREDENTIAL_TTL_SECONDS", 600, 60, 3600)
    register_expires = _int("SIP_REGISTER_EXPIRES", min(300, ttl), 60, ttl)
    pattern = os.getenv("SIP_INTERNAL_DIAL_PATTERN") or r"^[0-9]{3,6}$"
    try:
        compiled = re.compile(pattern)
    except re.error:
        compiled = re.compile(r"^[0-9]{3,6}$")
        pattern = compiled.pattern
    # A pattern that would accept an external number is refused (no free dialing, PROJECT-NOTES §3.2).
    for external in ("0501234567", "966501234567", "+966501234567", "00966501234567"):
        if compiled.search(external):
            pattern = r"^[0-9]{3,6}$"
            break
    echo = (os.getenv("SIP_ECHO_TEST_EXTENSION") or "").strip() or None
    if echo is not None and not _EXT_RE.match(echo):
        echo = None
    turn_secret = os.getenv("SIP_TURN_SECRET") or None
    external_pattern = (os.getenv("SIP_EXTERNAL_DIAL_PATTERN") or "").strip() or None
    if external_pattern is not None:
        try:
            re.compile(external_pattern)
        except re.error:
            external_pattern = None
    mode = (os.getenv("SIP_CREDENTIAL_MODE") or "realtime").strip().lower()
    if mode not in ("realtime", "static"):
        raise ApiError(503, NOT_CONFIGURED, "SIP_CREDENTIAL_MODE must be realtime or static",
                       {"setting": "SIP_CREDENTIAL_MODE"})
    mux = (os.getenv("SIP_RTCP_MUX_POLICY") or "require").strip().lower()
    if mux not in ("require", "negotiate"):
        mux = "require"
    return SipSettings(
        credential_mode=mode, rtcp_mux_policy=mux, wss_url=wss, domain=domain, realm=realm, ttl_seconds=ttl, register_expires=register_expires,
        issue_limit=_int("SIP_ISSUE_LIMIT", 30, 1, 1000), stun_urls=_urls("SIP_STUN_URLS"),
        turn_urls=_urls("SIP_TURN_URLS") if turn_secret else (), turn_secret=turn_secret,
        internal_dial_pattern=pattern, echo_test_extension=echo, external_dial_pattern=external_pattern)
