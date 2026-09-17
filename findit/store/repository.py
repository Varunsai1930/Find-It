"""findit.store.repository — SQLite connection, migrations, ingest, fetch.

- connect(path, read_only=False): read_only uses mode=ro URI +
  PRAGMA query_only=ON and never runs migrations.
- migrate(conn): explicit, idempotent; applies schema.sql plus legacy
  instrument_type / flow_lakhs backfills (preserving db.py behavior).
- ingest_dataframe(conn, df, source_hash, nav_scale=None): staging ->
  validate -> atomic commit per scheme-month (whole snapshot replace);
  quarantines failures into scheme_month_status.
- fetch_holdings(conn, month, validated_only=True): validated view that
  hides quarantined scheme-months by default.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

REQUIRED_COLUMNS = {
    "isin",
    "instrument_name",
    "quantity",
    "market_value_lakhs",
    "pct_nav",
    "scheme_name",
    "amc_name",
    "report_month",
}

# ---------------------------------------------------------------- classify


def _classify_isin(isin: Any) -> str:
    try:
        from findit.core.instruments import classify_isin  # type: ignore

        return classify_isin(isin)  # type: ignore[no-any-return]
    except Exception:
        pass
    try:
        from db import classify_isin  # type: ignore

        return classify_isin(isin)  # type: ignore[no-any-return]
    except Exception:
        pass
    # Local fallback (mirrors db.py rules).
    if not isinstance(isin, str) or len(isin) < 12:
        return "other"
    s = isin.upper().strip()
    if not s.startswith("IN"):
        return "foreign"
    c3 = s[2]
    series = s[7:9]
    if c3 == "0":
        return "tbill_or_gsec"
    if c3 == "9":
        return "sgsec"
    if c3 == "F":
        return "mf_units"
    if c3 == "E":
        if series == "01":
            return "equity"
        if series == "02":
            return "preference"
        if series in ("07", "08", "09", "10", "11", "12"):
            return "ncd"
        if series in ("14", "16"):
            return "cp_or_cd"
        return "debt_other"
    return "other"


# ---------------------------------------------------------------- connect


def connect(path: Any, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection.

    read_only=True uses a mode=ro URI plus PRAGMA query_only=ON and never
    runs migrations (never creates or alters tables).
    """
    if read_only:
        uri = f"file:{Path(str(path))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA query_only=ON;")
        except sqlite3.DatabaseError:
            pass
        return conn
    conn = sqlite3.connect(str(path))
    migrate(conn)
    return conn


