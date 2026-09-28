"""Download monthly portfolios from major fund houses into the intake inbox."""

from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from pathlib import Path

import requests

from findit.ingest import download
from findit.ingest.intake import load_registry, prepare, render


def _browser_page(stack: ExitStack):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "browser support missing; install with 'pip install -e .[download]'"
        ) from exc
    playwright = stack.enter_context(sync_playwright())
    browser = download._browser(playwright)
    stack.callback(browser.close)
    return browser.new_page(accept_downloads=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", required=True, help="Portfolio month, YYYY-MM")
    ap.add_argument("--inbox", type=Path, default=Path("real_data/inbox"))
    ap.add_argument("--amc", action="append", choices=download.SUPPORTED,
                    help="Repeat to select AMCs; default: all supported houses")
    ap.add_argument("--dry-run", action="store_true",
                    help="Discover and print files without downloading")
    ap.add_argument("--prepare", action="store_true",
                    help="Run intake validation after all selected downloads succeed")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        download._month(args.month)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    selected = tuple(dict.fromkeys(args.amc or download.SUPPORTED))
    registry = {item.amc: item for item in load_registry()}
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 FindIt/0.2"})
    failed = []
    with ExitStack() as stack:
        page = None
        for amc in selected:
            try:
                if amc in {"HDFC AMC", "Aditya Birla Sun Life AMC", "Mirae Asset AMC"} and page is None:
                    page = _browser_page(stack)
                try:
                    files = download.discover(args.month, amc, session, page)
                except ValueError as exc:
                    if amc != "ICICI Prudential AMC" or "browser required" not in str(exc):
                        raise
                    if page is None:
                        page = _browser_page(stack)
                    files = download.discover_icici(args.month, page)
                print(f"{amc}: {len(files)} file(s) for {args.month}")
                if args.dry_run:
                    for item in files:
                        print(f"  {item.name}  {item.url}")
                    continue
                saved = download.save_files(files, args.inbox / args.month, session, page)
                for path in saved:
                    print(f"  saved {path}")
            except Exception as exc:
                failed.append(amc)
                print(f"{amc}: {exc}", file=sys.stderr)
                item = registry[amc]
                print(f"  ACTION NEEDED: Download {args.month} manually from "
                      f"{item.disclosure_page} into {args.inbox / args.month / amc}",
                      file=sys.stderr)
    if not args.amc:
        print("Manual download still needed for:")
        for amc in download.MANUAL:
            print(f"  {amc}: {registry[amc].disclosure_page} -> "
                  f"{args.inbox / args.month / amc}")
    if not args.dry_run:
        if args.prepare and not failed:
            report = prepare(args.inbox / args.month, args.month)
            print(render(report))
            if not report.ok:
                return 1
        elif args.prepare:
            print("Intake validation skipped because downloads failed.", file=sys.stderr)
        if not args.prepare or failed:
            print(f"Next: findit intake --month {args.month} --inbox {args.inbox}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
