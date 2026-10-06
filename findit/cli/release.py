"""Create an immutable local data release, inspect it, or restore the local pointer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from findit.store.releases import snapshot, activate_release


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="Monthly research release")
    ap.add_argument("--parent")
    ap.add_argument("--notes", default="")
    ap.add_argument("--month", help="Monthly activation period (YYYY-MM); required for legacy rollback")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--rollback", help="Restore current.json to a retained release ID")
    action.add_argument("--candidate", action="store_true",
                        help="Freeze candidate files without creating or changing current.json")
    args = ap.parse_args(argv)
    if args.rollback:
        try:
            activate_release(args.out, args.rollback, args.month, args.notes)
        except (ValueError, OSError) as exc:
            ap.error(str(exc))
        print(f"Restored local pointer to {args.rollback}; restart the demo with that database.")
        return 0
    if not args.db or not args.db.is_file():
        ap.error("an existing --db is required")
    if args.parent and not args.notes.strip():
        ap.error("a revision needs --notes explaining the correction")
    try:
        manifest = snapshot(args.db, args.out, args.label, args.parent, args.notes,
                            activate=not args.candidate, report_month=args.month)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
