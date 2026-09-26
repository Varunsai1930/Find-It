"""Monthly disclosure intake: workbooks you downloaded -> parsed CSVs to load.

Every AMC publishes its monthly portfolio differently, so the files are
downloaded by hand from each AMC's disclosure page (listed in
``amfi_amcs.json``, taken from AMFI) into one folder per fund house:

    real_data/inbox/2026-08/SBI AMC/<workbooks or zips>
    real_data/inbox/2026-08/Nippon India AMC/...

``prepare`` checks and parses them. It never downloads anything and never
opens the database; loading the CSVs it writes stays a separate, explicit
``run_pipeline.py`` step. An AMC is refused -- nothing written for it -- when
its files would load wrong data silently:

- a folder name that is not an AMFI fund house (a typo would fork the AMC);
- the same sheet in two files (a consolidated and a per-scheme download, or
  generic sheet names): the loader keys holdings on the sheet, so one would
  silently overwrite the other;
- sheets whose titles name a different fund house (a file in the wrong folder);
- a workbook whose header dates name a different month;
- a workbook the parser cannot read.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import re
import shlex
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

import amfi_mf_parser
import db

REGISTRY_PATH = Path(__file__).with_name("amfi_amcs.json")
WORKBOOK_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
PARSED_DIR = "_parsed"
UNZIPPED_DIR = "_unzipped"
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]
# "august 31 2026", "aug 2026", "31st august 2026" after normalising.
_DATE_RE = re.compile(
    r"\b(" + "|".join(sorted({n for m in _MONTH_NAMES for n in (m, m[:3])},
                             key=len, reverse=True))
    + r")(?: \d{1,2}(?: (?:st|nd|rd|th))?)? (20\d\d)\b")
_HEADER_ROWS = 10


@dataclass(frozen=True)
class Amc:
    amfi_name: str
    amc: str
    brands: tuple[str, ...]
    disclosure_page: str | None


@dataclass
class AmcResult:
    amc: str
    files: list[str] = field(default_factory=list)
    schemes: int = 0
    rows: int = 0
    warnings: int = 0
    csv: Path | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class IntakeReport:
    month: str
    results: list[AmcResult]
    unknown_folders: dict[str, list[str]]
    loose_files: list[str]
    missing: list[Amc]

    @property
    def ok(self) -> bool:
        return not (self.unknown_folders or self.loose_files
                    or any(r.problems for r in self.results))


def load_registry(path: Path = REGISTRY_PATH) -> list[Amc]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Amc(a["amfi_name"], a["amc"], tuple(a["brands"]), a["disclosure_page"])
            for a in data["amcs"]]


def normalise(text: str) -> str:
    """Lower-case words and numbers separated by single spaces."""
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", str(text).lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def previous_month(month: str) -> str:
    year, mon = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def _slug(amc: str) -> str:
    return "_".join(normalise(amc).split()) or "amc"


def _brand_owner(title: str, registry: list[Amc]) -> str | None:
    """The fund house a scheme title starts with, or None when it names none."""
    text = normalise(title)
    for amc in registry:
        for brand in amc.brands:
            if re.match(rf"{re.escape(brand)}\b", text):
                return amc.amc
    return None


def _months_named(path: Path) -> set[str]:
    """YYYY-MM months named in the header rows of a workbook's first sheets."""
    found: set[str] = set()
    with pd.ExcelFile(path) as book:
        for sheet in book.sheet_names[:5]:
            raw = book.parse(sheet, header=None, nrows=_HEADER_ROWS)
            cells = [v for v in raw.to_numpy().ravel() if pd.notna(v)]
            # Real date cells ("as on" written as an Excel date) as well as text.
            found.update(f"{v.year}-{v.month:02d}" for v in cells if isinstance(v, datetime))
            text = normalise(" ".join(str(v) for v in cells if not isinstance(v, datetime)))
            for name, year in _DATE_RE.findall(text):
                mon = next(i for i, m in enumerate(_MONTH_NAMES, 1) if m.startswith(name))
                found.add(f"{year}-{mon:02d}")
    return found


def _expand_zips(folder: Path) -> list[str]:
    """Unpack each zip in folder into _unzipped/<zip name>/; returns problems."""
    problems = []
    # Unpack afresh: files from a zip that has since been removed must not linger.
    shutil.rmtree(folder / UNZIPPED_DIR, ignore_errors=True)
    for archive in sorted(folder.glob("*.zip")):
        target = folder / UNZIPPED_DIR / archive.stem
        try:
            with zipfile.ZipFile(archive) as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                # Refuse entries that would land outside the target folder.
                unsafe = [n for n in names
                          if Path(n).is_absolute() or ".." in Path(n).parts]
                if unsafe:
                    problems.append(f"{archive.name}: unsafe paths in zip ({unsafe[0]})")
                    continue
                zf.extractall(target)
        except zipfile.BadZipFile:
            problems.append(f"{archive.name}: not a readable zip")
    return problems


def _workbooks(folder: Path) -> list[Path]:
    """Workbooks under folder (including unpacked zips), skipping Excel lock files."""
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in WORKBOOK_SUFFIXES
        and not p.name.startswith(("~$", "."))
        and PARSED_DIR not in p.relative_to(folder).parts)


