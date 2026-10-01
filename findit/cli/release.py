"""Create an immutable local data release, inspect it, or restore the local pointer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from findit.store.releases import snapshot, verify_release


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="Monthly research release")
    ap.add_argument("--parent")
    ap.add_argument("--notes", default="")
    ap.add_argument("--rollback", help="Restore current.json to a retained release ID")
    args = ap.parse_args(argv)
    if args.rollback:
        try:
            verify_release(args.out, args.rollback)
        except ValueError as exc:
            ap.error(str(exc))
        pointer = args.out / ".current.tmp"
        pointer.write_text(json.dumps({"release_id": args.rollback}) + "\n")
        pointer.replace(args.out / "current.json")
        print(f"Restored local pointer to {args.rollback}; restart the demo with that database.")
        return 0
    if not args.db or not args.db.is_file():
        ap.error("an existing --db is required")
    if args.parent and not args.notes.strip():
        ap.error("a revision needs --notes explaining the correction")
    print(json.dumps(snapshot(args.db, args.out, args.label, args.parent, args.notes), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
