"""Restricted extension-first dialplan generator (offline review artifact).

    python -m services.calling.dialplan < policy.json > collections-human.conf

Ported from the rebuild's `human-dialplan.js` (concept, not code). The output is the exact
text the test PBX loads (`pbx-test/conf/extensions_collections.conf`, checked by
tests/test_calling_config.py) and what the Issabel runbook (docs/issabel-commissioning.md)
asks the operator to paste into `extensions_custom.conf`. It installs nothing and contacts
no PBX.

Policy (JSON object, exactly these keys):

    context           "collections-human-outbound" (must start with collections-human-)
    driver            "PJSIP" | "SIP"             (chan_sip for older Issabel builds)
    trunk             outbound trunk endpoint/peer name
    max_call_seconds  30..14400  hard upper bound of the AGI-granted limit
    ring_seconds      1..60      customer ringing timeout
    record            true | false  MixMonitor under the monitor dir as YYYY/MM/DD/<uniqueid>.wav
    calling_windows   Riyadh windows (services/calling/windows.py); [] denies everything

Optional:

    caller_id_gosub   a dialplan context the PBX operator maintains (e.g. "cawp-cid-rotate") that
                      sets the outgoing caller ID; called with Gosub(<context>,s,1) on the agent
                      channel right before the customer Dial. Absent = no call, output unchanged.
    post_call_context a dialplan context (e.g. "cawp-survey") the customer leg goes to when the AGENT
                      hangs up first (Dial option F(<context>^s^1)): the post-call survey. The
                      context decides itself (FastAGI collections-survey-offer) and hangs up when the
                      call is not eligible. Absent = no F option, output unchanged.

How a call flows (extension-first):

1. The server originates `PJSIP/<agent extension>` with ChannelId `cawp-<attempt id>`,
   Context = the policy context, Exten = the customer's E.164 number, and channel variables
   `__COLLECTIONS_ATTEMPT` (inherited) and `COLLECTIONS_TOKEN` (one-shot, agent channel only).
2. Only when the agent answers does the agent channel run this context.
3. `AGI(${GLOBAL(COLLECTIONS_AGI_URL)})` asks the calling service's FastAGI server. The URL
   is read with GLOBAL() so a channel variable of the same name cannot redirect it. The
   server consumes the one-shot token and, only then, sets the gate variables and ALLOW last.
4. Every gate output is re-validated here; any failure goes to `deny` (Hangup 21), never to
   Dial. PJSIP endpoints must have `allow_transfer=no` (checked right before Dial).
5. The customer leg is dialled through the one fixed trunk with a ring timeout, an absolute
   channel timeout and a Dial duration limit (`L`), `i` (ignore forwarding) and no transfer
   options. `UserEvent(CollectionsCall, AttemptId, State)` marks each step; hangup handlers
   report both legs' ends (the agent's also carries DIALSTATUS, because an answered Dial whose
   customer hangs up does not continue the dialplan - no `g` option is generated).
"""
from __future__ import annotations

import json
import re
import sys

from services.calling.windows import WindowError, dialplan_time_ranges, validate_windows

KEYS = {"context", "driver", "trunk", "max_call_seconds", "ring_seconds", "record", "calling_windows"}
OPTIONAL_KEYS = {"caller_id_gosub", "post_call_context"}
GOSUB_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CONTEXT_RE = re.compile(r"^collections-human-[a-z0-9_-]{1,50}$")
TRUNK_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DialplanPolicyError(ValueError):
    pass


def _int(policy, key, lo, hi):
    v = policy.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < lo or v > hi:
        raise DialplanPolicyError(f"{key} must be an integer {lo}..{hi}")
    return v


