"""A minimal SIP user agent client for the AI voice service (0092): exactly what one outgoing call to
the PBX needs, nothing more (RFC 3261 over UDP).

* INVITE with an SDP offer (PCMU + telephone-event), retransmitted until a provisional or final
  answer; a 401/407 digest challenge (MD5, optional qop=auth) is ACKed and the INVITE re-sent once
  with credentials;
* 1xx: ringing; 2xx: ACK (new branch, to the answer's Contact), the remote RTP address from its SDP;
  a final >= 300: ACKed, the call failed with that status;
* in-dialog BYE from the PBX: 200 OK; BYE from us, retransmitted until answered; CANCEL while ringing;
* a re-INVITE or UPDATE from the PBX: 200 OK with the same SDP; OPTIONS: 200 OK; keep-alive OPTIONS
  out of dialog so the network keeps the UDP path open (the PBX cannot open one to us).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field

CRLF = "\r\n"


def rand(n: int = 10) -> str:
    return secrets.token_hex(n)


@dataclass
class Message:
    first: str
    headers: list = field(default_factory=list)       # [(name, value)] in order
    body: str = ""

    @property
    def is_response(self) -> bool:
        return self.first.startswith("SIP/2.0 ")

    @property
    def status(self) -> int | None:
        if not self.is_response:
            return None
        try:
            return int(self.first.split(" ", 2)[1])
        except (IndexError, ValueError):
            return None

    @property
    def method(self) -> str | None:
        return None if self.is_response else self.first.split(" ", 1)[0].upper()

    def get(self, name: str) -> str | None:
        n = name.lower()
        for k, v in self.headers:
            if k.lower() == n:
                return v
        return None

    def get_all(self, name: str) -> list:
        n = name.lower()
        return [v for k, v in self.headers if k.lower() == n]

    @property
    def cseq(self) -> tuple[int, str]:
        raw = (self.get("CSeq") or "0 X").split()
        return int(raw[0]), raw[1].upper() if len(raw) > 1 else ""

    def to_bytes(self) -> bytes:
        body = self.body.encode()
        heads = [(k, v) for k, v in self.headers if k.lower() != "content-length"] + [("Content-Length", str(len(body)))]
        return (self.first + CRLF + "".join(f"{k}: {v}{CRLF}" for k, v in heads) + CRLF).encode() + body


COMPACT = {"v": "Via", "f": "From", "t": "To", "i": "Call-ID", "m": "Contact", "l": "Content-Length",
           "c": "Content-Type"}


def parse(data: bytes) -> Message | None:
    try:
        text = data.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines or not lines[0]:
        return None
    headers = []
    for ln in lines[1:]:
        if ln[:1] in (" ", "\t") and headers:                     # folded header
            headers[-1] = (headers[-1][0], headers[-1][1] + " " + ln.strip())
            continue
        name, sep, value = ln.partition(":")
        if not sep:
            continue
        name = name.strip()
        headers.append((COMPACT.get(name, name), value.strip()))
    return Message(lines[0], headers, body)


def tag_of(value: str | None) -> str | None:
    m = re.search(r";\s*tag=([^;>\s]+)", value or "")
    return m.group(1) if m else None


def uri_of(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"<([^>]+)>", value)
    return (m.group(1) if m else value.split(";")[0]).strip()


def sdp_offer(ip: str, port: int, session_id: int) -> str:
    return CRLF.join([
        "v=0", f"o=cawp-ai {session_id} {session_id} IN IP4 {ip}", "s=cawp-ai", f"c=IN IP4 {ip}", "t=0 0",
        f"m=audio {port} RTP/AVP 0 101", "a=rtpmap:0 PCMU/8000", "a=rtpmap:101 telephone-event/8000",
        "a=fmtp:101 0-16", "a=ptime:20", "a=sendrecv", ""])


def sdp_remote(body: str) -> tuple[str, int, list] | None:
    """(ip, port, payload types) of the first audio stream, or None."""
    ip = port = None
    pts = []
    for ln in (body or "").splitlines():
        ln = ln.strip()
        if ln.startswith("c=IN IP4 ") and ip is None:
            ip = ln.split()[2]
        elif ln.startswith("m=audio "):
            parts = ln.split()
            try:
                port = int(parts[1])
            except (IndexError, ValueError):
                return None
            pts = parts[3:]
        elif ln.startswith("c=IN IP4 ") and port is not None:
            ip = ln.split()[2]                                    # media-level c= wins
    if ip is None or not port:
        return None
    return ip, port, pts


def parse_challenge(value: str) -> dict:
    out = {}
    for k, raw, quoted in re.findall(r'(\w+)=("([^"]*)"|[^,\s]+)', value or ""):
        out[k.lower()] = quoted if raw.startswith('"') else raw
    return out


def digest(*, method: str, uri: str, user: str, password: str, challenge: dict) -> str:
    realm, nonce = challenge.get("realm", ""), challenge.get("nonce", "")
    ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    qop = challenge.get("qop")
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', "algorithm=MD5"]
    if qop and "auth" in qop.split(","):
        nc, cnonce = "00000001", rand(8)
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
        parts += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    parts.append(f'response="{resp}"')
    if challenge.get("opaque"):
        parts.append(f'opaque="{challenge["opaque"]}"')
    return "Digest " + ", ".join(parts)


def response_to(req: Message, status: int, reason: str, *, to_tag: str | None = None, extra=(), body: str = "") -> Message:
    """A response copying Via/From/To/Call-ID/CSeq from the request (RFC 3261 8.2.6)."""
    heads = [(k, v) for k, v in req.headers if k.lower() == "via"]
    to = req.get("To") or ""
    if to_tag and not tag_of(to):
        to += f";tag={to_tag}"
    heads += [("From", req.get("From") or ""), ("To", to), ("Call-ID", req.get("Call-ID") or ""),
              ("CSeq", req.get("CSeq") or "")] + list(extra)
    return Message(f"SIP/2.0 {status} {reason}", heads, body)
