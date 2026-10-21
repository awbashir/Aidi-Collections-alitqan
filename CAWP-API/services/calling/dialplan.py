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
    dial_format       "e164" (default, output unchanged): the trunk is dialled with the E.164 number.
                      "national_sa": the trunk is dialled in Saudi national format, +966XXXXXXXXX ->
                      0XXXXXXXXX (the Alitqan GSM gateways expect 05XXXXXXXX), and any destination
                      that is not +966 followed by 9 digits is DENIED. The Originate Exten, the
                      AGI-granted destination and the dispatch token stay E.164.
    trunks            ["Mobily18", "Mobily19", ...] (1..16 distinct names, the first = `trunk`;
                      driver SIP only): trunk SELECTION before the one Dial. The first trunk in the
                      list whose chan_sip `${SIPPEER(<trunk>,status)}` starts with `OK` (qualify
                      reachable) is dialled, and only that one: whatever the DIALSTATUS, no other
                      trunk is tried afterwards (chan_sip maps far-end 480/404/503... to CONGESTION
                      or CHANUNAVAIL, so "retry on CONGESTION" could ring the same customer on
                      several gateways). No trunk OK -> UserEvent `no_trunk` and Hangup(34), no
                      Dial. Absent (or just [trunk]) = one Dial of `trunk`, output unchanged.
    pbx_events        "modern" (default, output unchanged) | "legacy": Asterisk <= 11 (the Alitqan
                      Issabel; CALLING_PBX_EVENTS=legacy). Every CollectionsCall UserEvent body also
                      carries `ChanUniqueid: ${UNIQUEID}`, the Uniqueid of the channel that sends it
                      (the agent channel, or the customer channel in the pre-dial, answer and hangup
                      handlers): Asterisk 11 events have no Linkedid, and the calling service
                      attributes a UserEvent only when that channel is a leg of the claimed attempt.
    issabel_trunk_ids {"Mobily18": 2, ...} (driver SIP; one IssabelPBX trunk number, the N of the
                      `OUT_N = SIP/<trunk>` global, for every dialled trunk): the outgoing caller ID
                      that IssabelPBX's own outbound route would send (macro-outbound-callerid),
                      set right before the one Dial: the agent extension's `AMPUSER/<ext>/outboundcid`,
                      else the trunk's `OUTCID_N`, and `FORCEDOUTCID_N` over both; read with GLOBAL().
                      Without it the gateway sees the agent's extension as the caller ID (the Alitqan
                      gateways answer that with SIP 604). Absent = output unchanged.

