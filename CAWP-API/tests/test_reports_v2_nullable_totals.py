"""A report total marked nullable (averages, min/max, unknown durations) stays null when the
aggregate is NULL; ordinary sums over an empty set still read 0."""
from types import SimpleNamespace

from core.reports.definition import Total
from core.reports.engine import format_totals


def test_nullable_total_stays_unknown():
    report = SimpleNamespace(totals=[
        Total("billed", "Billed", "المفوتر", "sum(x)", type="int", nullable=True),
        Total("amount", "Amount", "المبلغ", "sum(y)", type="money"),
        Total("avg_amount", "Average", "المتوسط", "avg(y)", type="money", nullable=True),
    ])
    out = format_totals(report, {"count": 0, "billed": None, "amount": None, "avg_amount": None})
    assert out == {"count": 0, "billed": None, "amount": "0.00", "avg_amount": None}
    out = format_totals(report, {"count": 2, "billed": 90, "amount": 10, "avg_amount": 5})
    assert out["billed"] == 90 and out["amount"] == "10.00" and out["avg_amount"] == "5.00"
