"""Parse one AMC monthly disclosure workbook into a CSV the pipeline loads."""
from __future__ import annotations

import argparse
from pathlib import Path

from findit.ingest import amfi_mf_parser


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=amfi_mf_parser.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path, help="Path to one AMC's monthly disclosure .xlsx")
    ap.add_argument("--amc", required=True, help="AMC name, e.g. 'HDFC AMC'")
    ap.add_argument("--month", required=True, help="Report month, e.g. 2026-08")
    ap.add_argument("--out", type=Path, default=None, help="Output CSV path")
    args = ap.parse_args(argv)

    print(f"Parsing {args.file} ...")
    df = amfi_mf_parser.parse_workbook(args.file, args.amc, args.month)
    print(
        f"Parsed {df['isin'].notna().sum()} holding rows across {df['scheme_name'].nunique()} "
        f"schemes."
    )

    out_path = args.out or args.file.with_suffix(".parsed.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {out_path}")



if __name__ == "__main__":
    main()
