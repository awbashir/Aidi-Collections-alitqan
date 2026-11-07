"""Call monitor (customer request 2026-10-13): live calls and calls by user over time.

Scope, decided here for every request (never by the page); the page's user filter
(`extension`) can only narrow it (403 `not_in_scope` outside it):

* a user whose role is `SuperAdmin` sees every extension and calls not yet at an agent (IVR);
* a supervisor (an active `supervisors` row) sees the active extensions of their team
  (`supervisor_users`, and since 2026-10-21 the users of their contracts: `supervisor_contracts`
  × `users.contract_id`) and their own;
* anyone else with access sees only their own active extension.

Live calls come from the calling service's channel snapshot (`pbx_live_channels`, refreshed
every few seconds; services/calling/live_monitor.py), grouped into calls by linkedid. A
snapshot that is old or failed is reported as such, never as "no calls".

Calls by user come from the PBX call records (asteriskcdrdb.cdr, read-only), classified per
agent extension: outbound (PJSIP/<ext> -> trunk), inbound (-> PJSIP/<ext>), internal (agent to
agent). Agent channels follow CALLING_DRIVER (common.channel_driver): `PJSIP/<ext>-...`, or with
chan_sip `SIP/<ext>-<8 hex>` (then a trunk is a SIP/ channel that is not an agent's). A ring-group call rings several extensions: each rung extension that did not answer
counts one missed inbound. Extensions map to users through their CURRENT owner
(`user_extensions`): calls are attributed to today's owner of the extension.

Downloads (customer request 2026-10-15), same scope, period and user filter as the page:
* the summary (export_summary): the per-user table, its totals and the calls over time;
* the call detail (export_calls): one row per call and agent (calls(): the CDR rows of one
  linkedid merged, as the statistics count them) with the time, the other party's number, the
  line (caller ID) used, the result, ring and talk seconds and whether it was recorded. The
  number is matched to a client ONLY among the cases the downloader may see (money.scope_sql),
  through the normalised-mobile index; otherwise the client columns stay empty.

Talk-time buckets (customer request 2026-10-17): per user, in the totals and in both downloads,
the ANSWERED calls whose talk time (the merged call's, _merge() below) is STRICTLY longer
than 60, 120 and 180 seconds (`talk_over_60` / `talk_over_120` / `talk_over_180`; any direction,
like talk_seconds). The call detail has one yes/no column per bucket.

One user's calls (customer request 2026-10-17, calls_page(); GET /v2/calling/monitor/calls): the
calls() rows of the period, newest first, paged, with the CDR `recording` value (recordingfile,
the merged call's first non-empty one). The recording itself is played/downloaded through the
legacy GET /cdr/recording, which authorizes it again (a visible CDR record naming the file) under
its own keys; the page only shows the buttons with those keys.

Search by the client's number (customer request 2026-10-19): `number` on calls() / calls_page() /
export_calls keeps the calls whose number matches (services/calling/number_search.py: digits only,
the last 9); without `extension` the search covers every extension of the scope.

Charts (customer request 2026-10-20): stats() also returns, additively, per-bucket outcome and
direction counts, the talk-time distribution, a weekday x hour heatmap, per-user ring time and
answered counts, and follows `number` (the summary download does not: it keeps every call).

Result, answered, ring and talk of one merged call (fix 2026-10-19, _merge(); the statistics,
the call detail, the calls page and both downloads use the same classification):
* only a Dial row (a non-empty dstchannel: the Dial to the trunk or to an agent) says whether
  the call was answered. The rows the PBX writes after the dial (Hangup / Busy / Congestion /
  Playback, empty dstchannel) are about the agent's own leg: they never mark a call answered
  and give the result only when there is no Dial row (e.g. Congestion before dialling), never
  "ANSWERED" (then the result is unknown, "");
* dial-pad and inbound: talk = billsec of the answered Dial row (the longest), ring = its
  duration - billsec (no answer: of the first Dial row);
* click-to-call / campaign calls (uniqueid / linkedid `cawp-<attempt>`, userfield
  `cawp:<attempt>`): the CDR billsec counts from when the AGENT's browser answered, so answered,
  talk and ring come from the app's own call_attempts row (pbx_channel_id `cawp-<attempt>`, one
  query per request): answered = bridged (customer conversation evidenced), talk = ended -
  bridged, ring = bridged (or ended) - customer leg dialled (0 when the customer leg never
  started). Without that row the CDR rule above applies;
* the answer macro an answering agent's channel runs in a ring-group call (dcontext macro-*,
  PJSIP/<ext> or SIP/<ext> -> Local/...) is not a call the agent placed: ignored (the ring group's Dial row
  to that agent counts it).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import uuid

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from services.calling import number_search as numsearch
from services.calling.common import USER_NAME_SQL, channel_driver, iso, me, sess

AGENT_RE = re.compile(r"^PJSIP/([0-9]{2,6})-")
AGENT_RES = {"PJSIP": AGENT_RE, "SIP": re.compile(r"^SIP/([0-9]{2,6})-[0-9a-f]{8}")}
AGENT_EXT_RE = re.compile(r"^[0-9]{2,6}$")
CAWP_ID_RE = re.compile(r"^cawp[-:]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
MAX_DAYS = 31
MAX_CDR_ROWS = 200_000
TALK_BUCKETS = (60, 120, 180)            # answered calls with talk time strictly longer than these (seconds)
PAGE_SIZE, MAX_PAGE_SIZE = 50, 200
# the charts (2026-10-20, stats()): answered calls by talk time, (above, up to and including) seconds
TALK_DISTRIBUTION = ((0, 60), (60, 120), (120, 180), (180, 300), (300, 600), (600, None))
OUTCOMES = ("answered", "no_answer", "busy", "failed", "unknown")
DIRECTIONS = ("outbound", "inbound_answered", "inbound_missed", "internal")


# --------------------------------------------------------------------------- scope
def scope(user_id=None) -> dict:
    uid = str(user_id or me())
    role = sess().execute(text("SELECT r.name FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = CAST(:u AS uuid)"),
                          {"u": uid}).scalar()
    owners = sess().execute(text(f"""
        SELECT x.extension, x.user_id::text AS user_id, {USER_NAME_SQL.format(a='u')} AS name
          FROM user_extensions x LEFT JOIN users u ON u.id = x.user_id
         WHERE x.released_at IS NULL""")).mappings().all()
    names = {o["extension"]: {"id": o["user_id"], "name": o["name"]} for o in owners}
    if role == "SuperAdmin":
        return {"kind": "all", "extensions": None, "owners": names}
    team = {r[0] for r in sess().execute(text("""
        SELECT su.user_id::text FROM supervisors s
          JOIN supervisor_users su ON su.supervisor_id = s.id AND su.is_active
         WHERE s.user_id = CAST(:u AS uuid) AND s.is_active
        UNION
        -- 2026-10-21: and the users of the supervisor's contracts (users.contract_id), as the case
        -- scope already counts supervised contracts (services/clients_scope.py); the old system's
        -- supervisors are linked to contracts, hardly to users
        SELECT tu.id::text FROM supervisors s
          JOIN supervisor_contracts sc ON sc.supervisor_id = s.id AND sc.is_active
          JOIN users tu ON tu.contract_id = sc.contract_id AND tu.is_active
         WHERE s.user_id = CAST(:u AS uuid) AND s.is_active"""), {"u": uid})}
    kind = "team" if team else "own"
    team.add(uid)
    exts = {ext for ext, o in names.items() if o["id"] in team}
    return {"kind": kind, "extensions": exts, "owners": names}


def narrow(sc: dict, extension, contract=None) -> dict:
    """The scope narrowed to one extension (the page's user filter; 403 outside the scope) and/or
    to the users of one contract (2026-10-21: `users.contract_id`; inside the scope only, so an
    unknown or foreign contract simply matches nobody)."""
    if contract not in (None, ""):
        try:
            cid = str(uuid.UUID(str(contract)))
        except (ValueError, TypeError):
            raise ApiError(422, VALIDATION_FAILED, "contract must be a UUID", {"contract": "invalid"}) from None
        users = {r[0] for r in sess().execute(text(
            "SELECT id::text FROM users WHERE contract_id = CAST(:c AS uuid)"), {"c": cid})}
        exts = {e for e, o in sc["owners"].items() if o["id"] in users}
        if sc["extensions"] is not None:
            exts &= sc["extensions"]
        sc = {**sc, "extensions": exts, "contract": cid}
    if extension in (None, ""):
        return sc
    ext = str(extension).strip()
    if not AGENT_EXT_RE.match(ext) or (sc["extensions"] is not None and ext not in sc["extensions"]):
        raise ApiError(403, "not_in_scope", "this extension is not in your scope", {"extension": "not_in_scope"})
    return {**sc, "extensions": {ext}, "filtered": ext}


def contracts(sc: dict) -> list[dict]:
    """The contracts of the users in the scope (before any filter), for the page's contract filter:
    [{id, name, users}] by name. Never a contract with nobody of the caller's scope."""
    exts = sc["extensions"] if sc["extensions"] is not None else set(sc["owners"])
    ids = sorted({sc["owners"][e]["id"] for e in exts if e in sc["owners"] and sc["owners"][e]["id"]})
    if not ids:
        return []
    rows = sess().execute(text("""
        SELECT c.id::text AS id, c.name, count(*) AS users
          FROM users u JOIN contracts c ON c.id = u.contract_id
         WHERE u.id = ANY(CAST(:ids AS uuid[]))
         GROUP BY c.id, c.name ORDER BY c.name"""), {"ids": ids}).mappings().all()
    return [{"id": r["id"], "name": r["name"], "users": int(r["users"])} for r in rows]


def options(sc: dict) -> list[dict]:
    """Every extension of the scope (before any filter), for the page's user filter."""
    exts = sc["extensions"] if sc["extensions"] is not None else sc["owners"].keys()
    return [_agent(e, sc["owners"]) for e in sorted(exts, key=lambda e: (len(e), e))]


def _agent(ext, owners):
    o = owners.get(ext) or {}
    return {"extension": ext, "user_id": o.get("id"), "name": o.get("name")}


def _agent_re():
    """The agent-channel pattern of the configured driver (group 1 = the extension)."""
    return AGENT_RES[channel_driver()]


def _trunk_leg(r, driver) -> bool:
    """A trunk leg of the live snapshot: a SIP/ channel (with chan_sip: one that is not an agent's)."""
    return (r["channel"] or "").startswith("SIP/") and not (driver == "SIP" and r["extension"])


INBOUND_CONTEXT_RE = re.compile(r"^(?:from-trunk|from-pstn|from-did|ext-did)")
LOCAL_HALF_RE = re.compile(r"^(Local/([^@;]+)@[^;]+);[12]$")


def _dialing(r) -> bool:
    """A channel the PBX is dialing out on (no dialplan application yet, not answered)."""
    return not (r["application"] or "") and (r["state"] or "") in ("Down", "Ringing")


def _sip_groups(rows) -> dict[str, list[dict]]:
    """chan_sip / Asterisk 11 grouping (the live snapshot has no Linkedid there, only two-party
    bridges): pseudo channels without a technology ("OutgoingSpoolFailed": the Issabel dialer's
    failed originates) are not calls, and the two halves of a Local/...;1 / ;2 channel (queues,
    the dialer, follow-me) join the groups on either side into one call; a Local/<ext>@... pair
    not bridged yet (a queue still ringing the agent) joins that extension's channel when there is
    exactly one. A group of Local/ channels only is plumbing, not a call. The call's id is the
    trunk leg's group when there is one, else the oldest group."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if "/" not in (r["channel"] or ""):
            continue
        groups.setdefault(r["linkedid"] or r["uniqueid"], []).append(r)
    parent = {k: k for k in groups}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    def union(x, y):
        a, b = find(x), find(y)
        if a != b:
            parent[b] = a
    halves: dict[str, str] = {}
    local_exts: dict[str, str] = {}
    agent_groups: dict[str, list[str]] = {}
    for key, legs in groups.items():
        for r in legs:
            m = LOCAL_HALF_RE.match(r["channel"] or "")
            if m:
                union(halves.setdefault(m.group(1), key), key)
                local_exts.setdefault(key, m.group(2))
            elif r["extension"]:
                agent_groups.setdefault(r["extension"], []).append(key)
    for key, ext in local_exts.items():
        if len(agent_groups.get(ext, [])) == 1:
            union(key, agent_groups[ext][0])
    merged: dict[str, list[dict]] = {}
    for key in groups:
        merged.setdefault(find(key), []).extend(groups[key])
    out = {}
    for legs in merged.values():
        if all((r["channel"] or "").startswith("Local/") for r in legs):
            continue
        trunk_keys = [r["linkedid"] or r["uniqueid"] for r in legs if _trunk_leg(r, "SIP")]
        out[min(trunk_keys) if trunk_keys else min(r["linkedid"] or r["uniqueid"] for r in legs)] = legs
    return out


# --------------------------------------------------------------------------- live
def live(user_id=None, extension=None, contract=None) -> dict:
    full = scope(user_id)
    base = narrow(full, None, contract)              # the user filter's choices follow the contract
    sc = narrow(base, extension)
    snap = sess().execute(text("""
        SELECT captured_at, ok, error, channel_count, extract(epoch FROM now() - captured_at) AS age
          FROM pbx_live_snapshot WHERE id = 1""")).mappings().first()
    rows = [dict(r) for r in sess().execute(text("SELECT * FROM pbx_live_channels ORDER BY linkedid, uniqueid")).mappings()]
    allowed = sc["extensions"]
    driver = channel_driver()
    if driver == "SIP":
        groups = _sip_groups(rows)
    else:
        groups = {}
        for r in rows:
            groups.setdefault(r["linkedid"] or r["uniqueid"], []).append(r)
    calls = []
    for linkedid, legs in groups.items():
        agents = [r for r in legs if r["extension"]]
        trunks = [r for r in legs if _trunk_leg(r, driver)]
        if allowed is not None and not any(r["extension"] in allowed for r in agents):
            continue                                   # IVR-only calls are SuperAdmin's
        first = next((r for r in legs if r["uniqueid"] == linkedid), legs[0])
        if driver == "SIP" and _trunk_leg(first, driver) and (
                _dialing(first) or not INBOUND_CONTEXT_RE.match(first["context"] or "")):
            # chan_sip: a trunk leg being dialed out, or one the dialer originated into its own
            # context (Issabel inbound trunk channels sit in from-trunk-... / from-pstn / from-did)
            direction = "outbound"
        elif _trunk_leg(first, driver):
            direction = "inbound"
        elif trunks:
            direction = "outbound"
        elif len(agents) >= 2:
            direction = "internal"
        else:
            direction = "outbound"
        bridged = [r for r in legs if r["bridge_id"]]
        ringing = [r for r in agents if (r["state"] or "") in ("Ring", "Ringing")]
        if len(bridged) >= 2:
            state = "talking"
        elif ringing:
            state = "ringing"
        elif direction == "inbound" and not agents:
            state = "ivr"
        else:
            state = "connecting"
        if direction == "inbound":
            external = first["caller_num"]
        elif trunks:
            external = trunks[0]["connected_num"] or trunks[0]["exten"] or trunks[0]["caller_num"]
        else:
            external = None
        on_call = [r for r in agents if r["bridge_id"]] or agents
        visible = [r for r in on_call if allowed is None or r["extension"] in allowed]
        calls.append({
            "id": linkedid, "direction": direction, "state": state, "external_number": external,
            "agents": [_agent(r["extension"], sc["owners"]) for r in visible],
            "ringing": [r["extension"] for r in ringing if allowed is None or r["extension"] in allowed],
            "duration_seconds": max((r["duration_s"] or 0) for r in legs),
            "where": first["application"] if state == "ivr" else None,
        })
    calls.sort(key=lambda c: -c["duration_seconds"])
    busy = {a["extension"] for c in calls if c["state"] == "talking" for a in c["agents"]}
    ringing_exts = {e for c in calls for e in c["ringing"]}
    exts = sorted(allowed if allowed is not None else sc["owners"].keys(), key=lambda e: (len(e), e))
    agents = [{**_agent(e, sc["owners"]), "status": "on_call" if e in busy else "ringing" if e in ringing_exts else "free"}
              for e in exts]
    age = float(snap["age"]) if snap else None
    return {
        "scope": sc["kind"], "extension": sc.get("filtered"), "options": options(base),
        "contract": sc.get("contract"), "contracts": contracts(full),
        "snapshot": {"captured_at": iso(snap["captured_at"]) if snap else None, "ok": bool(snap["ok"]) if snap else False,
                     "error": snap["error"] if snap else "no_snapshot", "age_seconds": round(age, 1) if age is not None else None,
                     "stale": age is None or age > 15 or not snap["ok"]},
        "summary": {"active_calls": len(calls), "talking": sum(c["state"] == "talking" for c in calls),
                    "ringing": sum(c["state"] == "ringing" for c in calls), "in_ivr": sum(c["state"] == "ivr" for c in calls),
                    "agents_on_call": len(busy), "agents": len(agents)},
        "calls": calls, "agents": agents,
    }


# --------------------------------------------------------------------------- calls by user over time
def classify(row) -> list[tuple[str, str, bool]]:
    """One CDR row -> [(extension, direction, answered)] for the agent extensions it involves."""
    agent_re = _agent_re()
    src = agent_re.match(row.get("channel") or "")
    dst = agent_re.match(row.get("dstchannel") or "")
    answered = (row.get("disposition") or "").upper() == "ANSWERED"
    if src and dst:
        return [(src.group(1), "internal", answered)]
    if src:
        return [(src.group(1), "outbound", answered)]
    if dst:
        return [(dst.group(1), "inbound", answered)]
    return []


def _date(value, field, default):
    if value in (None, ""):
        return default
    try:
        return _dt.date.fromisoformat(str(value))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be YYYY-MM-DD", {field: "invalid"}) from None


def _agent_filter(ext=None, driver="PJSIP") -> tuple[str, tuple]:
    """The CDR WHERE fragment (mysql-connector `%s` style) that keeps the agent rows: those of one
    already validated extension, or of any agent extension. chan_sip trunks are SIP/ too, so
    without `ext` the SIP form matches the agent channel pattern (REGEXP), not the prefix."""
    if driver == "SIP":
        if ext:
            return "(channel LIKE %s OR dstchannel LIKE %s)", (f"SIP/{ext}-%", f"SIP/{ext}-%")
        return "(channel REGEXP %s OR dstchannel REGEXP %s)", ("^SIP/[0-9]{2,6}-", "^SIP/[0-9]{2,6}-")
    if ext:
        return "(channel LIKE %s OR dstchannel LIKE %s)", (f"PJSIP/{ext}-%", f"PJSIP/{ext}-%")
    return "(channel LIKE 'PJSIP/%%' OR dstchannel LIKE 'PJSIP/%%')", ()


_CDR_HAS_LINKEDID: dict[str, bool] = {}


def _cdr_shape(cur) -> bool:
    """Whether asteriskcdrdb.cdr has `linkedid` (Asterisk 12+ CDR schema); cached per process.
    The Alitqan Issabel (Asterisk 11) has none: there one CDR row is one leg."""
    key = "asteriskcdrdb.cdr"
    if key not in _CDR_HAS_LINKEDID:
        cur.execute("SHOW COLUMNS FROM asteriskcdrdb.cdr LIKE %s", ("linkedid",))
        _CDR_HAS_LINKEDID[key] = bool(cur.fetchall())
    return _CDR_HAS_LINKEDID[key]


def _uniqueid_bounds(lo, hi) -> tuple[str, str]:
    """`uniqueid` range for an Asterisk <= 11 CDR (uniqueid = "<epoch>.<seq>", the only index; no
    Originate ChannelId there, so never "cawp-..."): the period in PBX local time (Asia/Riyadh)
    widened by an hour on each side. String comparison is exact while epochs have 10 digits."""
    def epoch(v):
        if v.tzinfo is None:
            v = v.replace(tzinfo=riyadh.RIYADH)
        return int(v.timestamp())
    return str(epoch(lo) - 3600), str(epoch(hi) + 3600)


def _cdr_rows(lo, hi, ext=None, number=None):
    """The agent CDR rows of the period; `ext` (an already validated extension) narrows them to the
    rows that involve it (the same calls: classify() only counts the extensions on a row);
    `number` (number_search.needle) to the rows whose src or dst carries it (refined after the
    merge by number_search.matches)."""
    from services import reports_v2_cdr as cdrsrc
    agent, params = _agent_filter(ext, channel_driver())
    try:
        conn = cdrsrc._connect()
    except cdrsrc.CdrUnavailable as e:
        raise ApiError(503, "cdr_unavailable", "the PBX call records are unavailable", {"reason": str(e)}) from None
    try:
        cur = conn.cursor(dictionary=True)
        try:
            if number:
                like = f"%{number}" if len(number) >= numsearch.KEY_DIGITS else f"%{number}%"
                agent, params = f"{agent} AND (src LIKE %s OR dst LIKE %s)", (*params, like, like)
            if _cdr_shape(cur):
                linked, bound, bparams = "linkedid", "", ()
            else:                                        # Asterisk <= 11: no linkedid column
                linked, bound, bparams = "uniqueid AS linkedid", "uniqueid >= %s AND uniqueid < %s AND ", \
                    _uniqueid_bounds(lo, hi)
            cur.execute(f"""
                SELECT calldate, channel, dstchannel, disposition, billsec, duration, {linked}, uniqueid,
                       src, dst, userfield, recordingfile, lastapp, dcontext
                  FROM asteriskcdrdb.cdr
                 WHERE {bound}calldate >= %s AND calldate < %s AND {agent}
                 ORDER BY calldate LIMIT %s""", (*bparams, lo, hi, *params, MAX_CDR_ROWS + 1))
            return cur.fetchall()
        finally:
            cur.close()
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ApiError(503, "cdr_unavailable", "the PBX call records are unavailable",
                       {"reason": type(e).__name__}) from None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _range(args):
    """(from, to, lo, hi) of the page's period: Asia/Riyadh days, default today, 31 days max."""
    today = riyadh.now().date()
    start = _date(args.get("from"), "from", today)
    end = _date(args.get("to"), "to", start)
    if end < start:
        raise ApiError(422, VALIDATION_FAILED, "to is before from", {"to": "before_from"})
    if (end - start).days >= MAX_DAYS:
        raise ApiError(422, VALIDATION_FAILED, f"at most {MAX_DAYS} days", {"to": "range_too_long"})
    lo = _dt.datetime.combine(start, _dt.time.min)                      # the PBX writes Riyadh wall clock
    hi = _dt.datetime.combine(end + _dt.timedelta(days=1), _dt.time.min)
    return start, end, lo, hi


def _rows_for(sc, lo, hi, rows, number=None):
    if rows is not None:
        return rows
    exts = sc["extensions"]
    if exts is not None and not exts:
        return []                                         # no extension in scope: nothing to read
    extra = {"number": number} if number else {}
    ext = next(iter(exts)) if exts is not None and len(exts) == 1 else None
    return _cdr_rows(lo, hi, ext, **extra) if ext else _cdr_rows(lo, hi, **extra)


def _over(seconds, answered=True) -> dict:
    """{"talk_over_60": 0|1, ...} for one call."""
    return {f"talk_over_{b}": int(bool(answered) and seconds > b) for b in TALK_BUCKETS}


def _outcome(c) -> str:
    """The chart outcome of one merged call: answered / no_answer / busy / failed / unknown (an
    empty or unexpected result stays unknown)."""
    if c["answered"]:
        return "answered"
    return {"NO ANSWER": "no_answer", "BUSY": "busy", "FAILED": "failed", "CONGESTION": "failed"}.get(c["result"], "unknown")


def _talk_bucket(seconds) -> int:
    """Index in TALK_DISTRIBUTION of an answered call's talk time (upper bound inclusive)."""
    for i, (_lo, top) in enumerate(TALK_DISTRIBUTION):
        if top is None or seconds <= top:
            return i
    return len(TALK_DISTRIBUTION) - 1


def stats(args, user_id=None, rows=None) -> dict:
    """Calls by user over time. Added for the charts (2026-10-20, every key additive):
    per user and in the totals `calls` (every call incl. missed rings), `answered` (any
    direction) and `ring_seconds` (sum over every call); in each series bucket `by_outcome`
    (_outcome) and `by_direction` over EVERY call (the bucket's `total` stays "handled");
    `talk_distribution` (answered calls by talk time, TALK_DISTRIBUTION, upper bound inclusive so
    the buckets above 60 s add up to talk_over_60); `heatmap` (handled calls by weekday, Sunday
    first, x hour of day, with the number of such days in the period); `number` (number_search)
    keeps only the calls with that client number, as on the calls page."""
    full = scope(user_id)
    base = narrow(full, None, args.get("contract"))
    sc = narrow(base, args.get("extension"))
    start, end, lo, hi = _range(args)
    gran = args.get("granularity") or ("hour" if start == end else "day")
    if gran not in ("hour", "day"):
        raise ApiError(422, VALIDATION_FAILED, "granularity must be hour or day", {"granularity": "invalid"})
    number = numsearch.needle(args.get("number"))
    cdr = _rows_for(sc, lo, hi, rows, number)
    calls = _merge(cdr, sc["extensions"])
    if number:
        calls = [c for c in calls if c["direction"] != "internal" and numsearch.matches(c["number"], number)]
    fmt = "%Y-%m-%d %H:00" if gran == "hour" else "%Y-%m-%d"
    buckets = []
    t = lo
    while t < hi:
        buckets.append(t.strftime(fmt))
        t += _dt.timedelta(hours=1) if gran == "hour" else _dt.timedelta(days=1)
    per_user: dict[str, dict] = {}
    series = {b: {"bucket": b, "total": 0, "by_extension": {}, "by_outcome": dict.fromkeys(OUTCOMES, 0),
                  "by_direction": dict.fromkeys(DIRECTIONS, 0)} for b in buckets}
    talk_dist = [0] * len(TALK_DISTRIBUTION)
    days = [0] * 7                                        # weekday, Sunday first
    d = start
    while d <= end:
        days[(d.weekday() + 1) % 7] += 1
        d += _dt.timedelta(days=1)
    heat = [[0] * 24 for _ in range(7)]
    for c in calls:
        ext, talk = c["extension"], c["talk_seconds"]
        u = per_user.setdefault(ext, {**_agent(ext, sc["owners"]), "outbound": 0, "outbound_answered": 0,
                                      "inbound_answered": 0, "inbound_missed": 0, "internal": 0, "talk_seconds": 0,
                                      **_over(0), "calls": 0, "answered": 0, "ring_seconds": 0})
        if c["direction"] == "outbound":
            u["outbound"] += 1
            u["outbound_answered"] += c["answered"]
            way = "outbound"
        elif c["direction"] == "inbound":
            way = "inbound_answered" if c["answered"] else "inbound_missed"
            u[way] += 1
        else:
            u["internal"] += 1
            way = "internal"
        u["talk_seconds"] += talk if c["answered"] else 0
        for k, v in _over(talk, c["answered"]).items():
            u[k] += v
        u["calls"] += 1
        u["answered"] += c["answered"]
        u["ring_seconds"] += c["ring_seconds"]
        if c["answered"]:
            talk_dist[_talk_bucket(talk)] += 1
        handled = c["direction"] != "inbound" or c["answered"]            # calls handled, not every ring
        if handled:
            heat[(c["at"].weekday() + 1) % 7][c["at"].hour] += 1
        b = series.get(c["at"].strftime(fmt))
        if b is not None:
            b["by_outcome"][_outcome(c)] += 1
            b["by_direction"][way] += 1
        if b is not None and handled:
            b["total"] += 1
            b["by_extension"][ext] = b["by_extension"].get(ext, 0) + 1
    users = sorted(per_user.values(), key=lambda u: -(u["outbound"] + u["inbound_answered"] + u["internal"]))
    for u in users:
        u["handled"] = u["outbound"] + u["inbound_answered"] + u["internal"]
    return {"scope": sc["kind"], "extension": sc.get("filtered"), "options": options(base),
            "contract": sc.get("contract"), "contracts": contracts(full),
            "from": start.isoformat(), "to": end.isoformat(), "granularity": gran, "number": number,
            "truncated": len(cdr) > MAX_CDR_ROWS, "users": users, "series": list(series.values()),
            "totals": {"handled": sum(u["handled"] for u in users), "outbound": sum(u["outbound"] for u in users),
                       "outbound_answered": sum(u["outbound_answered"] for u in users),
                       "inbound_answered": sum(u["inbound_answered"] for u in users),
                       "inbound_missed": sum(u["inbound_missed"] for u in users),
                       "talk_seconds": sum(u["talk_seconds"] for u in users),
                       **{k: sum(u[k] for u in users) for k in _over(0)},
                       **{k: sum(u[k] for u in users) for k in ("internal", "calls", "answered", "ring_seconds")}},
            "talk_distribution": [{"min": a, "max": b, "count": n} for (a, b), n in zip(TALK_DISTRIBUTION, talk_dist)],
            "heatmap": [{"weekday": i, "days": days[i], "hours": heat[i]} for i in range(7)]}


# --------------------------------------------------------------------------- call detail + downloads
DIRECTION_AR = {"outbound": "صادرة", "inbound": "واردة", "internal": "داخلية"}
RESULT_AR = {"ANSWERED": "تم الرد", "NO ANSWER": "لم يتم الرد", "BUSY": "مشغول", "FAILED": "فشل",
             "CONGESTION": "تعذر الاتصال"}
SCOPE_AR = {"all": "كل التحويلات", "team": "الفريق", "own": "تحويلتي"}
# the talk-time buckets (TALK_BUCKETS): summary = counts, call detail = yes/no per call
OVER_AR_SUMMARY = ("مكالمات تم الرد عليها أطول من دقيقة", "مكالمات تم الرد عليها أطول من دقيقتين",
                   "مكالمات تم الرد عليها أطول من 3 دقائق")
OVER_AR_DETAIL = ("محادثة أطول من دقيقة", "محادثة أطول من دقيقتين", "محادثة أطول من 3 دقائق")


def _other_party(r, direction):
    """(number, line) of one CDR row: the customer's number and the PBX line (caller ID / DID)."""
    src, dst = (r.get("src") or "").strip(), (r.get("dst") or "").strip()
    if direction == "outbound":
        return dst, src
    if direction == "inbound":
        uf = (r.get("userfield") or "").strip()
        did = uf.split(":")[1] if uf.startswith("ivr:") and uf.count(":") >= 1 else ""
        return src, did
    peer = _agent_re().match(r.get("dstchannel") or "")
    return (peer.group(1) if peer else dst), ""


def _disp(r) -> str:
    return (r.get("disposition") or "").strip().upper()


def _secs(r, field) -> int:
    return max(int(r.get(field) or 0), 0)


def _ignored(r) -> bool:
    """The answer macro an answering agent's channel runs in a ring-group call (dcontext macro-*,
    PJSIP/<ext> (or SIP/<ext>) -> Local/FMGL-...): not a call the agent placed."""
    return (r.get("dcontext") or "").startswith("macro-") and (r.get("dstchannel") or "").startswith("Local/")


def _attempt_key(rows) -> str | None:
    """`cawp-<attempt uuid>` (call_attempts.pbx_channel_id) of an app-dispatched call, else None."""
    for r in rows:
        for v in (r.get("userfield"), r.get("uniqueid"), r.get("linkedid")):
            m = CAWP_ID_RE.match((v or "").strip())
            if m:
                return f"cawp-{m.group(1)}"
    return None


def _attempts_for(keys) -> dict:
    """pbx_channel_id -> the call_attempts timestamps of those app-dispatched calls (one query)."""
    if not keys:
        return {}
    rows = sess().execute(text("""
        SELECT pbx_channel_id, customer_dialing_at, bridged_at, ended_at
          FROM call_attempts WHERE pbx_channel_id = ANY(CAST(:ids AS text[]))"""), {"ids": sorted(keys)}).mappings()
    return {r["pbx_channel_id"]: r for r in rows}


def _between(a, b) -> int:
    return max(int(round((b - a).total_seconds())), 0) if a is not None and b is not None else 0


def _merge(cdr, allowed) -> list[dict]:
    """The CDR rows merged into one item per call and agent ((linkedid, extension, direction)),
    oldest first, with the corrected result / answered / ring / talk (module docstring)."""
    groups: dict[tuple, list] = {}
    for r in cdr[:MAX_CDR_ROWS]:
        if _ignored(r):
            continue
        for ext, direction, _answered in classify(r):
            if allowed is not None and ext not in allowed:
                continue
            groups.setdefault((r.get("linkedid") or r.get("uniqueid"), ext, direction), []).append(r)
    keys = {key: _attempt_key(rows) for key, rows in groups.items()}
    attempts = _attempts_for({k for k in keys.values() if k})
    items = []
    for key, rows in groups.items():
        call_id, ext, direction = key
        dials = [r for r in rows if (r.get("dstchannel") or "").strip()]          # the customer / agent leg
        picked = [r for r in dials if _disp(r) == "ANSWERED"]
        if picked:
            best = max(picked, key=lambda r: _secs(r, "billsec"))
            answered, result, talk = True, "ANSWERED", _secs(best, "billsec")
            ring = max(_secs(best, "duration") - talk, 0)
        elif dials:
            answered, result, talk = False, _disp(dials[0]), 0
            ring = max(_secs(dials[0], "duration") - _secs(dials[0], "billsec"), 0)
        else:                                             # only post-dial rows: never answered
            answered, talk = False, 0
            result = next((_disp(r) for r in rows if _disp(r) and _disp(r) != "ANSWERED"), "")
            ring = max((_secs(r, "duration") - _secs(r, "billsec") for r in rows), default=0)
        a = attempts.get(keys[key])
        if a is not None:                                 # click-to-call: the app's own evidence
            answered = a["bridged_at"] is not None
            talk = _between(a["bridged_at"], a["ended_at"]) if answered else 0
            ring = _between(a["customer_dialing_at"], a["bridged_at"] or a["ended_at"])
            if answered:
                result = "ANSWERED"
            elif result == "ANSWERED":                    # the PBX says answered, no conversation evidence
                result = ""
        number = line = ""
        for r in rows:
            n, ln = _other_party(r, direction)
            number, line = number or n, line or ln
        recording = next(((r.get("recordingfile") or "").strip() for r in rows
                          if (r.get("recordingfile") or "").strip()), None)
        items.append({"at": min(r["calldate"] for r in rows), "extension": ext, "direction": direction,
                      "answered": answered, "result": result, "number": number, "line": line,
                      "talk_seconds": talk, "ring_seconds": ring, "recorded": recording is not None,
                      "recording": recording, "call_id": call_id})
    items.sort(key=lambda c: (c["at"], c["extension"]))
    return items


def _merged(args, user_id=None, rows=None):
    """(scope, start, end, truncated, items): one item per call and agent, oldest first; `number`
    (2026-10-19, number_search) keeps the calls with that client number (not internal calls)."""
    full = scope(user_id)
    sc = narrow(full, args.get("extension"), args.get("contract"))
    start, end, lo, hi = _range(args)
    number = numsearch.needle(args.get("number"))
    cdr = _rows_for(sc, lo, hi, rows, number)
    items = _merge(cdr, sc["extensions"])
    if number:
        items = [c for c in items if c["direction"] != "internal" and numsearch.matches(c["number"], number)]
    for c in items:
        c.update({k: v for k, v in _agent(c["extension"], sc["owners"]).items() if k != "extension"})
    return sc, start, end, len(cdr) > MAX_CDR_ROWS, items


def _with_clients(items, user_id=None):
    clients = _clients_for([c["number"] for c in items if c["direction"] != "internal"], user_id)
    for c in items:
        c["client"] = clients.get(c["number"]) if c["direction"] != "internal" else None
    return items


def calls(args, user_id=None, rows=None) -> dict:
    """One row per call and agent (the CDR rows of one linkedid merged), oldest first."""
    sc, start, end, truncated, items = _merged(args, user_id, rows)
    return {"scope": sc["kind"], "extension": sc.get("filtered"), "contract": sc.get("contract"),
            "from": start.isoformat(), "to": end.isoformat(),
            "number": numsearch.needle(args.get("number")), "truncated": truncated, "calls": _with_clients(items, user_id)}


def _page_arg(value, field, default, hi):
    if value in (None, ""):
        return default
    try:
        n = int(str(value))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be a whole number", {field: "invalid"}) from None
    if n < 1 or n > hi:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be 1..{hi}", {field: "out_of_range"})
    return n


def calls_page(args, user_id=None, rows=None) -> dict:
    """calls() newest first, one page (clients matched for that page only), with the period's
    talk-time counts of the same calls. Paged like the v2 lists: items, total, page, page_size."""
    page = _page_arg(args.get("page"), "page", 1, 1_000_000)
    size = _page_arg(args.get("page_size"), "page_size", PAGE_SIZE, MAX_PAGE_SIZE)
    sc, start, end, truncated, items = _merged(args, user_id, rows)
    items.reverse()
    chunk = _with_clients(items[(page - 1) * size: page * size], user_id)
    for c in chunk:                                       # the PBX writes Riyadh wall clock
        c["at"] = c["at"].replace(tzinfo=riyadh.RIYADH).isoformat(timespec="seconds") if c["at"].tzinfo is None \
            else c["at"].isoformat(timespec="seconds")
    return {"scope": sc["kind"], "extension": sc.get("filtered"), "contract": sc.get("contract"),
            "from": start.isoformat(), "to": end.isoformat(),
            "number": numsearch.needle(args.get("number")), "truncated": truncated, "items": chunk,
            "total": len(items), "page": page, "page_size": size}


def _clients_for(numbers, user_id=None) -> dict:
    """number -> {name, account_number} for the numbers that are a contact of a case the caller
    may see (one client per number: active before archived, the latest updated)."""
    from services import money
    nums = sorted({n for n in numbers if n and len(re.sub(r"[^0-9]", "", n)) >= 9})
    if not nums:
        return {}
    pred, params = money.scope_sql(money.caller_scope(str(user_id or me())), alias="k")
    rows = sess().execute(text(f"""
        SELECT DISTINCT ON (m.n) m.n, k.name, k."accountNumber"
          FROM unnest(CAST(:nums AS text[])) AS m(n)
          JOIN "ClintContacts" cc ON cc.is_deleted = false
                                 AND cawp_norm_mobile(cc.contact) = cawp_norm_mobile(m.n)
          JOIN clints k ON k."identityNumber" = cc."identityNumber"
         WHERE cawp_norm_mobile(m.n) IS NOT NULL AND {pred}
         ORDER BY m.n, k.is_archive ASC, k.update_at DESC NULLS LAST"""), {"nums": nums, **params}).all()
    return {r[0]: {"name": r[1], "account_number": r[2]} for r in rows}


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _who(sc_kind, ext, owners) -> str:
    if not ext:
        return SCOPE_AR.get(sc_kind, sc_kind)
    a = _agent(ext, owners)
    return f"{a['name'] or '—'} ({ext})"


def _info_sheet(wb, data, owners, kind_ar):
    sh = wb.add_sheet("معلومات")
    sh.append([("s", "البند"), ("s", "القيمة")], header=True)
    for label, value in (("التقرير", kind_ar), ("من", data["from"]), ("إلى", data["to"]),
                         ("الموظف", _who(data["scope"], data.get("extension"), owners)),
                         ("وقت الإنشاء (الرياض)", riyadh.now().strftime("%Y-%m-%d %H:%M")),
                         ("ملاحظة", "المكالمات تُنسب للمالك الحالي للتحويلة؛ الأوقات بتوقيت الرياض")):
        sh.append([("s", label), ("s", value)])
    if data.get("contract"):
        name = sess().execute(text("SELECT name FROM contracts WHERE id = CAST(:c AS uuid)"),
                              {"c": data["contract"]}).scalar()
        sh.append([("s", "العقد"), ("s", name or data["contract"])])
    if data.get("number"):
        sh.append([("s", "بحث برقم العميل (آخر 9 أرقام)"), ("s", data["number"])])
    if data.get("truncated"):
        sh.append([("s", "تنبيه"), ("s", f"الفترة فيها أكثر من {MAX_CDR_ROWS} سجل؛ قصّر الفترة")])


def export_summary(args, dest_dir: str, user_id=None, rows=None) -> tuple[str, str, int]:
    """XLSX: the per-user table with totals, the calls over time, and the period. -> (path, name, users)."""
    from core.reports.xlsx import Workbook
    data = stats({**args, "granularity": args.get("granularity") or None, "number": None},   # every call, as before
                 user_id=user_id, rows=rows)
    owners = scope(user_id)["owners"]
    name = f"call-monitor-summary-{data['from']}_{data['to']}{'-' + data['extension'] if data['extension'] else ''}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("ملخص الموظفين", freeze_header=True)
    sh.append([("s", h) for h in ("الموظف", "التحويلة", "المكالمات المنجزة", "صادرة", "صادرة تم الرد عليها",
                                  "واردة تم الرد عليها", "واردة فائتة", "داخلية", "مدة المحادثة",
                                  "مدة المحادثة (ثانية)") + OVER_AR_SUMMARY], header=True)
    for u in data["users"]:
        sh.append([("s", u["name"] or "—"), ("s", u["extension"]), ("int", u["handled"]), ("int", u["outbound"]),
                   ("int", u["outbound_answered"]), ("int", u["inbound_answered"]), ("int", u["inbound_missed"]),
                   ("int", u["internal"]), ("s", _clock(u["talk_seconds"])), ("int", u["talk_seconds"])]
                  + [("int", u[f"talk_over_{b}"]) for b in TALK_BUCKETS])
    tot = data["totals"]
    sh.append([("s", "الإجمالي"), None, ("int", tot["handled"]), ("int", tot["outbound"]),
               ("int", tot["outbound_answered"]), ("int", tot["inbound_answered"]), ("int", tot["inbound_missed"]),
               ("int", sum(u["internal"] for u in data["users"])), ("s", _clock(tot["talk_seconds"])),
               ("int", tot["talk_seconds"])] + [("int", tot[f"talk_over_{b}"]) for b in TALK_BUCKETS], header=True)
    ts = wb.add_sheet("حسب الوقت", freeze_header=True)
    exts = [u["extension"] for u in data["users"]]
    ts.append([("s", "الساعة" if data["granularity"] == "hour" else "اليوم"), ("s", "الإجمالي")]
              + [("s", f"{u['name'] or '—'} ({u['extension']})") for u in data["users"]], header=True)
    for b in data["series"]:
        ts.append([("s", b["bucket"]), ("int", b["total"])] + [("int", b["by_extension"].get(e, 0)) for e in exts])
    _info_sheet(wb, data, owners, "ملخص مكالمات الموظفين")
    wb.save()
    return path, name, len(data["users"])


def export_calls(args, dest_dir: str, user_id=None, rows=None) -> tuple[str, str, int]:
    """XLSX: one row per call (the call detail). -> (path, name, calls)."""
    from core.reports.xlsx import Workbook
    data = calls(args, user_id=user_id, rows=rows)
    owners = scope(user_id)["owners"]
    name = f"call-monitor-calls-{data['from']}_{data['to']}{'-' + data['extension'] if data['extension'] else ''}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("تفاصيل المكالمات", freeze_header=True)
    sh.append([("s", h) for h in ("التاريخ والوقت", "الموظف", "التحويلة", "الاتجاه", "الرقم", "العميل",
                                  "رقم الحساب", "الخط", "النتيجة", "مدة الرنين (ثانية)", "مدة المحادثة",
                                  "مدة المحادثة (ثانية)") + OVER_AR_DETAIL + ("مسجلة", "معرف المكالمة")],
              header=True)
    for c in data["calls"]:
        client = c["client"] or {}
        over = _over(c["talk_seconds"], c["answered"])
        sh.append([("datetime", c["at"]), ("s", c["name"] or "—"), ("s", c["extension"]),
                   ("s", DIRECTION_AR[c["direction"]]), ("s", c["number"] or None), ("s", client.get("name")),
                   ("s", client.get("account_number")), ("s", c["line"] or None),
                   ("s", RESULT_AR.get(c["result"], c["result"] or "—")), ("int", c["ring_seconds"]),
                   ("s", _clock(c["talk_seconds"])), ("int", c["talk_seconds"])]
                  + [("s", "نعم" if over[f"talk_over_{b}"] else "لا") for b in TALK_BUCKETS]
                  + [("s", "نعم" if c["recorded"] else "لا"), ("s", c["call_id"])])
    _info_sheet(wb, data, owners, "تفاصيل المكالمات")
    wb.save()
    return path, name, len(data["calls"])