def _prepare_amc(folder: Path, amc: str, month: str, registry: list[Amc],
                 out_dir: Path) -> AmcResult:
    result = AmcResult(amc)
    result.problems.extend(_expand_zips(folder))
    books = _workbooks(folder)
    if not books:
        result.problems.append("no workbooks (.xlsx, .xls or .zip) in the folder")
    frames, sheet_files = [], {}
    for book in books:
        name = str(book.relative_to(folder))
        result.files.append(name)
        try:
            named = _months_named(book)
            if named and month not in named:
                result.problems.append(
                    f"{name}: dated {', '.join(sorted(named))}, not {month}")
                continue
            log = io.StringIO()
            # The parser logs every sheet; keep the log out of the report.
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                frame = amfi_mf_parser.parse_workbook(book, amc, month)
        except Exception as exc:  # the parser fails loudly by design; report it
            result.problems.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        result.warnings += log.getvalue().count("[warn]")
        for sheet in frame["scheme_name"].dropna().unique():
            # Keyed as the loader resolves schemes, so "SBI Bluechip" and
            # "SBI  Bluechip" count as the same scheme here too.
            sheet_files.setdefault(db.normalize_scheme_name(sheet), []).append(
                f"{name} [{sheet}]")
        frames.append(frame)

    for places in sheet_files.values():
        if len(places) > 1:
            result.problems.append(
                f"one scheme is in {' and '.join(places)}: keep one file per scheme "
                "(a consolidated download and a per-scheme download overlap)")

    if frames:
        parsed = pd.concat(frames, ignore_index=True)
        titled = parsed.dropna(subset=["scheme_title"]).drop_duplicates("scheme_name")
        for _, row in titled.iterrows():
            owner = _brand_owner(row["scheme_title"], registry)
            if owner is not None and owner != amc:
                result.problems.append(
                    f"sheet {row['scheme_name']!r} is {row['scheme_title']!r}, "
                    f"a {owner} scheme: move its file to the {owner} folder")
        result.schemes = parsed["scheme_name"].nunique()
        result.rows = len(parsed)

    stale = out_dir / f"{_slug(amc)}.parsed.csv"
    if result.problems or not frames:
        # Never leave an earlier run's CSV behind for a refused AMC.
        stale.unlink(missing_ok=True)
        return result
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed.to_csv(stale, index=False)
    result.csv = stale
    return result


def prepare(month_dir: Path, month: str, registry: list[Amc] | None = None) -> IntakeReport:
    """Check and parse every AMC folder under month_dir for month (YYYY-MM)."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    month_dir = Path(month_dir)
    if not month_dir.is_dir():
        raise FileNotFoundError(f"no folder {month_dir}")
    registry = registry if registry is not None else load_registry()
    by_name = {a.amc: a for a in registry}
    out_dir = month_dir / PARSED_DIR

    results, unknown, loose = [], {}, []
    for entry in sorted(month_dir.iterdir()):
        if entry.name.startswith(".") or entry.name == PARSED_DIR:
            continue
        if entry.is_file():
            loose.append(entry.name)
        elif entry.name not in by_name:
            lowered = {a.lower(): a for a in by_name}
            close = difflib.get_close_matches(entry.name.lower(), list(lowered), n=3, cutoff=0.5)
            unknown[entry.name] = [lowered[c] for c in close]
        else:
            results.append(_prepare_amc(entry, entry.name, month, registry, out_dir))
    present = {r.amc for r in results}
    missing = [a for a in registry if a.amc not in present]
    return IntakeReport(month, results, unknown, loose, missing)


def pipeline_command(report: IntakeReport, db_path: str = "tracker.db") -> str | None:
    """The run_pipeline.py call that loads every accepted AMC's CSV."""
    csvs = [str(r.csv) for r in report.results if r.csv is not None]
    if not csvs:
        return None
    parts = ["python3", "run_pipeline.py", "--load", *csvs,
             "--prev", previous_month(report.month), "--curr", report.month]
    if db_path != "tracker.db":
        parts += ["--db", db_path]
    return " ".join(shlex.quote(p) for p in parts)


def render(report: IntakeReport, db_path: str = "tracker.db") -> str:
    lines = [f"Intake for {report.month}"]
    for r in report.results:
        status = "REFUSED" if r.problems else "ok"
        lines.append(f"  {status:8} {r.amc}: {len(r.files)} file(s), {r.schemes} scheme(s), "
                     f"{r.rows} row(s)" + (f", {r.warnings} parser warning(s)" if r.warnings else ""))
        lines.extend(f"           - {p}" for p in r.problems)
    for name, close in report.unknown_folders.items():
        hint = f" -- did you mean {', '.join(repr(c) for c in close)}?" if close else ""
        lines.append(f"  UNKNOWN  folder {name!r} is not an AMFI fund house{hint}")
    for name in report.loose_files:
        lines.append(f"  LOOSE    {name}: put it inside its AMC's folder")
    command = pipeline_command(report, db_path)
    if command:
        lines += ["", "Load the accepted AMCs (review the lines above first):", f"  {command}"]
    if not report.ok:
        lines += ["", "Refused, unknown and loose items were not parsed; fix them and re-run."]
    lines += ["", f"Not in this folder ({len(report.missing)} of AMFI's "
                  f"{len(report.missing) + len(report.results)} fund houses):"]
    lines.extend(f"  {a.amc:32} {a.disclosure_page or '(no disclosure page listed by AMFI)'}"
                 for a in report.missing)
    return "\n".join(lines)
