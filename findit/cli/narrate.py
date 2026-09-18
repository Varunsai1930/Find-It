"""Generate cached fund summaries explicitly; rules are the offline default."""
import argparse
import json
from pathlib import Path
import sqlite3

from findit.narrate.facts import build_snapshot
from findit.narrate.service import generate_batch, selected_scheme_ids, validate_month


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--month", required=True, type=validate_month)
    parser.add_argument("--schemes", nargs="+", type=int)
    parser.add_argument("--provider", choices=("rules", "zai"), default="rules")
    parser.add_argument("--model", default="glm-5.3")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.db).expanduser().resolve()
    if not path.is_file():
        parser.error("database must already exist")
    mode = "ro" if args.dry_run else "rw"
    conn = sqlite3.connect(path.as_uri() + f"?mode={mode}", uri=True)
    try:
        ids = selected_scheme_ids(conn, args.month, args.schemes)
        if args.dry_run:
            reports = []
            for sid in ids:
                snapshot = build_snapshot(conn, sid, args.month)
                reports.append({"scheme_id": sid,
                                "status": "eligible" if snapshot.eligible else "skipped",
                                "reason": snapshot.reason,
                                "source_data_hash": snapshot.source_hash})
        else:
            provider = None
            if args.provider == "zai":
                from findit.narrate.provider import ZaiNarrator
                provider = ZaiNarrator(model=args.model)
            reports = generate_batch(conn, args.month, ids, provider, args.force)
        print(json.dumps(reports, ensure_ascii=False, sort_keys=True))
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
