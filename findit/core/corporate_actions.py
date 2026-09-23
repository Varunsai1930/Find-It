"""Pure corporate-action candidate detection. No DB/IO.

Never auto-confirms: returns candidates with ratio evidence only.
"""

_TOL = 0.05
# Share-count multiples produced by common splits, bonuses and
# consolidations. The one list both detectors use (see active_weight).
SPLIT_RATIOS = (1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 10.0, 0.5, 0.2, 0.1)


def detect_candidate(prev_qty, prev_px, curr_qty, curr_px, tol: float = _TOL):
    """Return a candidate dict, or None.

    Flags when the qty ratio is near one of SPLIT_RATIOS AND the price ratio
    is approximately its inverse, within `tol` (default 5%) on both legs.
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

    for target in SPLIT_RATIOS:
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
