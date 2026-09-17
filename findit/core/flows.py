"""Pure flow vs price-effect decomposition. No DB/IO."""


def decompose(prev_qty, prev_val, curr_qty, curr_val) -> dict:
    """Decompose value change into flow + price effect (all in lakhs).

    flow = qty_delta * implied_px_curr where
      implied_px_curr = curr_val / curr_qty if curr_qty > 0 else 0
    new position (prev_qty<=0, curr_qty>0) -> flow = curr_val
    exited position (curr_qty<=0)          -> flow = -prev_val, price_effect = 0
    value_change = curr_val - prev_val
    price_effect = value_change - flow (0 for exits)
    """

    prev_qty = float(prev_qty)
    prev_val = float(prev_val)
    curr_qty = float(curr_qty)
    curr_val = float(curr_val)

    value_change = curr_val - prev_val

    # Both empty: nothing happened.
    if prev_qty <= 0 and curr_qty <= 0:
        return {
            "flow_lakhs": 0.0,
            "price_effect_lakhs": 0.0,
            "value_change_lakhs": float(value_change),
        }

    # Exited convention.
    if curr_qty <= 0:
        return {
            "flow_lakhs": float(-prev_val),
            "price_effect_lakhs": 0.0,
            "value_change_lakhs": float(value_change),
        }

    # New position convention.
    if prev_qty <= 0:
        flow = float(curr_val)
        price_effect = float(value_change - flow)
        return {
            "flow_lakhs": float(flow),
            "price_effect_lakhs": float(price_effect),
            "value_change_lakhs": float(value_change),
        }

    implied_px_curr = curr_val / curr_qty if curr_qty > 0 else 0.0
    qty_delta = curr_qty - prev_qty
    flow = qty_delta * implied_px_curr
    price_effect = value_change - flow
    return {
        "flow_lakhs": float(flow),
        "price_effect_lakhs": float(price_effect),
        "value_change_lakhs": float(value_change),
    }
