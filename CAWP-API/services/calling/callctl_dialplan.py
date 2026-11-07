"""Call-control dialplan and ConfBridge profiles (customer request 2026-10-20), pure.

    python -m services.calling.callctl_dialplan [--confbridge] < policy.json > cawp-callctl.conf

The output is what the PBX loads: `/etc/asterisk/cawp-callctl.conf` (`#include`d from
extensions_custom.conf) and, with --confbridge, the profiles for `confbridge_custom.conf`.
It contacts no PBX. The calling service (services/calling/call_control.py) is the only thing that
sends a channel into these contexts: by AMI Redirect (identity preserved: no masquerade) or by an
Originate to an app user's own extension. No endpoint dials into them (no include anywhere).

Policy (JSON object; every key optional):

    post_call_context  where the customer goes when the control is over and the call is still up
                       (the post-call survey, "cawp-survey"); absent = Hangup
    record             true (default): MixMonitor the customer leg from the moment it leaves the
                       agent (the agent's own MixMonitor of the click-to-call dialplan stops with
                       the agent's channel), YYYY/MM/DD/<customer uniqueid>-cc.wav, CDR recordingfile
    max_call_seconds   30..14400 (default 3600): TIMEOUT(absolute) of the customer leg here
    ring_seconds       5..60 (default 30): a blind transfer's ring time on the target
    recall_seconds     0..60 (default 20): ring the transferring agent back once when the target
                       did not answer (0 = no recall)
    driver             "PJSIP" (default) | "SIP": the agents' channel driver, the same as the
                       calling service's CALLING_DRIVER. The PJSIP output is unchanged.

chan_sip (driver "SIP"; Asterisk 11, e.g. the Alitqan Issabel), the only differences:
    Dial(SIP/<ext>,...); the header through SIPAddHeader(X-CAWP-Control: <tag>) in the pre-dial
    handler; the spy target must be SIP/<ext>-<8 hex>; ChanSpy options without `u` (newer than
    Asterisk 11; the exact channel name is a prefix of that channel only); the consultation hold
    loop checks the holder's channel NAME with CHANNELS() (Asterisk 11 has no CHANNEL_EXISTS;
    call_control.py sets CAWP_CTL_HOLDER to the agent's channel name with this driver).

Contexts (variables come from the AMI Setvar/Originate of the calling service and are
re-validated here; a bad value never reaches Dial/ConfBridge/ChanSpy):

    cawp-callctl-xfer,<ext>,1   the PEER after a blind transfer: Dial the target (X-CAWP-Control
                                header through the pre-dial handler), recall the agent once
    cawp-callctl-done,s,1       the AGENT after a transfer: UserEvent transferred, Hangup
    cawp-callctl-hold,s,1       the PEER during a consultation: music on hold until the agent
                                returns it; when the agent's channel is gone, it joins the consult
                                room if the target is in it (the transfer completes), else leaves
    cawp-callctl-room,<role>,1  ConfBridge room ${CAWP_CTL_ROOM}: agent | customer | invitee
    cawp-callctl-spy,<mode>,1   ChanSpy(<agent channel>, q d E u [w|B]); DTMF 4/5/6 switch the
                                mode (the calling service sends them with PlayDTMF Receive)
    cawp-callctl-post,s,1       the customer leg is released: post-call context or Hangup

ConfBridge profiles: one bridge profile `cawp_room`; users `cawp_customer` (marked) and
`cawp_member` (agents and invitees, end_marked): when the customer hangs up, the internal users
are dropped too; when an agent leaves, the customer stays with whoever is left. Quiet (no join
sounds, no "you are the only person"), no DTMF menu.

Every CollectionsCall UserEvent here is sent only when the channel carries COLLECTIONS_ATTEMPT
(the click-to-call legs, whose Linkedid is the app-issued `cawp-<attempt>` channel, so the
evidence consumer attributes them): transfer_blind, transferred, customer_on_hold, conference.
The projection (services/calling/projection.py) uses them to end the collector's attempt when
the agent left and the customer was handed over.
"""
from __future__ import annotations