# ---------------------------------------------------------------- migrate


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def _tables(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.DatabaseError:
        return set()


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema.sql idempotently (safe to call repeatedly)."""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    # Strip full-line SQL comments first so semicolons inside comments can
    # never split a statement (e.g. "-- ... file; migrate() ...").
    stripped = "\n".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("--")
    )
    # Execute statement-by-statement so bare ALTER TABLE lines for existing
    # DBs can be tolerated (duplicate-column -> ignore).
    for raw in stripped.split(";"):
        stmt = raw.strip()
        if not stmt:
            continue
        sql = stmt + ";"
        try:
            conn.executescript(sql)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg:
                continue
            raise

    # Legacy backfills preserved from db.py (idempotent PRAGMA-guarded).
    _ensure_column(conn, "stocks", "instrument_type", "instrument_type TEXT")
    _ensure_column(conn, "mf_holding_deltas", "flow_lakhs", "flow_lakhs REAL")
    # v2 columns (also present as ALTERs in schema.sql; PRAGMA guard keeps
    # this idempotent even if the script path above is skipped/partial).
    _ensure_column(conn, "mf_holdings_monthly", "pct_nav_raw", "pct_nav_raw REAL")
    _ensure_column(conn, "mf_holdings_monthly", "pct_nav_scale", "pct_nav_scale TEXT")
    _ensure_column(
        conn, "mf_holdings_monthly", "source_file_hash", "source_file_hash TEXT"
    )
    _ensure_column(conn, "mf_holdings_monthly", "ingest_run_id", "ingest_run_id TEXT")
    _ensure_column(conn, "shareholding_quarterly", "filing_type", "filing_type TEXT")
    _ensure_column(
        conn, "shareholding_quarterly", "validation_status", "validation_status TEXT"
    )
    _ensure_column(conn, "shareholding_quarterly", "source_url", "source_url TEXT")
    _ensure_column(
        conn, "shareholding_quarterly", "source_sha256", "source_sha256 TEXT"
    )

    # Backfill instrument_type for stocks missing it (db.py behavior).
    try:
        if "stocks" in _tables(conn) and "instrument_type" in _columns(conn, "stocks"):
            missing = conn.execute(
                "SELECT isin FROM stocks WHERE instrument_type IS NULL"
            ).fetchall()
            if missing:
                cur = conn.cursor()
                for (isin,) in missing:
                    cur.execute(
                        "UPDATE stocks SET instrument_type = ? WHERE isin = ?",
                        (_classify_isin(isin), isin),
                    )
    except sqlite3.DatabaseError:
        pass

    conn.commit()


# ---------------------------------------------------------------- ingest helpers


def _normalize_nav_scale(raw_sum: float, nav_scale: Any) -> str:
    if nav_scale is not None:
        if isinstance(nav_scale, str):
            s = nav_scale.strip().lower()
            if s in ("fraction", "fractional", "frac", "0-1", "0..1"):
                return "fraction"
            if s in ("percent", "percentage", "pct", "0-100", "0..100"):
                return "percent"
            raise ValueError(f"unknown nav_scale {nav_scale!r}")
        try:
            f = float(nav_scale)  # type: ignore[arg-type]
            # Heuristic: 1 -> fraction multiplier 100; 100 -> percent.
            if f == 1 or abs(f - 0.01) < 1e-9:
                return "fraction"
            if f == 100:
                return "percent"
        except (TypeError, ValueError):
            pass
        raise ValueError(f"unknown nav_scale {nav_scale!r}")
    # Strict auto-detect: only unambiguous bands infer a scale. Anything
    # else (e.g. a partial sheet summing to 1.5 or 20) raises so the caller
    # quarantines instead of guessing and corrupting data.
    if 0.95 <= raw_sum <= 1.05:
        return "fraction"
    if 95.0 <= raw_sum <= 105.0:
        return "percent"
    raise ValueError(f"ambiguous pct_nav sum {raw_sum!r}: cannot infer scale")


def _latest_prev_month(
    conn: sqlite3.Connection, scheme_id: int, report_month: str
) -> Optional[str]:
    try:
        row = conn.execute(
            "SELECT MAX(report_month) FROM mf_holdings_monthly "
            "WHERE scheme_id = ? AND report_month < ?",
            (scheme_id, report_month),
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row and row[0]:
        return str(row[0])
    return None


def _prev_quantities(
    conn: sqlite3.Connection, scheme_id: int, prev_month: str
) -> dict[str, float]:
    try:
        rows = conn.execute(
            "SELECT isin, quantity FROM mf_holdings_monthly "
            "WHERE scheme_id = ? AND report_month = ?",
            (scheme_id, prev_month),
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    out: dict[str, float] = {}
    for isin, qty in rows:
        try:
            out[str(isin)] = float(qty) if qty is not None else 0.0
        except (TypeError, ValueError):
            continue
    return out


def _has_corporate_action(
    conn: sqlite3.Connection, isin: str, effective_month: str
) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM corporate_actions WHERE isin = ? AND effective_month = ? LIMIT 1",
            (isin, effective_month),
        ).fetchone()
        return row is not None
    except sqlite3.DatabaseError:
        return False


def _refresh_prices_for_month(
    conn: sqlite3.Connection, report_month: str, isins: list[str]
) -> None:
    """Recompute instrument_prices_monthly for the touched ISINs."""
    import pandas as pd

    for isin in set(isins):
        try:
            df = pd.read_sql_query(
                "SELECT quantity, market_value_lakhs FROM mf_holdings_monthly "
                "WHERE isin = ? AND report_month = ?",
                conn,
                params=(isin, report_month),
            )
        except Exception:
            continue
        if df.empty:
            continue
        try:
            df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
            df["market_value_lakhs"] = pd.to_numeric(
                df["market_value_lakhs"], errors="coerce"
            )
            df = df[(df["quantity"] > 0) & (df["market_value_lakhs"].notna())]
        except Exception:
            continue
        if df.empty:
            continue
        px = (df["market_value_lakhs"] / df["quantity"]).tolist()
        n = len(px)
        mean = sum(px) / n if n else None
        cv = None
        if n >= 2 and mean:
            var = sum((x - mean) ** 2 for x in px) / n
            import math

            cv = math.sqrt(var) / abs(mean) if mean != 0 else None
        try:
            conn.execute(
                "INSERT OR REPLACE INTO instrument_prices_monthly "
                "(isin, report_month, implied_px, n_schemes, px_cv) "
                "VALUES (?, ?, ?, ?, ?)",
                (isin, report_month, mean, n, cv),
            )
        except sqlite3.DatabaseError:
            continue


# ---------------------------------------------------------------- ingest


def ingest_dataframe(
    conn: sqlite3.Connection,
    df: Any,
    source_hash: str,
    nav_scale: Any = None,
) -> dict:
    """Stage -> validate -> atomic commit per scheme-month.

    Each (amc_name, scheme_name, report_month) group is validated as a unit.
    Valid groups fully replace that scheme-month snapshot (DELETE + INSERT in
    one transaction). Invalid groups are quarantined: no holdings rows are
    touched and scheme_month_status gets status='quarantined' with the JSON
    validation report. Returns {"run_id", "ingested", "quarantined", ...}.
    """
    import pandas as pd

    from .validation_gate import (
        validate_implied_price_cv,
        validate_nav_sum,
        validate_qty_ratio,
    )

    if df is None or not hasattr(df, "columns"):
        raise ValueError("ingest_dataframe requires a DataFrame")
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"dataframe is missing expected columns: {sorted(missing)}")
    if not isinstance(source_hash, str) or not source_hash:
        raise ValueError("source_hash must be a non-empty string")

    # Ensure schema exists on writable connections (no-op if already migrated).
    try:
        migrate(conn)
    except sqlite3.DatabaseError:
        # Read-only / query_only connections will fail here; ingest needs
        # write access anyway so let the subsequent write raise clearly.
        pass

    run_id = uuid.uuid4().hex
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ingest_runs "
            "(run_id, started_at, status, validation_report_json) "
            "VALUES (?, ?, ?, ?)",
            (run_id, started_at, "running", None),
        )
        conn.commit()
    except sqlite3.DatabaseError:
        pass

    if df.empty:
        report = {"run_id": run_id, "groups": 0, "quarantined": 0}
        try:
            conn.execute(
                "UPDATE ingest_runs SET status = ?, validation_report_json = ? "
                "WHERE run_id = ?",
                ("completed", json.dumps(report), run_id),
            )
            conn.commit()
        except sqlite3.DatabaseError:
            pass
        return {"run_id": run_id, "ingested": 0, "quarantined": []}

    work = df.copy()
    quarantined: list[dict] = []
    ingested_rows = 0
    group_reports: list[dict] = []

    for (amc, scheme, month), idx in work.groupby(
        ["amc_name", "scheme_name", "report_month"]
    ).groups.items():
        grp = work.loc[idx].copy()
        amc_s, scheme_s, month_s = str(amc), str(scheme), str(month)

        # Resolve scheme_id.
        try:
            conn.execute(
                "INSERT OR IGNORE INTO schemes (amc_name, scheme_name) VALUES (?, ?)",
                (amc_s, scheme_s),
            )
            conn.commit()
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"failed to ensure scheme {amc_s}/{scheme_s}: {exc}")
        row = conn.execute(
            "SELECT scheme_id FROM schemes WHERE amc_name = ? AND scheme_name = ?",
            (amc_s, scheme_s),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"scheme_id missing for {amc_s}/{scheme_s}")
        scheme_id = int(row[0])

        # Numeric coercion (keep raw for provenance).
        grp["quantity"] = pd.to_numeric(grp["quantity"], errors="coerce")
        grp["market_value_lakhs"] = pd.to_numeric(
            grp["market_value_lakhs"], errors="coerce"
        )
        grp["pct_nav"] = pd.to_numeric(grp["pct_nav"], errors="coerce")
        if grp[["quantity", "market_value_lakhs", "pct_nav"]].isna().any().any():
            bad = int(grp[["quantity", "market_value_lakhs", "pct_nav"]].isna().any(axis=1).sum())
            report_d = {
                "passed": False,
                "issues": [
                    {
                        "code": "non_numeric_holding",
                        "severity": "error",
                        "message": (
                            f"{bad} row(s) with non-numeric quantity/market_value/pct_nav"
                        ),
                    }
                ],
            }
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO scheme_month_status "
                    "(scheme_id, report_month, status, validation_report_json, source_data_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        scheme_id,
                        month_s,
                        "quarantined",
                        json.dumps(report_d),
                        source_hash,
                    ),
                )
                conn.commit()
            except sqlite3.DatabaseError:
                pass
            quarantined.append({"scheme_id": scheme_id, "report_month": month_s})
            group_reports.append(
                {"scheme_id": scheme_id, "report_month": month_s, "status": "quarantined"}
            )
            continue

        raw_vals = grp["pct_nav"].astype(float).tolist()
        raw_sum = float(sum(raw_vals)) if raw_vals else 0.0
        try:
            scale = _normalize_nav_scale(raw_sum, nav_scale)
        except ValueError as exc:
            report_d = {
                "passed": False,
                "issues": [
                    {"code": "nav_scale", "severity": "error", "message": str(exc)}
                ],
            }
            conn.execute(
                "INSERT OR REPLACE INTO scheme_month_status "
                "(scheme_id, report_month, status, validation_report_json, source_data_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (scheme_id, month_s, "quarantined", json.dumps(report_d), source_hash),
            )
            conn.commit()
            quarantined.append({"scheme_id": scheme_id, "report_month": month_s})
            group_reports.append(
                {"scheme_id": scheme_id, "report_month": month_s, "status": "quarantined"}
            )
            continue

        if scale == "fraction":
            norm_vals = [v * 100.0 for v in raw_vals]
        else:
            norm_vals = list(raw_vals)

        # ---- validation (pure) ----
        issues: list[dict] = []
        nav_res = validate_nav_sum(sum(norm_vals))
        issues.extend(nav_res["issues"])

        # Implied-price CV within this batch per ISIN (warn-only).
        try:
            priced = grp.copy()
            priced["implied_px"] = priced["market_value_lakhs"] / priced["quantity"]
            priced = priced[(priced["quantity"] > 0)]
            for isin_v, sub in priced.groupby("isin"):
                if len(sub) >= 2:
                    r = validate_implied_price_cv(
                        prices=sub["implied_px"].tolist(), label=str(isin_v)
                    )
                    issues.extend(r["issues"])
        except Exception:
            pass

        # Qty-ratio vs previous stored month for this scheme (fail -> quarantine).
        gate_passed = True
        prev_month = _latest_prev_month(conn, scheme_id, month_s)
        if prev_month is not None:
            prev_q = _prev_quantities(conn, scheme_id, prev_month)
            cur_q = {
                str(isin): float(q)
                for isin, q in zip(
                    grp["isin"].astype(str).tolist(), grp["quantity"].astype(float).tolist()
                )
            }
            for isin_v, curr_qty in cur_q.items():
                if isin_v not in prev_q:
                    continue
                ca = _has_corporate_action(conn, isin_v, month_s)
                r = validate_qty_ratio(
                    prev_q[isin_v], curr_qty, has_corporate_action=ca, isin=isin_v
                )
                issues.extend(r["issues"])
                if not r["passed"]:
                    gate_passed = False

        report_d = {"passed": bool(gate_passed), "issues": issues}
        if not gate_passed:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO scheme_month_status "
                    "(scheme_id, report_month, status, validation_report_json, source_data_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        scheme_id,
                        month_s,
                        "quarantined",
                        json.dumps(report_d),
                        source_hash,
                    ),
                )
                conn.commit()
            except sqlite3.DatabaseError:
                pass
            quarantined.append({"scheme_id": scheme_id, "report_month": month_s})
            group_reports.append(
                {"scheme_id": scheme_id, "report_month": month_s, "status": "quarantined"}
            )
            continue

        # ---- atomic commit: whole snapshot replace ----
        staged: list[tuple] = []
        isins_touched: list[str] = []
        for i, row_s in grp.iterrows():
            isin_v = str(row_s["isin"])
            isins_touched.append(isin_v)
            staged.append(
                (
                    scheme_id,
                    isin_v,
                    month_s,
                    float(row_s["quantity"]),
                    float(row_s["market_value_lakhs"]),
                    float(norm_vals[grp.index.get_loc(i)]),
                    float(row_s["pct_nav"]),
                    scale,
                    source_hash,
                    run_id,
                )
            )

        try:
            # Upsert reference rows first (outside the snapshot transaction
            # would also work; keep them inside for single-commit atomicity
            # per group is not required for dims, so do dims first + commit,
            # then snapshot replace atomically).
            cur = conn.cursor()
            for _, row_s in grp.iterrows():
                isin_v = str(row_s["isin"])
                name_v = str(row_s.get("instrument_name", isin_v))
                try:
                    industry_v = row_s.get("industry", None)
                    if pd.isna(industry_v):
                        industry_v = None
                    else:
                        industry_v = str(industry_v)
                except Exception:
                    industry_v = None
                cur.execute(
                    "INSERT OR IGNORE INTO stocks (isin, name, industry, instrument_type) "
                    "VALUES (?, ?, ?, ?)",
                    (isin_v, name_v, industry_v, _classify_isin(isin_v)),
                )
                cur.execute(
                    "INSERT OR IGNORE INTO instruments "
                    "(isin, name, instrument_type) VALUES (?, ?, ?)",
                    (isin_v, name_v, _classify_isin(isin_v)),
                )
            conn.commit()

            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM mf_holdings_monthly WHERE scheme_id = ? AND report_month = ?",
                (scheme_id, month_s),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO mf_holdings_monthly "
                "(scheme_id, isin, report_month, quantity, market_value_lakhs, "
                " pct_nav, pct_nav_raw, pct_nav_scale, source_file_hash, ingest_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                staged,
            )
            conn.execute(
                "INSERT OR REPLACE INTO scheme_month_status "
                "(scheme_id, report_month, status, validation_report_json, source_data_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (scheme_id, month_s, "validated", json.dumps(report_d), source_hash),
            )
            conn.commit()
            _refresh_prices_for_month(conn, month_s, isins_touched)
            try:
                conn.commit()
            except sqlite3.DatabaseError:
                pass
            ingested_rows += len(staged)
            group_reports.append(
                {"scheme_id": scheme_id, "report_month": month_s, "status": "validated"}
            )
        except Exception as exc:  # noqa: BLE001 — quarantine on any commit failure
            try:
                conn.rollback()
            except sqlite3.DatabaseError:
                pass
            err_report = {
                "passed": False,
                "issues": [
                    {
                        "code": "commit_failed",
                        "severity": "error",
                        "message": f"atomic commit failed, rolled back: {exc}",
                    }
                ],
            }
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO scheme_month_status "
                    "(scheme_id, report_month, status, validation_report_json, source_data_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        scheme_id,
                        month_s,
                        "quarantined",
                        json.dumps(err_report),
                        source_hash,
                    ),
                )
                conn.commit()
            except sqlite3.DatabaseError:
                pass
            quarantined.append({"scheme_id": scheme_id, "report_month": month_s})
            group_reports.append(
                {"scheme_id": scheme_id, "report_month": month_s, "status": "quarantined"}
            )
            continue

    final_status = "completed" if not quarantined else "completed_with_quarantine"
    summary = {
        "run_id": run_id,
        "groups": len(group_reports),
        "ingested_rows": ingested_rows,
        "quarantined": len(quarantined),
        "groups_detail": group_reports,
    }
    try:
        conn.execute(
            "UPDATE ingest_runs SET status = ?, validation_report_json = ? WHERE run_id = ?",
            (final_status, json.dumps(summary), run_id),
        )
        conn.commit()
    except sqlite3.DatabaseError:
        pass
    return {
        "run_id": run_id,
        "ingested": ingested_rows,
        "quarantined": quarantined,
        "status": final_status,
    }


# ---------------------------------------------------------------- fetch


def fetch_holdings(
    conn: sqlite3.Connection, month: str, validated_only: bool = True
) -> Any:
    """Return holdings for a report_month as a DataFrame.

    validated_only=True hides scheme-months quarantined in
    scheme_month_status; False returns every stored row.
    """
    import pandas as pd

    base_cols = (
        "h.scheme_id, h.isin, h.report_month, h.quantity, "
        "h.market_value_lakhs, h.pct_nav, h.pct_nav_raw, h.pct_nav_scale, "
        "h.source_file_hash, h.ingest_run_id"
    )
    if validated_only:
        sql = (
            f"SELECT {base_cols} FROM mf_holdings_monthly h "
            "WHERE h.report_month = ? AND h.scheme_id NOT IN ("
            " SELECT scheme_id FROM scheme_month_status "
            " WHERE report_month = ? AND status = 'quarantined')"
        )
        return pd.read_sql_query(sql, conn, params=(month, month))
    sql = (
        f"SELECT {base_cols} FROM mf_holdings_monthly h WHERE h.report_month = ?"
    )
    return pd.read_sql_query(sql, conn, params=(month,))
