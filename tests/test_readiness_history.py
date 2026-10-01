import sqlite3

from findit.core.history import stock_history
from tests.test_web import _copy_db


def test_history_uses_one_cohort_and_never_fills_missing_month_with_zero(tmp_path):
    c = sqlite3.connect(_copy_db(tmp_path))
    h = stock_history(c, "INE002A01018", "2026-07", "2026-08")
    assert h["cohort"] == [1, 2, 3]
    assert [p["shares"] for p in h["points"]] == [210, 300]
    assert h["points"][1]["net_share_change"] == 90
    # September has no sources/comparison. It cannot manufacture exits.
    gap = stock_history(c, "INE002A01018", "2026-07", "2026-09")
    assert gap["cohort"] == [] and all(p["shares"] is None for p in gap["points"])
    c.execute("DELETE FROM scheme_month_status WHERE scheme_id=3 AND report_month='2026-07'")
    changed = stock_history(c, "INE002A01018", "2026-07", "2026-08")
    assert changed["cohort"] == [1, 2]
    assert [p["shares"] for p in changed["points"]] == [140, 210]
    unknown = stock_history(c, "INE123A01012", "2026-07", "2026-08")
    assert all(p["shares"] is None for p in unknown["points"])