Asterisk 11 (legacy) support of every construct used here: AGI/FastAGI, GLOBAL(), REGEX(),
substrings ${VAR:offset}, GotoIfTime with a timezone, TIMEOUT(absolute), STRFTIME, MixMonitor b,
CDR(userfield) / CDR(recordingfile) (cdr_mysql userfield=1), CHANNEL(hangup_handler_push) and Dial
b() pre-dial handlers (both new in Asterisk 11), Dial i, U(context) (Gosub to context,s,1), L(),
F(context^exten^priority), UserEvent(name,Key: value,...), and DIALSTATUS after Dial. PJSIP_ENDPOINT
is only generated for the PJSIP driver.

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
OPTIONAL_KEYS = {"caller_id_gosub", "post_call_context", "dial_format", "trunks", "pbx_events", "issabel_trunk_ids"}
DIAL_FORMATS = ("e164", "national_sa")
MAX_TRUNKS = 16
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
    if "dial_format" in policy and policy["dial_format"] not in DIAL_FORMATS:
        raise DialplanPolicyError("dial_format must be e164 or national_sa")
    if "pbx_events" in policy and policy["pbx_events"] not in ("modern", "legacy"):
        raise DialplanPolicyError("pbx_events must be modern or legacy")
    if "trunks" in policy:
        trunks = policy["trunks"]
        if (not isinstance(trunks, list) or not 1 <= len(trunks) <= MAX_TRUNKS
                or any(not isinstance(t, str) or not TRUNK_RE.match(t) for t in trunks)
                or len(set(trunks)) != len(trunks)):
            raise DialplanPolicyError(f"trunks must be 1..{MAX_TRUNKS} distinct names matching [A-Za-z0-9_-]{{1,64}}")
        if trunks[0] != policy["trunk"]:
            raise DialplanPolicyError("trunks must start with trunk (the first trunk dialled)")
        if len(trunks) > 1 and policy["driver"] != "SIP":
            raise DialplanPolicyError("trunks (more than one) needs driver SIP: the trunk is selected by "
                                      "SIPPEER(<trunk>,status) before the one Dial")
    if "issabel_trunk_ids" in policy:
        ids = policy["issabel_trunk_ids"]
        if policy["driver"] != "SIP":
            raise DialplanPolicyError("issabel_trunk_ids needs driver SIP (IssabelPBX chan_sip trunks)")
        if (not isinstance(ids, dict) or set(ids) != set(policy.get("trunks") or [policy["trunk"]])
                or any(not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 9999 for n in ids.values())):
            raise DialplanPolicyError("issabel_trunk_ids must map every dialled trunk to its IssabelPBX trunk number 1..9999")
    try:
        windows = validate_windows(policy["calling_windows"])
    except WindowError as e:
        raise DialplanPolicyError(f"calling_windows: {e}") from None
    return {**policy, "max_call_seconds": _int(policy, "max_call_seconds", 30, 14400),
            "ring_seconds": _int(policy, "ring_seconds", 1, 60), "calling_windows": windows}


CHAN_UNIQUEID = ",ChanUniqueid: ${UNIQUEID}"


def _event(state: str, extra: str = "", legacy: bool = False) -> str:
    chan = CHAN_UNIQUEID if legacy else ""
    return f" same => n,UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}}{chan},State: {state}{extra})"


def _dial_target(driver: str, trunk: str, number_var: str) -> str:
    if driver == "PJSIP":
        return f"PJSIP/${{{number_var}}}@{trunk}"
    return f"SIP/{trunk}/${{{number_var}}}"


def _issabel_caller_id(label: str = "") -> list[str]:
    """IssabelPBX macro-outbound-callerid for COLLECTIONS_TRUNK_ID: user CID, else trunk CID; forced wins."""
    return [
        f" same => n{label},Set(COLLECTIONS_CID=${{DB(AMPUSER/${{COLLECTIONS_GATE_EXTENSION}}/outboundcid)}})",
        " same => n,ExecIf($[${LEN(${COLLECTIONS_CID})} = 0]?Set(COLLECTIONS_CID=${GLOBAL(OUTCID_${COLLECTIONS_TRUNK_ID})}))",
        " same => n,ExecIf($[${LEN(${GLOBAL(FORCEDOUTCID_${COLLECTIONS_TRUNK_ID})})} != 0]"
        "?Set(COLLECTIONS_CID=${GLOBAL(FORCEDOUTCID_${COLLECTIONS_TRUNK_ID})}))",
        " same => n,ExecIf($[${LEN(${COLLECTIONS_CID})} != 0]?Set(CALLERID(all)=${COLLECTIONS_CID}))",
        " same => n,Set(CDR(outbound_cnum)=${CALLERID(num)})",
    ]


