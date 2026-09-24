"""Phase 6 Q2: the evidence rule, review validation and the review metrics (review/phase6-quality.md).

A review has one result per template criterion:

    met | needs_improvement | not_applicable | insufficient_evidence

and every criterion needs a reason. Evidence items:

    {"type": "quote", "text": "...", "segment_ids": ["s3"]}          (segment_ids optional)
    {"type": "audio", "start": 12.5, "end": 18.0, "human_reviewed": true}

**The evidence rule** (docs/phase6-spec.md hard rule 5):

* `met` / `needs_improvement` need at least one valid evidence item: an exact transcript quotation
  (validated here against the SAVED transcript: verbatim in the cited segments, or anywhere in the
  transcript when no segment is cited; only Unicode NFC and runs of whitespace are normalised) or a
  human-reviewed audio timestamp inside the recording (audio still present).
* A criterion marked `human_listening_only` (audible delivery) needs a human-reviewed audio
  timestamp for `met` / `needs_improvement`: a quotation is not enough. AI can never give it.
* A criterion that compares a reviewer-supplied input (`requires_input`: the employee notes or the
  recorded action) cannot be `met` / `needs_improvement` when that input is empty.

Human saves are all-or-nothing: any violation answers 422 with the errors per criterion. An AI
draft is repaired instead: each bad item becomes `insufficient_evidence` with a validation note
(what the model said stays visible as `ai_result`), invalid quotations are dropped, missing
criteria are added as `insufficient_evidence`. Nothing is ever approved automatically.

**Metrics** (call-level findings, not an employee ranking):

    compliance = met / (met + needs_improvement)          None when nothing was assessed
    coverage   = (met + needs_improvement) / all criteria

An all-unknown review (no met / needs improvement) has no compliance percentage.
"""
from __future__ import annotations

import unicodedata

from core.api_response import ApiError

RESULTS = ("met", "needs_improvement", "not_applicable", "insufficient_evidence")
ASSESSED = ("met", "needs_improvement")
INPUTS = ("notes", "recorded_action")
MAX_EVIDENCE = 10
MAX_QUOTE = 1000
MIN_QUOTE = 2
MAX_REASON = 2000

LABELS = {
    "en": {"met": "Met", "needs_improvement": "Needs improvement", "not_applicable": "Not applicable",
           "insufficient_evidence": "Insufficient evidence"},
    "ar": {"met": "مستوفى", "needs_improvement": "يحتاج تحسيناً", "not_applicable": "لا ينطبق",
           "insufficient_evidence": "أدلة غير كافية"},
}
AI_NOTES = {
    "en": {"human_listening_only": "Human listening only: a reviewer must listen and cite an audio timestamp.",
           "reason_missing": "The AI draft gave no reason.",
           "not_returned": "The AI draft did not return this criterion.",
           "evidence_rule": "The AI finding had no valid transcript quotation, so it is insufficient evidence."},
    "ar": {"human_listening_only": "استماع بشري فقط: يجب على المراجع الاستماع وتحديد توقيت في التسجيل.",
           "reason_missing": "لم تذكر مسودة الذكاء الاصطناعي سبباً.",
           "not_returned": "لم تُرجع مسودة الذكاء الاصطناعي هذا المعيار.",
           "evidence_rule": "لم يتضمن حكم الذكاء الاصطناعي اقتباساً صحيحاً من النص، لذا فالنتيجة أدلة غير كافية."},
}


def norm(text: str) -> str:
    """Only NFC and whitespace runs are normalised: a paraphrase never matches."""
    return " ".join(unicodedata.normalize("NFC", text or "").split())


class Transcript:
    """The saved transcript a review is checked against (None text = no transcript / purged)."""

    def __init__(self, text: str | None, segments: list | None):
        self.text = text
        self.segments = list(segments or [])
        self.by_id = {s["id"]: i for i, s in enumerate(self.segments)}
        self._norm_text = norm(text) if text is not None else None
        self._norm_all = norm(" ".join(s.get("text") or "" for s in self.segments))

    @property
    def available(self) -> bool:
        return self.text is not None

    def find(self, quote: str, segment_ids) -> tuple[str | None, list]:
        """(error code or None, resolved segment ids) for one quotation."""
        if not self.available:
            return "no_transcript", []
        q = norm(quote)
        if len(q) < MIN_QUOTE:
            return "quote_too_short", []
        if segment_ids:
            if any(sid not in self.by_id for sid in segment_ids):
                return "unknown_segment", []
            idx = sorted({self.by_id[sid] for sid in segment_ids})
            joined = norm(" ".join(self.segments[i].get("text") or "" for i in idx))
            if q in joined:
                return None, [self.segments[i]["id"] for i in idx]
            return "quote_not_in_segments", []
        if q not in self._norm_text and q not in self._norm_all:
            return "quote_not_found", []
        return None, self._locate(q)

    def _locate(self, q: str) -> list:
        """The shortest run of consecutive segments containing the quote (for timestamps)."""
        segs = self.segments
        for size in range(1, min(len(segs), 6) + 1):
            for i in range(0, len(segs) - size + 1):
                if q in norm(" ".join(s.get("text") or "" for s in segs[i:i + size])):
                    return [s["id"] for s in segs[i:i + size]]
        return []

    def span(self, segment_ids) -> tuple:
        starts = [self.segments[self.by_id[s]].get("start") for s in segment_ids if s in self.by_id]
        ends = [self.segments[self.by_id[s]].get("end") for s in segment_ids if s in self.by_id]
        start = starts[0] if starts and all(x is not None for x in starts) else None
        end = ends[-1] if ends and all(x is not None for x in ends) else None
        return start, end


