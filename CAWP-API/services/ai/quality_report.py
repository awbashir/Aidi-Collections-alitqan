"""Phase 6 Q2: the downloadable, printable HTML quality report (Arabic RTL or English).

    GET /v2/quality/recordings/<id>/report?language=ar|en[&approval_id=<id>]

* Without `approval_id`: the current approval when there is one, otherwise the current review
  (an AI draft or a human draft), labelled as a DRAFT awaiting human review/approval.
* With `approval_id`: that approval's immutable snapshot (labelled "superseded" when it is no
  longer the recording's current approval).

The report states its status, the provenance of the audio, transcript, template and review
(AI model and provider-call id, or the human reviewer), the metrics with their exact counts, and
every criterion with its result, reason and evidence (verbatim quotations with segment
timestamps, human-reviewed audio timestamps). It is self-contained (no scripts, no external
resources) and escapes every value. Call-level findings, not an employee ranking.
"""
from __future__ import annotations

import datetime as _dt
import html

from core.api_response import ApiError
from services.ai import quality as svc
from services.ai import quality_evidence as qe

T = {
    "en": {
        "brand": "Aidi Collections · Quality review report", "status": "Status",
        "approved": "APPROVED after human review", "superseded": "APPROVED (superseded: the evidence changed later)",
        "ai_draft": "DRAFT: AI draft awaiting human review. Not approved.",
        "human_draft": "DRAFT: human review saved, awaiting approval. Not approved.",
        "recording": "Recording", "source": "Source", "import": "Imported recording",
        "pbx_call": "Copied from a call attempt's PBX recording", "attempt": "Call attempt", "case": "Case",
        "duration": "Duration", "seconds": "s", "sha": "Audio SHA-256", "audio_purged": "audio removed by retention",
        "template": "Template", "version": "version", "origin": "Template origin",
        "transcript": "Transcript", "t_provider": "Provider transcription", "t_manual": "Manual transcript",
        "t_correction": "Human correction", "t_none": "No transcript", "t_purged": "transcript text removed by retention",
        "speakers": "Speaker labels", "anonymous": "anonymous (not verified)", "verified": "verified",
        "review": "Review", "r_ai": "AI draft", "r_human": "Human review", "model": "model", "call": "provider call",
        "saved_by": "saved by", "revision": "Revision", "approval": "Approval", "approved_by": "approved by",
        "none": "—", "metrics": "Metrics", "compliance": "Compliance (met / (met + needs improvement))",
        "coverage": "Coverage ((met + needs improvement) / all criteria)",
        "no_pct": "No percentage: no criterion was met or needs improvement.",
        "counts": "Counts", "disclaimer": ("Call-level quality findings, not an employee ranking or an employment "
                                           "decision. Not applicable and insufficient evidence are excluded from the "
                                           "compliance denominator and shown separately."),
        "summary": "Summary", "criteria": "Criteria", "criterion": "Criterion", "result": "Result",
        "evidence": "Evidence and reason", "reason": "Reason", "quote": "Transcript quotation",
        "audio_ts": "Human-reviewed audio timestamp", "segments": "segments", "hlo": "human listening only",
        "conditional": "conditional", "ai_said": "AI draft said", "validation": "server validation",
        "coaching": "Coaching plan", "generated": "Generated", "by": "by",
        "draft_banner": "This report is a draft. AI output is never a finding until a human approves it.",
        "inputs": "Reviewer-supplied evidence", "employee": "Employee (verified label)", "notes": "Employee notes",
        "action": "Recorded action", "context": "Context",
    },
    "ar": {
        "brand": "عايدي للتحصيل · تقرير مراجعة الجودة", "status": "الحالة",
        "approved": "معتمد بعد مراجعة بشرية", "superseded": "معتمد (سابق: تغيّرت الأدلة لاحقاً)",
        "ai_draft": "مسودة: مسودة ذكاء اصطناعي بانتظار المراجعة البشرية. غير معتمدة.",
        "human_draft": "مسودة: مراجعة بشرية محفوظة بانتظار الاعتماد. غير معتمدة.",
        "recording": "التسجيل", "source": "المصدر", "import": "تسجيل مستورد",
        "pbx_call": "نسخة من تسجيل مقسم لمحاولة اتصال", "attempt": "محاولة الاتصال", "case": "العميل",
        "duration": "المدة", "seconds": "ث", "sha": "بصمة الصوت SHA-256", "audio_purged": "حُذف الصوت وفق سياسة الاحتفاظ",
        "template": "القالب", "version": "الإصدار", "origin": "مصدر القالب",
        "transcript": "التفريغ النصي", "t_provider": "تفريغ آلي من المزود", "t_manual": "تفريغ يدوي",
        "t_correction": "تصحيح بشري", "t_none": "لا يوجد تفريغ", "t_purged": "حُذف نص التفريغ وفق سياسة الاحتفاظ",
        "speakers": "المتحدثون", "anonymous": "مجهولون (غير متحقق منهم)", "verified": "متحقق منه",
        "review": "المراجعة", "r_ai": "مسودة ذكاء اصطناعي", "r_human": "مراجعة بشرية", "model": "النموذج",
        "call": "طلب المزود", "saved_by": "حفظها", "revision": "المراجعة رقم", "approval": "الاعتماد",
        "approved_by": "اعتمدها", "none": "—", "metrics": "المؤشرات",
        "compliance": "نسبة الاستيفاء (مستوفى / (مستوفى + يحتاج تحسيناً))",
        "coverage": "نسبة التغطية ((مستوفى + يحتاج تحسيناً) / جميع المعايير)",
        "no_pct": "لا توجد نسبة: لم يُحكم على أي معيار بمستوفى أو يحتاج تحسيناً.",
        "counts": "الأعداد", "disclaimer": ("نتائج جودة على مستوى المكالمة، وليست ترتيباً للموظفين ولا قراراً وظيفياً. "
                                            "لا ينطبق والأدلة غير الكافية مستبعدة من مقام نسبة الاستيفاء وتظهر منفصلة."),
        "summary": "الملخص", "criteria": "المعايير", "criterion": "المعيار", "result": "النتيجة",
        "evidence": "الدليل والسبب", "reason": "السبب", "quote": "اقتباس من التفريغ",
        "audio_ts": "توقيت صوتي راجعه إنسان", "segments": "المقاطع", "hlo": "استماع بشري فقط",
        "conditional": "مشروط", "ai_said": "حكم مسودة الذكاء الاصطناعي", "validation": "تحقق الخادم",
        "coaching": "خطة التدريب", "generated": "أُنشئ", "by": "بواسطة",
        "draft_banner": "هذا التقرير مسودة. مخرجات الذكاء الاصطناعي ليست نتيجة حتى يعتمدها إنسان.",
        "inputs": "الأدلة المدخلة من المراجع", "employee": "الموظف (اسم متحقق منه)", "notes": "ملاحظات الموظف",
        "action": "الإجراء المسجل", "context": "السياق",
    },
}


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _ts(v) -> str:
    if v is None:
        return "…"
    v = float(v)
    return f"{int(v // 60)}:{v % 60:06.3f}"


