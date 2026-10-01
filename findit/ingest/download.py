"""Discover and download public monthly portfolios from the largest AMCs.

Discovery is separate from saving so a dry run can show the exact files first.
The existing intake module remains responsible for parsing and validation.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from findit.ingest.intake import load_registry

SBI_PAGE = "https://www.sbimf.com/portfolios"
SBI_LIST = "https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets"
ICICI_PAGE = "https://www.icicipruamc.com/media-center/downloads"
HDFC_PAGE = "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio"
NIPPON_PAGE = ("https://mf.nipponindiaim.com/investor-service/downloads/"
               "factsheet-portfolio-and-other-disclosures")
UTI_API = "https://www.utimf.com/api/get-consolidate-portfolio-disclosure"
DSP_PAGE = "https://www.dspim.com/mandatory-disclosures/portfolio-disclosures"
PPFAS_PAGE = "https://amc.ppfas.com/downloads/portfolio-disclosure/"
ABSL_PAGE = "https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio"
MIRAE_PAGE = "https://www.miraeassetmf.co.in/downloads/portfolio"
FRANKLIN_PAGE = "https://www.franklintempletonindia.com/reports"
SUPPORTED = ("SBI AMC", "ICICI Prudential AMC", "HDFC AMC", "Nippon India AMC",
             "UTI AMC", "Aditya Birla Sun Life AMC", "Mirae Asset AMC", "DSP AMC",
             "PPFAS AMC", "Franklin Templeton AMC")
MANUAL = ("Kotak Mahindra AMC", "Axis AMC")
SUFFIXES = {".xlsx", ".xls", ".zip"}
MANIFEST = ".findit-download.json"


@dataclass(frozen=True)
class SourceFile:
    amc: str
    name: str
    url: str
    browser: bool = False


def _month(month: str) -> tuple[int, int, str]:
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    year, number = map(int, month.split("-"))
    return year, number, calendar.month_name[number]


def _safe_name(name: str) -> str:
    clean = Path(name).name
    if clean != name or clean in {"", ".", ".."} or Path(clean).suffix.lower() not in SUFFIXES:
        raise ValueError(f"unsafe or unsupported download filename: {name!r}")
    return clean


def _one(candidates: list[SourceFile], amc: str, month: str) -> list[SourceFile]:
    if len(candidates) != 1:
        raise ValueError(f"{amc}: expected one monthly file for {month}, found {len(candidates)}")
    return candidates


def discover_sbi(month: str, session: requests.Session) -> list[SourceFile]:
    _, _, month_name = _month(month)
    response = session.post(SBI_LIST, json={"FundId": "", "PSYear": "", "PSMonth": "",
                                            "PSFrequency": "Monthly"},
                            headers={"Referer": SBI_PAGE}, timeout=30)
    response.raise_for_status()
    candidates = []
    for row in BeautifulSoup(response.text, "html.parser").select("tr"):
        anchor = row.select_one("td a[href]")
        if anchor is None:
            continue
        title = anchor.get_text(" ", strip=True).lower()
        if ("all schemes monthly portfolio" in title and
                re.search(rf"\b{month_name.lower()}\s+{month[:4]}\b", title)):
            url = anchor["href"]
            candidates.append(SourceFile("SBI AMC", _safe_name(Path(urlparse(url).path).name), url))
    return _one(candidates, "SBI AMC", month)


def discover_nippon(month: str, session: requests.Session) -> list[SourceFile]:
    year, number, _ = _month(month)
    response = session.get(NIPPON_PAGE, timeout=30)
    response.raise_for_status()
    candidates = []
    for anchor in BeautifulSoup(response.text, "html.parser").select("a[href]"):
        url = urljoin(NIPPON_PAGE, anchor["href"])
        name = Path(urlparse(url).path).name
        if not name.lower().startswith("nimf-monthly-portfolio-"):
            continue
        # Publication filenames vary between Jun/June and Jul/July.
        match = re.search(r"-(\d{1,2})-([a-z]+)-(\d{2})\.(xls|xlsx)$", name, re.I)
        if not match:
            continue
        day, named_month, short_year, _ = match.groups()
        if (int(day) == calendar.monthrange(year, number)[1]
                and named_month.lower() in {calendar.month_name[number].lower(),
                                            calendar.month_abbr[number].lower()}
                and int(short_year) == year % 100):
            candidates.append(SourceFile("Nippon India AMC", _safe_name(name), url))
    return _one(candidates, "Nippon India AMC", month)


def discover_uti(month: str, session: requests.Session) -> list[SourceFile]:
    year, _, month_name = _month(month)
    response = session.get(UTI_API, params={"year": year, "month": month_name}, timeout=30)
    response.raise_for_status()
    candidates = []
    for row in response.json().get("rows", []):
        if (str(row.get("year")) == str(year) and
                str(row.get("month", "")).casefold() == month_name.casefold() and
                "consolidated portfolio" in row.get("name", "").casefold()):
            url = row.get("url") or row.get("doc")
            if url:
                candidates.append(SourceFile("UTI AMC", _safe_name(Path(urlparse(url).path).name), url))
    return _one(candidates, "UTI AMC", month)


def discover_dsp(month: str, session: requests.Session) -> list[SourceFile]:
    year, number, month_name = _month(month)
    response = session.get(DSP_PAGE, timeout=30)
    response.raise_for_status()
    date = f"{month_name} {calendar.monthrange(year, number)[1]}, {year}"
    candidates = []
    for anchor in BeautifulSoup(response.text, "html.parser").select("a[href]"):
        title = anchor.get_text(" ", strip=True)
        if title.casefold() == f"Portfolio Details as on {date}".casefold():
            url = urljoin(DSP_PAGE, anchor["href"])
            candidates.append(SourceFile("DSP AMC", _safe_name(Path(urlparse(url).path).name), url))
    return _one(candidates, "DSP AMC", month)


def discover_ppfas(month: str, session: requests.Session) -> list[SourceFile]:
    year, number, month_name = _month(month)
    response = session.get(PPFAS_PAGE, timeout=30)
    response.raise_for_status()
    pattern = re.compile(rf"(?:^|_)Monthly_Portfolio_Report_{month_name}_"
                         rf"{calendar.monthrange(year, number)[1]}_{year}\.", re.I)
    candidates = []
    for anchor in BeautifulSoup(response.text, "html.parser").select("a[href]"):
        if anchor.get_text(" ", strip=True).casefold() != "consolidated":
            continue
        url = urljoin(PPFAS_PAGE, anchor["href"])
        name = Path(urlparse(url).path).name
        if pattern.search(name):
            candidates.append(SourceFile("PPFAS AMC", _safe_name(name), url))
    return _one(candidates, "PPFAS AMC", month)


def discover_absl(month: str, page) -> list[SourceFile]:
    year, number, month_name = _month(month)
    date = f"{month_name} {calendar.monthrange(year, number)[1]}, {year}"
    page.goto(ABSL_PAGE, wait_until="domcontentloaded", timeout=45000)
    label = page.get_by_text(f"Monthly Portfolios as on {date}", exact=True)
    label.wait_for(timeout=30000)
    links = label.evaluate("e => [...e.closest('li').querySelectorAll('a[href]')].map(a => a.href)")
    return _one([SourceFile("Aditya Birla Sun Life AMC",
                            _safe_name(Path(urlparse(url).path).name), url)
                 for url in links if Path(urlparse(url).path).suffix.lower() in SUFFIXES],
                "Aditya Birla Sun Life AMC", month)


def discover_mirae(month: str, page) -> list[SourceFile]:
    year, number, month_name = _month(month)
    page.goto(MIRAE_PAGE, wait_until="domcontentloaded", timeout=45000)
    listing = page.locator("#nav-portfolio-tab1")
    listing.locator("#nav-portfolio1 a[href]").first.wait_for(timeout=30000)
    date = rf"\b{calendar.monthrange(year, number)[1]}(?:st|nd|rd|th)? {month_name} {year}\b"
    found = {}
    for _ in range(100):
        links = listing.locator("#nav-portfolio1 a[href]").evaluate_all(
            "els => els.map(a => ({title: a.textContent.trim(), url: a.getAttribute('href')}))")
        if not links:
            raise ValueError("Mirae Asset AMC: empty portfolio page")
        matched = 0
        for item in links:
            if re.search(date, item["title"], re.I):
                href = item["url"]
                if href.startswith("docs/"):
                    href = "/" + href
                url = urljoin(MIRAE_PAGE, href)
                name = _safe_name(Path(urlparse(url).path).name)
                found[name] = SourceFile("Mirae Asset AMC", name, url)
                matched += 1
        if matched == 0 and found:
            break
        next_page = listing.locator("li.next")
        if "disabled" in (next_page.get_attribute("class") or "").split():
            break
        before = urljoin(MIRAE_PAGE, links[0]["url"])
        next_page.locator("a").click()
        page.wait_for_function("([selector, old]) => document.querySelector(selector)?.href !== old",
                               arg=["#nav-portfolio1 a[href]", before], timeout=30000)
    else:
        raise ValueError("Mirae Asset AMC: pagination did not finish")
    if not found:
        raise ValueError(f"Mirae Asset AMC: no monthly workbooks for {month}")
    return list(found.values())


def _browser(pw):
    # An installed Chrome works without an extra browser download. Playwright's
    # bundled Chromium remains available on machines without Chrome.
    try:
        return pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return pw.chromium.launch(headless=True)


def discover_icici(month: str, page) -> list[SourceFile]:
    _, _, month_name = _month(month)
    page.goto(ICICI_PAGE, wait_until="domcontentloaded", timeout=30000)
    with page.expect_response(lambda r: "/nms/v1/downloads/files" in r.url, timeout=30000) as event:
        page.get_by_role("tab", name="Other Scheme Disclosures").click()
    response = event.value
    if not response.ok:
        raise ValueError(f"ICICI portfolio listing returned HTTP {response.status}")
    files = response.json()["success"]["data"]["files"]
    title = f"Monthly Portfolio Disclosure {month_name} {month[:4]}"
    candidates = []
    for item in files:
        if item.get("title", {}).get("text", "").casefold() != title.casefold():
            continue
        path = item["url"]
        url = urljoin(ICICI_PAGE, "/blob/" + path.lstrip("/"))
        # Quote spaces while retaining the rest of the server's path.
        url = url.replace(" ", "%20")
        candidates.append(SourceFile("ICICI Prudential AMC",
                                     _safe_name(Path(urlparse(url).path).name), url))
    return _one(candidates, "ICICI Prudential AMC", month)


def discover_icici_archive(month: str, session: requests.Session) -> list[SourceFile]:
    """Use the public archive's dated naming when the dynamic listing fails."""
    year, number, month_name = _month(month)
    name = f"Monthly-Portfolio-Disclosure-{month_name}-{year}.zip"
    for directory in dict.fromkeys((month_name, calendar.month_abbr[number],
                                    "Sept" if number == 9 else month_name)):
        url = ("https://www.icicipruamc.com/blob/downloads/Files/"
               f"Monthly%20Portfolio%20Disclosures/{year}/{directory}/{name}")
        try:
            with session.get(url, headers={"Range": "bytes=0-7"},
                             stream=True, timeout=15) as response:
                if response.status_code in (200, 206) and next(
                        response.iter_content(chunk_size=8), b"").startswith(b"PK\x03\x04"):
                    return [SourceFile("ICICI Prudential AMC", name, url)]
        except requests.RequestException:
            continue
    raise ValueError(f"ICICI Prudential AMC: no public archive for {month}")