import json
import re
import sys

GOSUB_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
KEYS = {"post_call_context", "record", "max_call_seconds", "ring_seconds", "recall_seconds", "driver"}
ROOM_RE = r"^cawp-[0-9a-f]{32}$"
CTL_RE = r"^[0-9a-f]{32}$"
EXT_RE = r"^[0-9]{2,6}$"
SPY_TARGET_RE = r"^PJSIP/[0-9]{2,6}-[0-9a-f]{8}$"
SIP_CHANNEL_RE = r"^SIP/[0-9]{2,6}-[0-9a-f]{8}$"      # chan_sip agent channel (spy target, consult holder)


class CallControlPolicyError(ValueError):
    pass


def _int(policy, key, default, lo, hi):
    v = policy.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise CallControlPolicyError(f"{key} must be an integer {lo}..{hi}")
    return v


def validate_policy(policy) -> dict:
    if not isinstance(policy, dict) or set(policy) - KEYS:
        raise CallControlPolicyError("policy must be an object with only: " + ", ".join(sorted(KEYS)))
    post = policy.get("post_call_context")
    if post is not None and (not isinstance(post, str) or not GOSUB_RE.match(post)):
        raise CallControlPolicyError("post_call_context must match [a-z][a-z0-9_-]{0,63}")
    record = policy.get("record", True)
    if not isinstance(record, bool):
        raise CallControlPolicyError("record must be true or false")
    driver = policy.get("driver", "PJSIP")
    if driver not in ("PJSIP", "SIP"):
        raise CallControlPolicyError("driver must be PJSIP or SIP")
    return {"post_call_context": post, "record": record, "driver": driver,
            "max_call_seconds": _int(policy, "max_call_seconds", 3600, 30, 14400),
            "ring_seconds": _int(policy, "ring_seconds", 30, 5, 60),
            "recall_seconds": _int(policy, "recall_seconds", 20, 0, 60)}


def _evt(state: str, extra: str = "") -> list[str]:
    """A CollectionsCall UserEvent only on a click-to-call leg (two priorities)."""
    return [' same => n,GotoIf($["${COLLECTIONS_ATTEMPT}" = ""]?+2)',
            f" same => n,UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}},State: {state}{extra})"]


def _record() -> list[str]:
    """MixMonitor the customer leg once (a second entry into a context does not restart it)."""
    return [' same => n,GotoIf($["${CAWP_CTL_REC}" != ""]?+4)',
            " same => n,Set(CAWP_CTL_REC=${STRFTIME(${EPOCH},Asia/Riyadh,%Y/%m/%d)}/${UNIQUEID}-cc.wav)",
            " same => n,Set(CDR(recordingfile)=${CAWP_CTL_REC})",
            " same => n,MixMonitor(${CAWP_CTL_REC},b)"]