def validate_policy(policy) -> dict:
    if not isinstance(policy, dict) or not KEYS <= set(policy) or set(policy) - KEYS - OPTIONAL_KEYS:
        raise DialplanPolicyError("policy must be an object with exactly: " + ", ".join(sorted(KEYS))
                                  + " (optional: " + ", ".join(sorted(OPTIONAL_KEYS)) + ")")
    if "caller_id_gosub" in policy and (not isinstance(policy["caller_id_gosub"], str)
                                        or not GOSUB_RE.match(policy["caller_id_gosub"])):
        raise DialplanPolicyError("caller_id_gosub must match [a-z][a-z0-9_-]{0,63}")
    if "post_call_context" in policy and (not isinstance(policy["post_call_context"], str)
                                          or not GOSUB_RE.match(policy["post_call_context"])):
        raise DialplanPolicyError("post_call_context must match [a-z][a-z0-9_-]{0,63}")
    if not isinstance(policy["context"], str) or not CONTEXT_RE.match(policy["context"]):
        raise DialplanPolicyError("context must match collections-human-[a-z0-9_-]{1,50}")
    if policy["driver"] not in ("PJSIP", "SIP"):
        raise DialplanPolicyError("driver must be PJSIP or SIP")
    if not isinstance(policy["trunk"], str) or not TRUNK_RE.match(policy["trunk"]):
        raise DialplanPolicyError("trunk must match [A-Za-z0-9_-]{1,64}")
    if not isinstance(policy["record"], bool):
        raise DialplanPolicyError("record must be true or false")
    try:
        windows = validate_windows(policy["calling_windows"])
    except WindowError as e:
        raise DialplanPolicyError(f"calling_windows: {e}") from None
    return {**policy, "max_call_seconds": _int(policy, "max_call_seconds", 30, 14400),
            "ring_seconds": _int(policy, "ring_seconds", 1, 60), "calling_windows": windows}


def _event(state: str, extra: str = "") -> str:
    return f" same => n,UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}},State: {state}{extra})"


