"""Pure corporate-action candidate detection. No DB/IO.

Never auto-confirms: returns candidates with ratio evidence only.
"""

_TOL = 0.05
_TARGETS = (2.0, 0.5)


def adjust_quantity(qty, ratio):
    """Pure helper: scale a quantity by a corporate-action ratio."""
    return qty * ratio


def detect_candidate(prev_qty, prev_px, curr_qty, curr_px, tol: float = _TOL):
    """Return a candidate dict, or None.

    Flags when qty ratio ~= 2x (or 0.5x) AND price ratio is approximately
    the inverse (1/target) within `tol` (default 5%) on both legs.
    """

    try:
        pq = float(prev_qty)
        pp = float(prev_px)
        cq = float(curr_qty)
        cp = float(curr_px)
        t = float(tol)
    except (TypeError, ValueError):
        return None

    if pq <= 0 or pp <= 0 or cq <= 0 or cp <= 0:
        return None
    if t < 0:
        return None

    qty_ratio = cq / pq
    price_ratio = cp / pp

    for target in _TARGETS:
        expected_px = 1.0 / target
        if abs(qty_ratio - target) / target <= t and abs(price_ratio - expected_px) / expected_px <= t:
            return {
                "type": "split_candidate",
                "qty_ratio": float(qty_ratio),
                "price_ratio": float(price_ratio),
                "target_qty_ratio": float(target),
                "expected_price_ratio": float(expected_px),
                "tolerance": float(t),
            }
    return None
