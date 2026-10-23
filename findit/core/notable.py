"""Notable monthly fund activity: stocks that meet one stated rule. Pure.

These lists are observations, never predictions. FindIt's recorded ten-year
test (docs/research-and-workflows.md) found that no group of this kind beat
comparable stocks reliably once size is controlled. So each list says what
funds did and why a stock is on it, and nothing about what the stock will do.

Every rule reads one month's ranking (consensus_signals.ranked_consensus) in
one direction: the buying lists need more fund houses buying than selling,
the selling lists the reverse. Turnarounds and streaks also read the rankings
of the calendar months just before, and report which month is missing when
one was never compared, instead of treating it as no activity.
"""

from __future__ import annotations

import pandas as pd

# Every list needs at least this many fund houses behind a stock.
MIN_HOUSES = 2
# This month plus the months before it that a streak must cover.
STREAK_MONTHS = 3
# Stocks shown per list before the rest are folded away.
SHOWN = 5


def _signed(value: float, places: int = 0) -> str:
    return f"{value:+.{places}f}".replace("-", "−")


def _stock(row, reason: str) -> dict:
    return {"isin": row["isin"], "stock_name": row["stock_name"], "reason": reason,
            "followable": row.get("instrument_type") == "equity"}


def _nets(frame: pd.DataFrame | None) -> pd.Series:
    """Net fund houses per ISIN; a stock nobody traded that month counts as 0."""
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    return frame.set_index("isin")["net_amc_count"].astype(float)


