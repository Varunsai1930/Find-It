"""Start the holdings dashboard on your computer.

    findit web --db tracker.db

The default address is local to this computer. The database is opened
read-only; use the pipeline separately to ingest new disclosures.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path,
                        help="Holdings database (default: FINDIT_DB or ./tracker.db)")
    parser.add_argument("--host", default="127.0.0.1", help="Listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Listen port (default: 8000)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    import uvicorn
    from findit.web.app import create_app

    # Watchlist/search query strings contain user research choices. Retain
    # startup/error logs without recording every URL and its stock list.
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
