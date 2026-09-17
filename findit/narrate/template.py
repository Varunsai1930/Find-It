"""Deterministic summary template for future LLM narration input.

``render_summary`` is a pure function: no DB, no IO, no randomness, no
network.  It formats only pre-validated numbers supplied in ``payload`` —
it never computes flows, NAV percentages, or consensus itself.  The future
LLM narrator must narrate exactly these numbers, never recompute them.

Payload schema (all numbers validated, never guessed):
  scheme_name: str (required)
  amc_name: str (required)
  report_month: str YYYY-MM (required)
  new_count, added_count, trimmed_count: int >= 0 (default 0)
  new_largest_name, added_largest_name, trimmed_largest_name: str | None
  new_largest_value_cr, new_largest_flow_cr, new_largest_nav_pct: float | None
  added_largest_flow_cr, added_largest_value_cr: float | None
  trimmed_largest_flow_cr, trimmed_largest_value_cr: float | None
  exited_names: list[str] (default [])
  non_equity_note: str | None (optional separate sentence)

Money is formatted sign-aware ('+₹X.X Cr' / '-₹X.X Cr'); the output never
contains '+₹-' (use absolute values with an explicit sign).
Exits are capped at 5 names + 'and N more'.
"""

from __future__ import annotations

import math


def _req_str(payload: dict, key: str) -> str:
    val = payload.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"payload[{key!r}] must be a non-empty str")
    return val


def _opt_str(payload: dict, key: str) -> str | None:
    val = payload.get(key)
    if val is None:
        return None
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"payload[{key!r}] must be a str or None")
    return val


def _count(payload: dict, key: str) -> int:
    val = payload.get(key, 0)
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        raise ValueError(f"payload[{key!r}] must be an int >= 0")
    return val


def _opt_float(payload: dict, key: str) -> float | None:
    val = payload.get(key)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"payload[{key!r}] must be a number or None")
    f = float(val)
    if not math.isfinite(f):
        raise ValueError(f"payload[{key!r}] must be finite")
    return f


def _fmt_cr(cr: float) -> str:
    sign = "+" if cr >= 0 else "-"
    return f"{sign}₹{abs(cr):.1f} Cr"


def render_summary(payload: dict) -> str:
    """Render a deterministic plain-English summary from validated numbers."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    scheme_name = _req_str(payload, "scheme_name")
    amc_name = _req_str(payload, "amc_name")
    report_month = _req_str(payload, "report_month")

    new_count = _count(payload, "new_count")
    added_count = _count(payload, "added_count")
    trimmed_count = _count(payload, "trimmed_count")

    new_name = _opt_str(payload, "new_largest_name")
    added_name = _opt_str(payload, "added_largest_name")
    trimmed_name = _opt_str(payload, "trimmed_largest_name")

    new_value_cr = _opt_float(payload, "new_largest_value_cr")
    new_flow_cr = _opt_float(payload, "new_largest_flow_cr")
    new_nav_pct = _opt_float(payload, "new_largest_nav_pct")
    added_flow_cr = _opt_float(payload, "added_largest_flow_cr")
    added_value_cr = _opt_float(payload, "added_largest_value_cr")
    trimmed_flow_cr = _opt_float(payload, "trimmed_largest_flow_cr")
    trimmed_value_cr = _opt_float(payload, "trimmed_largest_value_cr")

    exited = payload.get("exited_names", [])
    if exited is None:
        exited = []
    if not isinstance(exited, list) or any(
        not isinstance(n, str) or not n.strip() for n in exited
    ):
        raise ValueError("payload['exited_names'] must be a list[str]")
    non_equity_note = _opt_str(payload, "non_equity_note")

    lines = [f"{scheme_name} ({amc_name}) — {report_month} portfolio changes:"]

    if new_count > 0 and new_name is not None:
        if new_value_cr is None or new_nav_pct is None:
            raise ValueError("new largest requires new_largest_value_cr + new_largest_nav_pct")
        detail = f"({_fmt_cr(new_flow_cr)} flow, {_fmt_cr(new_value_cr)} value change)" if new_flow_cr is not None else f"({_fmt_cr(new_value_cr)})"
        lines.append(
            f"- Opened {new_count} new position(s). The largest new entry was "
            f"{new_name}, a ₹{new_value_cr:.1f} Cr position "
            f"({new_nav_pct:.2f}% of NAV) {detail}."
        )
    elif new_count > 0:
        lines.append(f"- Opened {new_count} new position(s).")

    if added_count > 0 and added_name is not None:
        if added_flow_cr is None or added_value_cr is None:
            raise ValueError("added largest requires added_largest_flow_cr + added_largest_value_cr")
        lines.append(
            f"- Added to {added_count} existing position(s). The biggest addition was "
            f"{added_name} ({_fmt_cr(added_flow_cr)} flow, {_fmt_cr(added_value_cr)} value change)."
        )
    elif added_count > 0:
        lines.append(f"- Added to {added_count} existing position(s).")

    if trimmed_count > 0 and trimmed_name is not None:
        if trimmed_flow_cr is None or trimmed_value_cr is None:
            raise ValueError("trimmed largest requires trimmed_largest_flow_cr + trimmed_largest_value_cr")
        lines.append(
            f"- Trimmed {trimmed_count} position(s). The largest cut was "
            f"{trimmed_name} ({_fmt_cr(trimmed_flow_cr)} flow, {_fmt_cr(trimmed_value_cr)} value change)."
        )
    elif trimmed_count > 0:
        lines.append(f"- Trimmed {trimmed_count} position(s).")

    if exited:
        if len(exited) > 5:
            display = ", ".join(exited[:5]) + f", and {len(exited) - 5} more"
        else:
            display = ", ".join(exited)
        lines.append(f"- Fully exited: {display}.")

    if non_equity_note is not None:
        lines.append(f"- {non_equity_note}")

    if len(lines) == 1:
        lines.append("- No material changes versus the previous month.")

    text = "\n".join(lines)
    # Defensive: sign-aware formatting above must never emit '+₹-'.
    assert "+₹-" not in text
    return text