class Context:
    """Everything the evidence rule needs about the recording."""

    def __init__(self, *, transcript: Transcript, duration: float, audio_available: bool, inputs: dict):
        self.transcript = transcript
        self.duration = float(duration)
        self.audio_available = audio_available
        self.inputs = {k: bool((inputs.get(k) or "").strip()) for k in INPUTS}


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def check_evidence(ev, ctx: Context, *, origin: str) -> tuple[dict | None, str | None]:
    """(clean evidence item, None) or (None, error code)."""
    if not isinstance(ev, dict):
        return None, "invalid_evidence"
    kind = ev.get("type")
    if kind == "quote":
        text = ev.get("text")
        if not isinstance(text, str) or not text.strip():
            return None, "quote_required"
        if len(text) > MAX_QUOTE:
            return None, "quote_too_long"
        sids = ev.get("segment_ids") or []
        if not isinstance(sids, list) or not all(isinstance(x, str) for x in sids) or len(sids) > 20:
            return None, "invalid_segment_ids"
        err, resolved = ctx.transcript.find(text, sids)
        if err:
            return None, err
        start, end = ctx.transcript.span(resolved)
        return {"type": "quote", "text": text.strip(), "segment_ids": resolved, "start": start, "end": end}, None
    if kind == "audio":
        if origin != "human":
            return None, "ai_audio_evidence"
        if not ctx.audio_available:
            return None, "audio_unavailable"
        if ev.get("human_reviewed") is not True:
            return None, "audio_not_human_reviewed"
        start, end = _num(ev.get("start")), ev.get("end")
        end = None if end is None else _num(end)
        if start is None or start < 0 or start > ctx.duration:
            return None, "invalid_timestamp"
        if ev.get("end") is not None and (end is None or end < start or end > ctx.duration):
            return None, "invalid_timestamp"
        return {"type": "audio", "start": round(start, 3), "end": None if end is None else round(end, 3),
                "human_reviewed": True}, None
    return None, "invalid_evidence"


def check_item(item, criterion: dict, ctx: Context, *, origin: str) -> tuple[dict, list]:
    """(clean item, errors). The clean item keeps only the valid evidence."""
    errors = []
    result = item.get("result")
    if result not in RESULTS:
        errors.append("invalid_result")
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("reason_required")
        reason = ""
    elif len(reason) > MAX_REASON:
        errors.append("reason_too_long")
    evidence_in = item.get("evidence") if "evidence" in item else []
    if evidence_in is None:
        evidence_in = []
    if not isinstance(evidence_in, list) or len(evidence_in) > MAX_EVIDENCE:
        errors.append("invalid_evidence")
        evidence_in = []
    evidence = []
    for ev in evidence_in:
        clean, err = check_evidence(ev, ctx, origin=origin)
        if err:
            errors.append(err)
        else:
            evidence.append(clean)
    if result in ASSESSED:
        if criterion.get("human_listening_only"):
            if origin != "human":
                errors.append("human_listening_only")
            elif not any(e["type"] == "audio" for e in evidence):
                errors.append("audio_evidence_required")
        elif not evidence:
            errors.append("evidence_required")
        need = criterion.get("requires_input")
        if need in INPUTS and not ctx.inputs.get(need):
            errors.append("input_missing")
    clean = {"criterion_id": criterion["id"], "result": result, "reason": reason.strip(), "evidence": evidence}
    return clean, errors


def _index_items(items, template) -> tuple[dict, dict]:
    """criterion id -> item, plus structural errors (unknown / duplicate / missing)."""
    ids = [c["id"] for c in template["criteria"]]
    by_id, errors = {}, {}
    for item in items:
        if not isinstance(item, dict):
            errors.setdefault("_", []).append("invalid_item")
            continue
        cid = item.get("criterion_id")
        if cid not in ids:
            errors.setdefault("_", []).append("unknown_criterion")
            continue
        if cid in by_id:
            errors.setdefault(cid, []).append("duplicate_criterion")
            continue
        by_id[cid] = item
    for cid in ids:
        if cid not in by_id and cid not in errors:
            errors.setdefault(cid, []).append("criterion_missing")
    return by_id, errors