def render(policy) -> str:
    p = validate_policy(policy)
    ctx, driver, trunk = p["context"], p["driver"], p["trunk"]
    trunks = p.get("trunks") or [trunk]
    national = p.get("dial_format", "e164") == "national_sa"
    legacy = p.get("pbx_events", "modern") == "legacy"
    trunk_ids = p.get("issabel_trunk_ids")
    number_var = "COLLECTIONS_DIAL_NUMBER" if national else "COLLECTIONS_GATE_DESTINATION"
    ev = lambda state, extra="": _event(state, extra, legacy)  # noqa: E731
    ranges = dialplan_time_ranges(p["calling_windows"])
    windows_text = json.dumps(p["calling_windows"], separators=(",", ":"))
    extra_policy = "".join([
        f" trunks={','.join(trunks)} (first with SIPPEER status OK, one Dial, no retry)" if len(trunks) > 1 else "",
        " dial_format=national_sa" if national else "",
        " pbx_events=legacy (Asterisk <= 11)" if legacy else "",
        f" issabel_trunk_ids={','.join(f'{t}:{trunk_ids[t]}' for t in trunks)} (IssabelPBX outbound caller ID)"
        if trunk_ids else ""])
    lines = [
        "; Generated by `python -m services.calling.dialplan` (CAWP Phase 5, extension-first).",
        "; Review before installation. Regenerate instead of editing by hand.",
        f"; policy: context={ctx} driver={driver} trunk={trunk} max_call_seconds={p['max_call_seconds']}"
        f" ring_seconds={p['ring_seconds']} record={'yes' if p['record'] else 'no'}{extra_policy}",
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
        ev("agent_answered"),
        " same => n,AGI(${GLOBAL(COLLECTIONS_AGI_URL)})",
        " same => n,Set(COLLECTIONS_TOKEN=)",
        ' same => n,GotoIf($["${AGISTATUS}" != "SUCCESS"]?deny)',
        ' same => n,GotoIf($["${COLLECTIONS_GATE}" != "ALLOW"]?deny)',
        ' same => n,GotoIf($[${REGEX("^[+][1-9][0-9]{7,14}$" ${COLLECTIONS_GATE_DESTINATION})} != 1]?deny)',
        ' same => n,GotoIf($["${COLLECTIONS_GATE_DESTINATION}" != "${EXTEN}"]?deny)',
        *([' same => n,GotoIf($[${REGEX("^[+]966[0-9]{9}$" ${COLLECTIONS_GATE_DESTINATION})} != 1]?deny)']
          if national else []),
        ' same => n,GotoIf($[${REGEX("^[0-9]{2,6}$" ${COLLECTIONS_GATE_EXTENSION})} != 1]?deny)',
        ' same => n,GotoIf($[${REGEX("^[0-9]{2,5}$" ${COLLECTIONS_GATE_LIMIT})} != 1]?deny)',
        f" same => n,GotoIf($[${{COLLECTIONS_GATE_LIMIT}} < 30 | ${{COLLECTIONS_GATE_LIMIT}} > {p['max_call_seconds']}]?deny)",
        ' same => n,GotoIf($[${REGEX("^[0-9]{10,12}$" ${COLLECTIONS_GATE_EXPIRES})} != 1]?deny)',
        ev("authorized"),
        *[f" same => n,GotoIfTime({r['time']},{r['weekday']},*,*,Asia/Riyadh?window)" for r in ranges],
        " same => n,Goto(deny)",
        " same => n(window),GotoIf($[${EPOCH} >= ${COLLECTIONS_GATE_EXPIRES}]?deny)",
    ]
    if driver == "PJSIP":
        lines += [
            ' same => n,GotoIf($["${PJSIP_ENDPOINT(${COLLECTIONS_GATE_EXTENSION},allow_transfer)}" != "false"]?deny)',
            *[f' same => n,GotoIf($["${{PJSIP_ENDPOINT({t},allow_transfer)}}" != "false"]?deny)' for t in trunks],
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
            ev("customer_dialing", ",Recording: ${COLLECTIONS_RECORDING}"),
        ]
    else:
        lines.append(ev("customer_dialing"))
    if national:                                  # +966XXXXXXXXX -> 0XXXXXXXXX (re-checked)
        lines += [
            " same => n,Set(COLLECTIONS_DIAL_NUMBER=0${COLLECTIONS_GATE_DESTINATION:4})",
            ' same => n,GotoIf($[${REGEX("^0[0-9]{9}$" ${COLLECTIONS_DIAL_NUMBER})} != 1]?deny)',
        ]
    options = ("ib(collections-customer-created^s^1)U(collections-customer-answered)L($[${COLLECTIONS_GATE_LIMIT} * 1000])"
               + (f"F({p['post_call_context']}^s^1)" if p.get("post_call_context") else ""))
    if len(trunks) == 1:
        if trunk_ids:
            lines += [f" same => n,Set(COLLECTIONS_TRUNK_ID={trunk_ids[trunk]})", *_issabel_caller_id()]
        lines += [
            f" same => n,Dial({_dial_target(driver, trunk, number_var)},{p['ring_seconds']},{options})",
            ev("dial_end", ",DialStatus: ${DIALSTATUS}"),
            " same => n,Hangup()",
        ]
    else:
        # chan_sip (driver SIP only): pick the first reachable trunk BEFORE dialling; dial it once.
        # COLLECTIONS_TRUNK is only ever set from the constant names below.
        for t in trunks:
            lines += [
                f" same => n,Set(COLLECTIONS_TRUNK={t})",
                *([f" same => n,Set(COLLECTIONS_TRUNK_ID={trunk_ids[t]})"] if trunk_ids else []),
                f' same => n,GotoIf($["${{SIPPEER({t},status):0:2}}" = "OK"]?trunk)',
            ]
        lines += [
            " same => n,Set(COLLECTIONS_TRUNK=)",
            " same => n,Goto(notrunk)",
            *(_issabel_caller_id("(trunk)") if trunk_ids else []),
            f" same => n{'' if trunk_ids else '(trunk)'},Dial({_dial_target(driver, '${COLLECTIONS_TRUNK}', number_var)},{p['ring_seconds']},"
            f"{options})",
            ev("dial_end", ",DialStatus: ${DIALSTATUS}"),
            " same => n,Hangup()",                  # any DIALSTATUS ends the attempt: never another trunk
            f" same => n(notrunk),UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}}"
            f"{CHAN_UNIQUEID if legacy else ''},State: no_trunk)",
            " same => n,Hangup(34)",
        ]
    lines += [
        f" same => n(deny),UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}}"
        f"{CHAN_UNIQUEID if legacy else ''},State: denied)",
        " same => n,Hangup(21)",
        "exten => i,1,Hangup(21)",
        "exten => t,1,Hangup(21)",
        "exten => T,1,Hangup()",
        "",
        "; Pre-dial handler: runs on the CUSTOMER channel before it is dialled.",
        "[collections-customer-created]",
        "exten => s,1,Set(CHANNEL(hangup_handler_push)=collections-customer-finished,s,1)",
        ev("customer_created"),
        " same => n,Return()",
        "",
        "; Runs on the CUSTOMER channel only after the customer answered (Dial U option).",
        "[collections-customer-answered]",
        f"exten => s,1,UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}}{CHAN_UNIQUEID if legacy else ''},"
        "State: customer_answered)",
        " same => n,Return()",
        "",
        "; Hangup handlers: one per leg, including failed and denied attempts. When the customer hangs up",
        "; an answered call, Dial does not return (no `g` option), so the agent's handler carries DIALSTATUS.",
        "[collections-customer-finished]",
        f"exten => s,1,UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}}{CHAN_UNIQUEID if legacy else ''},"
        "State: customer_ended,Cause: ${HANGUPCAUSE})",
        " same => n,Return()",
        "",
        "[collections-agent-finished]",
        f"exten => s,1,UserEvent(CollectionsCall,AttemptId: ${{COLLECTIONS_ATTEMPT}}{CHAN_UNIQUEID if legacy else ''},"
        "State: agent_ended,Cause: ${HANGUPCAUSE},DialStatus: ${DIALSTATUS})",
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