def discover_hdfc(month: str, page) -> list[SourceFile]:
    year, number, month_name = _month(month)
    page.goto(HDFC_PAGE, wait_until="domcontentloaded", timeout=30000)
    date = f"{calendar.monthrange(year, number)[1]} {month_name} {year}"
    selector = page.get_by_role("button", name=re.compile(
        r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*-\s*\d{4}"))
    selector.wait_for(timeout=30000)
    if selector.inner_text().strip() != f"{calendar.month_abbr[number]} - {year}":
        current = selector.inner_text()
        selector.click()
        menu = page.locator('div[class*="calenderbg"]')
        if str(year) not in current:
            menu.get_by_text(str(year), exact=True).click()
        menu.locator('div[class*="greybg"]').click()
        menu.get_by_text(calendar.month_abbr[number], exact=True).click()
        page.get_by_role("link", name=re.compile(r"Monthly HDFC .*" + re.escape(date))).first.wait_for(timeout=30000)
    links = page.locator('a[href$=".xlsx"]').evaluate_all(
        "els => els.map(a => ({name: a.textContent.trim(), url: a.href}))")
    candidates = []
    for link in links:
        name = link["name"]
        if name.lower().startswith("monthly hdfc ") and date.lower() in name.lower():
            candidates.append(SourceFile("HDFC AMC", _safe_name(name), link["url"], True))
    if not candidates:
        raise ValueError(f"HDFC AMC: no monthly workbooks found for {month}")
    if len({item.name for item in candidates}) != len(candidates):
        raise ValueError("HDFC AMC: duplicate workbook names on disclosure page")
    return candidates


def discover_franklin(month: str, page) -> list[SourceFile]:
    year, number, month_name = _month(month)
    page.goto(FRANKLIN_PAGE, wait_until="domcontentloaded", timeout=45000)
    page.get_by_role("tab", name="Monthly Portfolio Disclosure", exact=True).click()
    # Franklin reports the last trading day in some months (27 Feb 2026),
    # rather than the final calendar day. Match the named reporting month.
    link = page.get_by_role("link", name=re.compile(
        rf"^ISIN as on \d{{1,2}} {re.escape(month_name)} {year}$"))
    link.wait_for(timeout=30000)
    url = urljoin(FRANKLIN_PAGE, link.get_attribute("href"))
    return [SourceFile("Franklin Templeton AMC", _safe_name(Path(urlparse(url).path).name), url)]


def discover(month: str, amc: str, session: requests.Session, page=None) -> list[SourceFile]:
    _month(month)
    if amc == "SBI AMC":
        return discover_sbi(month, session)
    if amc == "Nippon India AMC":
        return discover_nippon(month, session)
    if amc == "UTI AMC":
        return discover_uti(month, session)
    if amc == "DSP AMC":
        return discover_dsp(month, session)
    if amc == "PPFAS AMC":
        return discover_ppfas(month, session)
    if amc == "ICICI Prudential AMC":
        try:
            return discover_icici_archive(month, session)
        except ValueError:
            pass
        if page is not None:
            return discover_icici(month, page)
        raise ValueError(f"ICICI Prudential AMC: no archive for {month}; browser required")
    if page is None:
        raise ValueError(f"{amc}: browser is required to discover downloads")
    if amc == "HDFC AMC":
        return discover_hdfc(month, page)
    if amc == "Aditya Birla Sun Life AMC":
        return discover_absl(month, page)
    if amc == "Mirae Asset AMC":
        return discover_mirae(month, page)
    if amc == "Franklin Templeton AMC":
        return discover_franklin(month, page)
    raise ValueError(f"unsupported AMC: {amc}")


def _check_file(path: Path) -> Path:
    signature = path.open("rb").read(8)
    if signature.startswith(b"PK\x03\x04"):
        if not zipfile.is_zipfile(path):
            raise ValueError(f"invalid ZIP/workbook: {path.name}")
        if path.suffix.lower() == ".xls":
            renamed = path.with_suffix(".xlsx")
            path.rename(renamed)
            return renamed
    elif path.suffix.lower() != ".xls" or signature != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise ValueError(f"response is not an Excel workbook or ZIP: {path.name}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_files(folder: Path, files: list[SourceFile], amc: str) -> list[Path]:
    manifest_path = folder / MANIFEST
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        expected = {item["name"]: item for item in manifest.get("files", [])}
        requested = {item.name if item.name in expected else
                     Path(item.name).with_suffix(".xlsx").name for item in files}
        if (manifest.get("amc") == amc and
                requested == set(expected) and len(requested) == len(files) and
                {p.name for p in folder.iterdir() if p.name != "_unzipped"} ==
                set(expected) | {MANIFEST} and
                all((folder / name).is_file() and
                    _sha256(folder / name) == info["sha256"]
                    for name, info in expected.items())):
            return [folder / name for name in sorted(expected)]
    raise FileExistsError(f"{folder} has existing or changed files; review them manually")


def _download_file(item: SourceFile, target: Path, session: requests.Session, page) -> Path:
    if item.browser:
        if page is None:
            raise ValueError(f"{item.amc}: browser download unavailable")
        locator = page.locator(f'a[href="{item.url}"]')
        with page.expect_download(timeout=60000) as event:
            locator.first.click(force=True)
        event.value.save_as(target)
    else:
        with session.get(item.url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with target.open("wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    out.write(chunk)
    return _check_file(target)


def save_files(files: list[SourceFile], month_dir: Path, session: requests.Session,
               page=None) -> list[Path]:
    """Stage and verify a whole AMC, then publish it in one directory move."""
    if not files or len({f.amc for f in files}) != 1:
        raise ValueError("save_files needs one nonempty AMC batch")
    amc = files[0].amc
    if amc not in {a.amc for a in load_registry()}:
        raise ValueError(f"AMC is absent from registry: {amc}")
    folder = month_dir / amc
    if len({f.name for f in files}) != len(files):
        raise ValueError(f"{amc}: duplicate download names")
    if folder.exists() and any(folder.iterdir()):
        return _completed_files(folder, files, amc)
    month_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".findit-download-", dir=month_dir) as temporary:
        staged = [_download_file(item, Path(temporary) / _safe_name(item.name),
                                 session, page) for item in files]
        manifest = {"amc": amc, "files": [
            {"name": path.name, "sha256": _sha256(path), "source_url": item.url,
             "retrieved_at": datetime.now(timezone.utc).isoformat(), "published_at": None}
            for item, path in zip(files, staged, strict=True)]}
        (Path(temporary) / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        if folder.exists():
            folder.rmdir()  # only an empty pre-existing directory is safe to replace
        Path(temporary).replace(folder)
        return [folder / path.name for path in staged]