def render(policy) -> str:
    p = validate_policy(policy)
    ctx, driver, trunk = p["context"], p["driver"], p["trunk"]
    ranges = dialplan_time_ranges(p["calling_windows"])
    if driver == "PJSIP":
        dial_target = f"PJSIP/${{COLLECTIONS_GATE_DESTINATION}}@{trunk}"
    else:
        dial_target = f"SIP/{trunk}/${{COLLECTIONS_GATE_DESTINATION}}"
    windows_text = json.dumps(p["calling_windows"], separators=(",", ":"))
    lines = [
        "; Generated by `python -m services.calling.dialplan` (CAWP Phase 5, extension-first).",
        "; Review before installation. Regenerate instead of editing by hand.",
        f"; policy: context={ctx} driver={driver} trunk={trunk} max_call_seconds={p['max_call_seconds']}"
        f" ring_seconds={p['ring_seconds']} record={'yes' if p['record'] else 'no'}",
        f"; calling_windows (Asia/Riyadh, end exclusive): {windows_text}",
        "; Requires the FastAGI authorization hook (the `calling` service). No ALLOW, no customer Dial.",
        "; Never include this context in an endpoint's own dialing context. Disable endpoint transfers.",
        "; Required [globals], from protected include files (never from channel variables):",
        ";   COLLECTIONS_AGI_URL          agi://<calling host>:4573/collections-authorize",
        ";   COLLECTIONS_AGI_KEY          shared secret the AGI server checks (CALLING_AGI_SECRET)",
        ";   COLLECTIONS_HUMAN_PBX_EPOCH  UUID written by the pre-start hook on every PBX process start",
        *([f"; Caller ID: Gosub({p['caller_id_gosub']},s,1) before the customer Dial (operator-maintained context)"]
          if p.get("caller_id_gosub") else []),
        *([f"; Post-call: the customer goes to {p['post_call_context']},s,1 when the agent hangs up first (Dial F)"]
          if p.get("post_call_context") else []),
        "",
        f"[{ctx}]",
        "exten => _+X.,1,Set(COLLECTIONS_GATE=)",
        " same => n,Set(COLLECTIONS_GATE_DESTINATION=)",
        " same => n,Set(COLLECTIONS_GATE_EXTENSION=)",
        " same => n,Set(COLLECTIONS_GATE_LIMIT=)",
        " same => n,Set(COLLECTIONS_GATE_EXPIRES=)",
        " same => n,Set(CHANNEL(hangup_handler_push)=collections-agent-finished,s,1)",
        _event("agent_answered"),
        " same => n,AGI(${GLOBAL(COLLECTIONS_AGI_URL)})",
        " same => n,Set(COLLECTIONS_TOKEN=)",
        ' same => n,GotoIf($["${AGISTATUS}" != "SUCCESS"]?deny)',
        ' same => n,GotoIf($["${COLLECTIONS_GATE}" != "ALLOW"]?deny)',
        ' same => n,GotoIf($[${REGEX("^[+][1-9][0-9]{7,14}$" ${COLLECTIONS_GATE_DESTINATION})} != 1]?deny)',
        ' same => n,GotoIf($["${COLLECTIONS_GATE_DESTINATION}" != "${EXTEN}"]?deny)',
        ' same => n,GotoIf($[${REGEX("^[0-9]{2,6}$" ${COLLECTIONS_GATE_EXTENSION})} != 1]?deny)',
        ' same => n,GotoIf($[${REGEX("^[0-9]{2,5}$" ${COLLECTIONS_GATE_LIMIT})} != 1]?deny)',
        f" same => n,GotoIf($[${{COLLECTIONS_GATE_LIMIT}} < 30 | ${{COLLECTIONS_GATE_LIMIT}} > {p['max_call_seconds']}]?deny)",
        ' same => n,GotoIf($[${REGEX("^[0-9]{10,12}$" ${COLLECTIONS_GATE_EXPIRES})} != 1]?deny)',
        _event("authorized"),
        *[f" same => n,GotoIfTime({r['time']},{r['weekday']},*,*,Asia/Riyadh?window)" for r in ranges],
        " same => n,Goto(deny)",
        " same => n(window),GotoIf($[${EPOCH} >= ${COLLECTIONS_GATE_EXPIRES}]?deny)",
    ]
    if driver == "PJSIP":
        lines += [
            ' same => n,GotoIf($["${PJSIP_ENDPOINT(${COLLECTIONS_GATE_EXTENSION},allow_transfer)}" != "false"]?deny)',
            f' same => n,GotoIf($["${{PJSIP_ENDPOINT({trunk},allow_transfer)}}" != "false"]?deny)',
        ]
    lines += [
        " same => n,Set(__DYNAMIC_FEATURES=)",
        " same => n,Set(TIMEOUT(absolute)=${COLLECTIONS_GATE_LIMIT})",
        " same => n,Set(CDR(userfield)=cawp:${COLLECTIONS_ATTEMPT})",
    ]
    if p.get("caller_id_gosub"):
        lines.append(f" same => n,Gosub({p['caller_id_gosub']},s,1)")
    if p["record"]:
        lines += [
            " same => n,Set(COLLECTIONS_RECORDING=${STRFTIME(${EPOCH},Asia/Riyadh,%Y/%m/%d)}/${UNIQUEID}.wav)",
            " same => n,Set(CDR(recordingfile)=${COLLECTIONS_RECORDING})",
            " same => n,MixMonitor(${COLLECTIONS_RECORDING},b)",
            _event("customer_dialing", ",Recording: ${COLLECTIONS_RECORDING}"),
        ]
    else:
        lines.append(_event("customer_dialing"))
    lines += [
        f" same => n,Dial({dial_target},{p['ring_seconds']},ib(collections-customer-created^s^1)"
        "U(collections-customer-answered)L($[${COLLECTIONS_GATE_LIMIT} * 1000])"
        + (f"F({p['post_call_context']}^s^1)" if p.get("post_call_context") else "") + ")",
        _event("dial_end", ",DialStatus: ${DIALSTATUS}"),
        " same => n,Hangup()",
        " same => n(deny),UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},State: denied)",
        " same => n,Hangup(21)",
        "exten => i,1,Hangup(21)",
        "exten => t,1,Hangup(21)",
        "exten => T,1,Hangup()",
        "",
        "; Pre-dial handler: runs on the CUSTOMER channel before it is dialled.",
        "[collections-customer-created]",
        "exten => s,1,Set(CHANNEL(hangup_handler_push)=collections-customer-finished,s,1)",
        _event("customer_created"),
        " same => n,Return()",
        "",
        "; Runs on the CUSTOMER channel only after the customer answered (Dial U option).",
        "[collections-customer-answered]",
        "exten => s,1,UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},State: customer_answered)",
        " same => n,Return()",
        "",
        "; Hangup handlers: one per leg, including failed and denied attempts. When the customer hangs up",
        "; an answered call, Dial does not return (no `g` option), so the agent's handler carries DIALSTATUS.",
        "[collections-customer-finished]",
        "exten => s,1,UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},State: customer_ended,Cause: ${HANGUPCAUSE})",
        " same => n,Return()",
        "",
        "[collections-agent-finished]",
        "exten => s,1,UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},State: agent_ended,Cause: ${HANGUPCAUSE},"
        "DialStatus: ${DIALSTATUS})",
        " same => n,Return()",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    data = sys.stdin.read(65536 + 1)
    if len(data) > 65536:
        print("policy is larger than 64 KiB", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(render(json.loads(data)))
    except (ValueError, DialplanPolicyError) as e:
        print(f"invalid policy: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