def notable_lists(ranked: pd.DataFrame, earlier: list[tuple[str, pd.DataFrame | None]],
                  side: str = "buy") -> list[dict]:
    """The month's five notable lists for one direction.

    ``ranked``: the month's ranking in rank order for ``side`` ("buy" or
    "sell"). ``earlier``: (month, ranking) for the calendar months before it,
    newest first, at least STREAK_MONTHS - 1 of them; the ranking is None when
    that month was never compared with the month before it.

    Each list is {key, title, rule, needs, stocks}: ``needs`` names the month
    a rule could not read (its stocks are then empty), and each stock is
    {isin, stock_name, reason, followable}, strongest first, with
    ties in ranking order.
    """
    buying = side != "sell"
    sign = 1 if buying else -1
    frame = ranked.reset_index(drop=True).copy()
    frame["_position"] = range(len(frame))
    net = frame["net_amc_count"].astype(float) * sign if len(frame) else pd.Series(dtype=float)
    word = "buying" if buying else "selling"
    direction_rule = (f" More fund houses are {word} than "
                      f"{'selling' if buying else 'buying'} overall.")

    def ordered(mask, *keys):
        """Matching rows, by each key descending, then ranking position."""
        mask = mask & (net > 0)
        chosen = frame[mask].copy()
        for i, key in enumerate(keys):
            chosen[f"_k{i}"] = key[mask]
        columns = [f"_k{i}" for i in range(len(keys))] + ["_position"]
        return chosen.sort_values(columns, ascending=[False] * len(keys) + [True])

    lists = []

    # 1. Turnarounds: this month's direction reverses last month's.
    last_month, last = earlier[0]
    turn = {"key": "turnaround", "title": f"Turned to {word}", "needs": None, "stocks": [],
            "rule": (f"{MIN_HOUSES} or more net fund houses {word} this month, after more "
                     f"were {'selling' if buying else 'buying'} last month.")}
    if last is None:
        turn["needs"] = last_month
    elif len(frame):
        before = frame["isin"].map(_nets(last)).fillna(0.0)
        mask = (net >= MIN_HOUSES) & (before * sign <= -1)
        for _, row in ordered(mask, net - before * sign).iterrows():
            turn["stocks"].append(_stock(row, f"{_signed(row['net_amc_count'])} net fund houses, "
                                              f"from {_signed(before[row.name])} last month"))
    lists.append(turn)

    # 2. Fresh positions or full exits, counted by fund house.
    if buying:
        fresh = {"key": "opened", "title": "Fresh positions", "needs": None, "stocks": [],
                 "rule": (f"Schemes at {MIN_HOUSES} or more fund houses opened a position they "
                          "did not hold last month. Stocks absent from last month's loaded "
                          "portfolios, such as new listings, are left out." + direction_rule)}
        if len(frame):
            count = frame["amcs_opening"].astype(int)
            mask = (count >= MIN_HOUSES) & (frame["universe_status"] != "new_listing")
            for _, row in ordered(mask, count).iterrows():
                fresh["stocks"].append(_stock(row, f"Schemes at {row['amcs_opening']} fund "
                                                   "houses opened new positions"))
    else:
        fresh = {"key": "exited", "title": "Full exits", "needs": None, "stocks": [],
                 "rule": (f"Schemes at {MIN_HOUSES} or more fund houses sold their whole "
                          "position. Other schemes at the same house may still hold it." + direction_rule)}
        if len(frame):
            count = frame["amcs_exiting"].astype(int)
            for _, row in ordered(count >= MIN_HOUSES, count).iterrows():
                fresh["stocks"].append(_stock(row, f"Schemes at {row['amcs_exiting']} fund "
                                                   "houses sold their whole position"))
    lists.append(fresh)

    # 3. Active choices: changes larger than price movement alone would cause.
    active = {"key": "active", "title": f"Active {word}", "needs": None, "stocks": [],
              "rule": (f"{MIN_HOUSES} or more net fund houses whose change was larger than "
                       "price movement alone would cause." + direction_rule)}
    if len(frame):
        choice = frame["net_active_amc_count"].fillna(0).astype(float) * sign
        for _, row in ordered(choice >= MIN_HOUSES, choice).iterrows():
            active["stocks"].append(_stock(row, f"{_signed(row['net_active_amc_count'])} net "
                                                "fund houses beyond price movement"))
    lists.append(active)

    # 4. Streaks: the same direction in each of the last STREAK_MONTHS months.
    streak = {"key": "streak", "title": f"{word.capitalize()} streaks", "needs": None,
              "stocks": [],
              "rule": (f"More fund houses {word} than "
                       f"{'selling' if buying else 'buying'} in each of the last "
                       f"{STREAK_MONTHS} months.")}
    window = earlier[:STREAK_MONTHS - 1]
    missing = [month for month, frame_ in window if frame_ is None]
    if missing:
        streak["needs"] = missing[0]
    elif len(frame):
        # Oldest month first, so the reason reads in time order.
        history = [frame["isin"].map(_nets(frame_)).fillna(0.0) for _, frame_ in reversed(window)]
        history.append(frame["net_amc_count"].astype(float))
        mask = pd.concat([h * sign >= 1 for h in history], axis=1).all(axis=1)
        weakest = pd.concat([h * sign for h in history], axis=1).min(axis=1)
        for _, row in ordered(mask, weakest).iterrows():
            values = ", ".join(_signed(h[row.name]) for h in history)
            streak["stocks"].append(_stock(row, f"Net {word} {STREAK_MONTHS} months running: "
                                                f"{values}"))
    lists.append(streak)

    # 5. Foreign institutions moved the same way in their latest public filing.
    # FII only: DII includes mutual funds themselves, so it cannot agree independently.
    agree = {"key": "fii", "title": "Foreign investors agree", "needs": None, "stocks": [],
             "rule": (f"{MIN_HOUSES} or more net fund houses {word}, and foreign institutions "
                      f"(FII) {'raised' if buying else 'cut'} their stake in the latest quarterly "
                      "filing public by then. Stale filings are left out.")}
    if len(frame) and "fii_pct_change" in frame:
        change = pd.to_numeric(frame["fii_pct_change"], errors="coerce") * sign
        mask = ((net >= MIN_HOUSES) & (change > 0) & (frame["shareholding_status"] != "missing")
                & ~frame["shareholding_stale"].fillna(True).astype(bool))
        for _, row in ordered(mask, net, change).iterrows():
            agree["stocks"].append(_stock(row, f"{_signed(row['net_amc_count'])} net fund "
                                               f"houses; FII {_signed(row['fii_pct_change'], 2)} "
                                               "pp in the latest filing"))
    lists.append(agree)
    return lists