def render(policy=None) -> str:
    p = validate_policy(policy or {})
    sip = p["driver"] == "SIP"
    tech = p["driver"]
    post = f"Goto({p['post_call_context']},s,1)" if p["post_call_context"] else "Hangup()"
    rec = _record() if p["record"] else []
    limit = f" same => n,Set(TIMEOUT(absolute)={p['max_call_seconds']})"
    lines = [
        "; Generated by `python -m services.calling.callctl_dialplan` (CAWP call control, 2026-10-20).",
        "; Review before installation. Regenerate instead of editing by hand.",
        f"; policy: post_call_context={p['post_call_context'] or '-'} record={'yes' if p['record'] else 'no'}"
        f" max_call_seconds={p['max_call_seconds']} ring_seconds={p['ring_seconds']} recall_seconds={p['recall_seconds']}"
        + (" driver=SIP (chan_sip)" if sip else ""),
        "; Only the calling service (AMI Redirect / Originate) sends channels here. Never include these",
        "; contexts in an endpoint's dialing context. Endpoint transfers (SIP REFER) stay disabled.",
        "",
        "; Blind transfer: the PEER (customer) dials the target; ${EXTEN} = the target extension.",
        "[cawp-callctl-xfer]",
        f'exten => _X.,1,GotoIf($[${{REGEX("{EXT_RE}" ${{EXTEN}})}} != 1]?cawp-callctl-post,s,1)',
        f' same => n,GotoIf($[${{REGEX("{CTL_RE}" ${{CAWP_CTL_ID}})}} != 1]?cawp-callctl-post,s,1)',
        *_evt("transfer_blind", ",Target: ${EXTEN},Control: ${CAWP_CTL_ID}"),
        limit,
        *rec,
        f" same => n,Dial({tech}/${{EXTEN}},{p['ring_seconds']},ib(cawp-callctl-tag^s^1(${{CAWP_CTL_ID}})))",
        ' same => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?cawp-callctl-post,s,1)',
    ]
    if p["recall_seconds"]:
        lines += [
            "; the target did not answer: ring the transferring agent back once",
            f' same => n,GotoIf($[${{REGEX("{EXT_RE}" ${{CAWP_CTL_FROM}})}} != 1]?cawp-callctl-post,s,1)',
            f" same => n,Dial({tech}/${{CAWP_CTL_FROM}},{p['recall_seconds']},ib(cawp-callctl-tag^s^1(${{CAWP_CTL_ID}})))",
        ]
    lines += [
        " same => n,Goto(cawp-callctl-post,s,1)",
        "exten => i,1,Goto(cawp-callctl-post,s,1)",
        "",
        "; Pre-dial handler on the target's channel: the X-CAWP-Control header (the softphone shows",
        "; who transfers and the case; it never auto-answers a transfer).",
        "[cawp-callctl-tag]",
        f'exten => s,1,GotoIf($[${{REGEX("{CTL_RE}" ${{ARG1}})}} != 1]?done)',
        " same => n,SIPAddHeader(X-CAWP-Control: ${ARG1})" if sip else " same => n,Set(PJSIP_HEADER(add,X-CAWP-Control)=${ARG1})",
        " same => n(done),Return()",
        "",
        "; The AGENT after a blind transfer or a completed attended transfer.",
        "[cawp-callctl-done]",
        "exten => s,1,NoOp(CAWP call control ${CAWP_CTL_ID}: agent leaves)",
        *_evt("transferred", ",Control: ${CAWP_CTL_ID}"),
        " same => n,Hangup(16)",
        "",
        "; The PEER while the agent consults: hold music until the calling service returns it (Redirect).",
        "[cawp-callctl-hold]",
        f'exten => s,1,GotoIf($[${{REGEX("{ROOM_RE}" ${{CAWP_CTL_ROOM}})}} != 1]?cawp-callctl-post,s,1)',
        *_evt("customer_on_hold", ",Control: ${CAWP_CTL_ID}"),
        limit,
        *rec,
        " same => n,StartMusicOnHold()",
        " same => n(loop),Wait(2)",
        *([f' same => n,GotoIf($[${{REGEX("{SIP_CHANNEL_RE}" ${{CAWP_CTL_HOLDER}})}} != 1]?+2)',
           " same => n,GotoIf($[${LEN(${CHANNELS(^${CAWP_CTL_HOLDER}$)})} > 0]?loop)"] if sip else
          [" same => n,GotoIf($[0${CHANNEL_EXISTS(${CAWP_CTL_HOLDER})} > 0]?loop)"]),
        " same => n,StopMusicOnHold()",
        "; the agent is gone: the target already in the consult room takes the call, else release",
        " same => n,GotoIf($[0${CONFBRIDGE_INFO(parties,${CAWP_CTL_ROOM})} > 0]?cawp-callctl-room,customer,1)",
        " same => n,Goto(cawp-callctl-post,s,1)",
        "",
        "; ConfBridge rooms (attended transfer, conference). Roles: agent | customer | invitee.",
        "[cawp-callctl-room]",
        f'exten => agent,1,GotoIf($[${{REGEX("{ROOM_RE}" ${{CAWP_CTL_ROOM}})}} != 1]?bad)',
        " same => n,ConfBridge(${CAWP_CTL_ROOM},cawp_room,cawp_member)",
        " same => n(bad),Hangup()",
        f'exten => customer,1,GotoIf($[${{REGEX("{ROOM_RE}" ${{CAWP_CTL_ROOM}})}} != 1]?cawp-callctl-post,s,1)',
        " same => n,StopMusicOnHold()",
        *_evt("conference", ",Control: ${CAWP_CTL_ID}"),
        limit,
        *rec,
        " same => n,ConfBridge(${CAWP_CTL_ROOM},cawp_room,cawp_customer)",
        " same => n,Goto(cawp-callctl-post,s,1)",
        f'exten => invitee,1,GotoIf($[${{REGEX("{ROOM_RE}" ${{CAWP_CTL_ROOM}})}} != 1]?bad)',
        "; the room is gone (the call ended while this extension rang): do not open an empty room",
        " same => n,GotoIf($[0${CONFBRIDGE_INFO(parties,${CAWP_CTL_ROOM})} < 1]?bad)",
        " same => n,ConfBridge(${CAWP_CTL_ROOM},cawp_room,cawp_member)",
        " same => n(bad),Hangup()",
        "",
        "; Supervisor listening: the supervisor's own extension (Originate) spies on one agent channel.",
        *(["; d = DTMF 4 listen / 5 whisper / 6 barge; q quiet; E exit with the agent. No u (not in",
            "; Asterisk 11): the full channel name is a prefix of that one channel only. The calling service",
            "; changes the mode by redirecting the listening channel to the mode's extension."] if sip else
          ["; d = DTMF 4 listen / 5 whisper / 6 barge; q quiet; E exit with the agent; u = exact channel."]),
        "[cawp-callctl-spy]",
        f"exten => listen,1,Set(CAWP_SPY_OPTS=qdE{'' if sip else 'u'})",
        " same => n,Goto(spy,1)",
        f"exten => whisper,1,Set(CAWP_SPY_OPTS=qdE{'' if sip else 'u'}w)",
        " same => n,Goto(spy,1)",
        f"exten => barge,1,Set(CAWP_SPY_OPTS=qdE{'' if sip else 'u'}B)",
        " same => n,Goto(spy,1)",
        f'exten => spy,1,GotoIf($[${{REGEX("{SIP_CHANNEL_RE if sip else SPY_TARGET_RE}" ${{CAWP_SPY_TARGET}})}} != 1]?end)',
        " same => n,ChanSpy(${CAWP_SPY_TARGET},${CAWP_SPY_OPTS})",
        " same => n(end),Hangup()",
        "",
        "; The customer leg is released by call control (the control is over, the call is still up).",
        "[cawp-callctl-post]",
        f"exten => s,1,{post}",
        "",
    ]
    return "\n".join(lines)


CONFBRIDGE_PROFILES = """\
; CAWP call control (2026-10-20): ConfBridge profiles for services/calling/call_control.py rooms.
; Generated by `python -m services.calling.callctl_dialplan --confbridge`. confbridge.conf includes this file.
[cawp_room]
type = bridge
max_members = 6
record_conference = no
internal_sample_rate = auto
mixing_interval = 20
video_mode = none

[cawp_customer]
type = user
marked = yes
quiet = yes
announce_user_count = no
announce_only_user = no
music_on_hold_when_empty = no
dsp_drop_silence = yes
dtmf_passthrough = no

[cawp_member]
type = user
end_marked = yes
quiet = yes
announce_user_count = no
announce_only_user = no
music_on_hold_when_empty = no
dsp_drop_silence = yes
dtmf_passthrough = no
"""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--confbridge" in argv:
        sys.stdout.write(CONFBRIDGE_PROFILES)
        return 0
    data = sys.stdin.read(65536 + 1)
    if len(data) > 65536:
        print("policy is larger than 64 KiB", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(render(json.loads(data) if data.strip() else {}))
    except (ValueError, CallControlPolicyError) as e:
        print(f"invalid policy: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