def _gather(rec_id, approval_id) -> dict:
    """Everything the report shows, from a snapshot or from the current records."""
    rec = svc._load(rec_id)
    if approval_id is None and rec["current_approval_id"]:
        approval_id = str(rec["current_approval_id"])
    if approval_id is not None:
        a = svc._one("qc_approvals", approval_id)
        if a is None or str(a["recording_id"]) != str(rec["id"]):
            raise ApiError(404, "approval_not_found", "approval not found", {"approval_id": str(approval_id)})
        snap = a["snapshot"]
        current = rec["current_approval_id"] is not None and str(rec["current_approval_id"]) == str(a["id"])
        return {"status": "approved" if current else "superseded", "rec": rec, "snapshot": snap,
                "template": snap["template"], "transcript": snap["transcript"], "review": snap["review"],
                "evidence": snap["evidence"], "recording": snap["recording"], "metrics": snap["metrics"],
                "approval": {"id": str(a["id"]), **snap["approval"]}, "revision": a["approved_revision"]}
    rv = svc._one("qc_reviews", rec["current_review_id"])
    if rv is None:
        raise ApiError(422, "review_required", "create a review before downloading a report", {"review": "required"})
    names = svc._users([rv["created_by"]])
    t = svc._one("qc_transcripts", rv["transcript_id"])
    review = svc.review_public(rv, names)
    public = svc.recording_public(rec)
    return {"status": review["status"], "rec": rec, "snapshot": None,
            "template": svc.get_template(rv["template_key"], rv["template_version"]),
            "transcript": svc.transcript_public(t, svc._users([t["created_by"]])) if t else None,
            "review": review, "evidence": public["evidence"],
            "recording": {"id": public["id"], "title": public["title"], "source": public["source"],
                          "audio": public["audio"], "case_id": public["link"]["case_id"],
                          "attempt_id": public["link"]["attempt_id"], "provenance": public["provenance"]},
            "metrics": review["metrics"], "approval": None, "revision": rec["revision"]}