def validate_human(results, template: dict, ctx: Context) -> list:
    """Every criterion exactly once, all valid; else 422 with errors per criterion."""
    if not isinstance(results, list):
        raise ApiError(422, "validation_failed", "results must be a list with one item per criterion",
                       {"results": "invalid"})
    by_id, errors = _index_items(results, template)
    clean = []
    for c in template["criteria"]:
        item = by_id.get(c["id"])
        if item is None:
            continue
        ci, errs = check_item(item, c, ctx, origin="human")
        if errs:
            errors.setdefault(c["id"], []).extend(errs)
        clean.append(ci)
    if errors:
        raise ApiError(422, "evidence_rule_failed",
                       "every criterion needs a result and a reason; met or needs improvement need an exact "
                       "transcript quotation or a human-reviewed audio timestamp",
                       {"criteria": {k: sorted(set(v)) for k, v in errors.items()}})
    return clean


def validate_ai(items, template: dict, ctx: Context, language: str) -> tuple[list, list]:
    """Repair an AI draft: (results, validation notes). Never raises for content problems."""
    notes_text = AI_NOTES["ar" if language == "ar" else "en"]
    ids = [c["id"] for c in template["criteria"]]
    seen, validation = {}, []
    for item in items if isinstance(items, list) else []:
        cid = item.get("criterion_id") if isinstance(item, dict) else None
        if cid not in ids:
            validation.append({"criterion_id": None, "codes": ["unknown_criterion"]})
            continue
        if cid in seen:
            validation.append({"criterion_id": cid, "codes": ["duplicate_criterion"]})
            continue
        seen[cid] = item
    out = []
    for c in template["criteria"]:
        raw = seen.get(c["id"])
        if raw is None:
            out.append({"criterion_id": c["id"], "result": "insufficient_evidence", "reason": notes_text["not_returned"],
                        "evidence": [], "ai_result": None, "validation": ["not_returned"]})
            validation.append({"criterion_id": c["id"], "codes": ["not_returned"]})
            continue
        ai_result = raw.get("result")
        item = {"result": ai_result, "reason": raw.get("reason"),
                "evidence": [{"type": "quote", "text": q.get("text"), "segment_ids": q.get("segment_ids") or []}
                             for q in (raw.get("quotations") or []) if isinstance(q, dict)]}
        clean, errs = check_item(item, c, ctx, origin="ai")
        codes = list(dict.fromkeys(errs))
        if c.get("human_listening_only"):
            if ai_result != "insufficient_evidence" or clean["evidence"]:
                codes.append("human_listening_only")
            clean.update(result="insufficient_evidence", reason=notes_text["human_listening_only"], evidence=[])
        else:
            if "invalid_result" in codes:
                clean["result"] = "insufficient_evidence"
            if "reason_required" in codes:
                clean["reason"] = notes_text["reason_missing"]
                clean["result"] = "insufficient_evidence"
            if clean["result"] in ASSESSED and ({"evidence_required", "input_missing"} & set(codes)):
                clean["result"] = "insufficient_evidence"
                if "evidence_required" in codes:
                    clean["reason"] = f'{clean["reason"]} ({notes_text["evidence_rule"]})'.strip()
            clean["reason"] = clean["reason"][:MAX_REASON]
        clean["ai_result"] = ai_result if ai_result in RESULTS else None
        clean["validation"] = sorted(set(codes))
        if clean["validation"]:
            validation.append({"criterion_id": c["id"], "codes": clean["validation"]})
        out.append(clean)
    return out, validation


# ------------------------------------------------------------------------------ metrics
def _ratio(num: int, den: int) -> dict | None:
    if den <= 0:
        return None
    return {"numerator": num, "denominator": den, "ratio": round(num / den, 6), "percent": round(100 * num / den, 1)}


def counts(results) -> dict:
    c = {k: 0 for k in RESULTS}
    for r in results or []:
        if r.get("result") in c:
            c[r["result"]] += 1
    c["total"] = sum(c[k] for k in RESULTS)
    return c


def metrics_from_counts(c: dict) -> dict:
    assessed = c["met"] + c["needs_improvement"]
    return {
        "met": c["met"], "needs_improvement": c["needs_improvement"], "not_applicable": c["not_applicable"],
        "insufficient_evidence": c["insufficient_evidence"], "total": c["total"], "assessed": assessed,
        "has_percentage": assessed > 0,
        "compliance": _ratio(c["met"], assessed),
        "coverage": _ratio(assessed, c["total"]),
    }


def metrics(results) -> dict:
    return metrics_from_counts(counts(results))


__all__ = ["ASSESSED", "AI_NOTES", "Context", "LABELS", "RESULTS", "Transcript", "check_evidence", "check_item",
           "counts", "metrics", "metrics_from_counts", "norm", "validate_ai", "validate_human"]