def _metrics_html(m: dict, t: dict, labels: dict) -> str:
    comp, cov = m.get("compliance"), m.get("coverage")
    comp_s = (f"<strong>{comp['percent']}%</strong> ({comp['numerator']}/{comp['denominator']})" if comp
              else f"<strong>{e(t['no_pct'])}</strong>")
    cov_s = f"{cov['percent']}% ({cov['numerator']}/{cov['denominator']})" if cov else t["none"]
    counts = " · ".join(f"{e(labels[k])}: {m[k]}" for k in qe.RESULTS)
    return (f"<section class='metrics'><h2>{e(t['metrics'])}</h2>"
            f"<p>{e(t['compliance'])}: {comp_s}</p><p>{e(t['coverage'])}: {cov_s}</p>"
            f"<p>{e(t['counts'])}: {counts} ({m['total']})</p><p class='muted'>{e(t['disclaimer'])}</p></section>")


def _evidence_html(item: dict, t: dict) -> str:
    parts = []
    for ev in item.get("evidence") or []:
        if ev.get("type") == "quote":
            when = ""
            if ev.get("start") is not None or ev.get("end") is not None:
                when = f" · {_ts(ev.get('start'))}–{_ts(ev.get('end'))}"
            segs = ", ".join(ev.get("segment_ids") or [])
            parts.append(f"<blockquote dir='auto'>{e(ev.get('text'))}</blockquote>"
                         f"<small>{e(t['quote'])}{(' · ' + e(t['segments']) + ' ' + e(segs)) if segs else ''}"
                         f"{e(when)}</small>")
        elif ev.get("type") == "audio":
            parts.append(f"<p class='audio'>{e(t['audio_ts'])}: {e(_ts(ev.get('start')))}–{e(_ts(ev.get('end')))}</p>")
    parts.append(f"<p dir='auto'><b>{e(t['reason'])}:</b> {e(item.get('reason'))}</p>")
    if item.get("validation"):
        said = item.get("ai_result")
        parts.append(f"<small class='warn'>{e(t['ai_said'])}: {e(said or t['none'])} · {e(t['validation'])}: "
                     f"{e(', '.join(item['validation']))}</small>")
    return "".join(parts)


def render(rec_id, language: str, approval_id=None, *, generated_by: str | None = None) -> tuple[str, dict]:
    """(HTML text, metadata for the audit)."""
    if language not in T:
        raise ApiError(422, "validation_failed", "language must be ar or en", {"language": "invalid"})
    d = _gather(rec_id, approval_id)
    t, labels, ar = T[language], qe.LABELS[language], language == "ar"
    tpl, rv, tr, recd, ev = d["template"], d["review"], d["transcript"], d["recording"], d["evidence"]
    crit = {c["id"]: c for c in tpl["criteria"]}
    status = d["status"]
    audio = recd.get("audio") or {}
    source = t["pbx_call"] if recd.get("source") == "pbx_call" else t["import"]
    rows = []
    for item in rv["results"]:
        c = crit.get(item["criterion_id"], {})
        tags = []
        if c.get("human_listening_only"):
            tags.append(t["hlo"])
        if c.get("conditional"):
            tags.append(t["conditional"])
        tag_html = "".join(f"<span class='tag'>{e(x)}</span>" for x in tags)
        rows.append(f"<tr class='r-{e(item['result'])}'><td><b>{e(c.get('ar' if ar else 'en') or item['criterion_id'])}"
                    f"</b><br><small>{e(item['criterion_id'])}</small> {tag_html}</td>"
                    f"<td class='res'>{e(labels.get(item['result'], item['result']))}</td>"
                    f"<td>{_evidence_html(item, t)}</td></tr>")
    if tr is None:
        tr_html = e(t["t_none"])
    else:
        kind = {"provider": t["t_provider"], "manual": t["t_manual"], "correction": t["t_correction"]}[tr["source"]]
        bits = [f"{kind} · {t['version']} {tr['version']}"]
        if tr.get("model"):
            bits.append(f"{t['model']}: {tr['model']}")
        if tr.get("provider_call_id"):
            bits.append(f"{t['call']}: {tr['provider_call_id']}")
        if tr.get("purged"):
            bits.append(t["t_purged"])
        spk = tr.get("speakers") or {}
        labels_list = tr.get("speaker_labels") or []
        if labels_list:
            bits.append(t["speakers"] + ": " + ", ".join(
                f"{lb} ({t['verified']}: {spk[lb]['role']})" if lb in spk else f"{lb} ({t['anonymous']})"
                for lb in labels_list))
        tr_html = e(" · ".join(bits))
    review_kind = t["r_ai"] if rv["origin"] == "ai" else t["r_human"]
    rv_bits = [f"{review_kind} · {t['version']} {rv['version']}"]
    if rv.get("model"):
        rv_bits.append(f"{t['model']}: {rv['model']}")
    if rv.get("provider_call_id"):
        rv_bits.append(f"{t['call']}: {rv['provider_call_id']}")
    if rv.get("created_by"):
        rv_bits.append(f"{t['saved_by']}: {rv['created_by'].get('name') or rv['created_by']['id']}")
    rv_bits.append(str(rv.get("created_at") or ""))
    ap = d["approval"]
    ap_html = t["none"]
    if ap:
        who = ap.get("approved_by") or {}
        ap_html = f"{t['approved_by']}: {who.get('name') or who.get('id')} · {ap.get('approved_at')} · " \
                  f"{t['revision']} {ap.get('approved_revision')} · id {ap['id']}"
    banner = "" if status in ("approved", "superseded") else f"<p class='draft'>{e(t['draft_banner'])}</p>"
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=3))).strftime("%Y-%m-%d %H:%M (Asia/Riyadh)")
    link = []
    if recd.get("attempt_id"):
        link.append(f"{t['attempt']}: {recd['attempt_id']}")
    if recd.get("case_id"):
        link.append(f"{t['case']}: {recd['case_id']}")
    audio_line = f"{t['duration']}: {audio.get('duration_seconds')} {t['seconds']} · {audio.get('format')} · " \
                 f"{t['sha']}: {audio.get('sha256')}"
    if audio.get("available") is False or audio.get("purged_at"):
        audio_line += f" · {t['audio_purged']}"
    doc = f"""<!doctype html>
<html lang="{language}" dir="{'rtl' if ar else 'ltr'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(recd.get('title'))} · QC</title>
<style>
body{{font:14px/1.7 system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif;color:#23232f;max-width:1050px;margin:32px auto;padding:0 24px}}
header{{border-bottom:3px solid #5b4bc4;padding-bottom:16px;margin-bottom:16px}} h1{{font-size:26px;margin:4px 0}}
.brand{{color:#5b4bc4;font-weight:600;letter-spacing:.02em}} .status{{font-size:16px;font-weight:700}}
.status.approved{{color:#1d7a46}} .status.superseded{{color:#8a6d00}} .status.draft{{color:#b3261e}}
.draft{{background:#fdecea;border:1px solid #f5c2bd;padding:8px 12px}} dl{{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px}}
dt{{font-weight:600;color:#555}} dd{{margin:0;overflow-wrap:anywhere}} table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border-bottom:1px solid #ddd;padding:10px;text-align:start;vertical-align:top}} tr{{break-inside:avoid}}
blockquote{{background:#f4f2fb;margin:4px 0;padding:8px 12px;border-inline-start:3px solid #5b4bc4}}
.tag{{display:inline-block;font-size:11px;background:#eee;border-radius:8px;padding:0 6px;margin-inline-start:4px}}
.res{{white-space:nowrap;font-weight:600}} .muted,small{{color:#666}} .warn{{color:#8a6d00}}
@media print{{body{{margin:0;max-width:none}} .draft{{border:2px solid #b3261e}}}}
</style></head><body>
<header><div class="brand">{e(t['brand'])}</div><h1 dir="auto">{e(recd.get('title'))}</h1>
<p class="status {'draft' if status in ('ai_draft', 'human_draft') else e(status)}">{e(t['status'])}: {e(t[status])}</p>{banner}</header>
<dl>
<dt>{e(t['recording'])}</dt><dd>{e(recd.get('id'))} · {e(source)}{(' · ' + e(' · '.join(link))) if link else ''}</dd>
<dt>{e(t['source'])}</dt><dd>{e(audio_line)}</dd>
<dt>{e(t['template'])}</dt><dd>{e(tpl['name_ar'] if ar else tpl['name_en'])} · {e(tpl['key'])} · {e(t['version'])} {e(tpl['version'])}</dd>
<dt>{e(t['origin'])}</dt><dd>{e(tpl['origin_ar'] if ar else tpl['origin_en'])}</dd>
<dt>{e(t['transcript'])}</dt><dd>{tr_html}</dd>
<dt>{e(t['review'])}</dt><dd>{e(' · '.join(rv_bits))}</dd>
<dt>{e(t['approval'])}</dt><dd>{e(ap_html)}</dd>
<dt>{e(t['revision'])}</dt><dd>{e(d['revision'])}</dd>
</dl>
<section><h2>{e(t['inputs'])}</h2><dl>
<dt>{e(t['employee'])}</dt><dd dir="auto">{e(ev.get('employee_label') or t['none'])}</dd>
<dt>{e(t['notes'])}</dt><dd dir="auto">{e(ev.get('notes') or t['none'])}</dd>
<dt>{e(t['action'])}</dt><dd dir="auto">{e(ev.get('recorded_action') or t['none'])}</dd>
<dt>{e(t['context'])}</dt><dd dir="auto">{e(ev.get('context') or t['none'])}</dd>
</dl></section>
{_metrics_html(d['metrics'], t, labels)}
<section><h2>{e(t['summary'])}</h2><p dir="auto">{e(rv.get('summary') or t['none'])}</p></section>
<section><h2>{e(t['criteria'])}</h2><table><thead><tr><th>{e(t['criterion'])}</th><th>{e(t['result'])}</th>
<th>{e(t['evidence'])}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section><h2>{e(t['coaching'])}</h2><p dir="auto">{e(rv.get('coaching') or t['none'])}</p></section>
<footer><p class="muted">{e(t['disclaimer'])}</p><small>{e(t['generated'])}: {e(now)}{(' · ' + e(t['by']) + ' ' + e(generated_by)) if generated_by else ''}</small></footer>
</body></html>
"""
    meta = {"language": language, "status": status, "approval_id": ap["id"] if ap else None,
            "review_id": rv["id"], "revision": d["revision"], "bytes": len(doc.encode("utf-8"))}
    return doc, meta
