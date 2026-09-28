"""Downloader discovery and file safety without network access."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from findit.ingest import download


class Response:
    def __init__(self, body: bytes, status: int = 200):
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(self.status_code)

    def iter_content(self, chunk_size=8):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def json(self):
        import json
        return json.loads(self.content)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class Session:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **_):
        return self.responses[url]

    def post(self, url, **_):
        return self.responses[url]


def test_sbi_selects_only_the_requested_month_and_consolidated_file():
    html = b"""<table>
      <tr><td><a href="https://www.sbimf.com/july.xlsx">All Schemes Monthly Portfolio - as on 31st July 2026</a></td></tr>
      <tr><td><a href="https://www.sbimf.com/august.xlsx">All Schemes Monthly Portfolio - as on 31st August 2026</a></td></tr>
      <tr><td><a href="https://www.sbimf.com/other.xlsx">SBI Flexi Cap August 2026</a></td></tr>
    </table>"""
    files = download.discover_sbi("2026-08", Session({download.SBI_LIST: Response(html)}))
    assert [(f.amc, f.name, f.url) for f in files] == [
        ("SBI AMC", "august.xlsx", "https://www.sbimf.com/august.xlsx")]


def test_nippon_excludes_fortnightly_and_other_months():
    html = b"""<a href="/NIMF-FORTNIGHTLY-PORTFOLIO-31-Aug-26.xls">one</a>
       <a href="/NIMF-MONTHLY-PORTFOLIO-31-July-26.xls">two</a>
       <a href="/NIMF-MONTHLY-PORTFOLIO-31-Aug-26.xls">three</a>"""
    files = download.discover_nippon("2026-08", Session({download.NIPPON_PAGE: Response(html)}))
    assert len(files) == 1
    assert files[0].name == "NIMF-MONTHLY-PORTFOLIO-31-Aug-26.xls"


def test_icici_archive_probes_month_folder_and_checks_zip_signature():
    first = ("https://www.icicipruamc.com/blob/downloads/Files/"
             "Monthly%20Portfolio%20Disclosures/2026/August/"
             "Monthly-Portfolio-Disclosure-August-2026.zip")
    second = first.replace("/August/", "/Aug/")
    files = download.discover_icici_archive("2026-08", Session({
        first: Response(b"not a workbook", 404), second: Response(b"PK\x03\x04abcd", 206)}))
    assert len(files) == 1 and files[0].url == second


def test_mislabelled_xls_is_renamed_and_invalid_response_rejected(tmp_path):
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook />")
    path = tmp_path / "nippon.xls"
    path.write_bytes(payload.getvalue())
    fixed = download._check_file(path)
    assert fixed.name == "nippon.xlsx" and fixed.exists() and not path.exists()
    bad = tmp_path / "error.xlsx"
    bad.write_text("<html>blocked</html>")
    with pytest.raises(ValueError, match="not an Excel"):
        download._check_file(bad)


def test_existing_amc_folder_is_left_untouched(tmp_path):
    folder = tmp_path / "2026-08" / "SBI AMC"
    folder.mkdir(parents=True)
    existing = folder / "user.xlsx"
    existing.write_bytes(b"user data")
    files = [download.SourceFile("SBI AMC", "month.xlsx", "https://example.test/month.xlsx")]
    with pytest.raises(FileExistsError):
        download.save_files(files, Path(tmp_path / "2026-08"), Session({}))
    assert existing.read_bytes() == b"user data"


def test_new_exact_month_sources_exclude_other_files():
    uti = b'{"rows":[{"name":"Consolidated Portfolio August 2026","year":2026,"month":"August","url":"https://u.test/aug.zip"},{"name":"Consolidated Portfolio July 2026","year":2026,"month":"July","url":"https://u.test/jul.zip"}]}'
    dsp = b'<a href="/aug.zip">Portfolio Details as on August 31, 2026</a><a href="/debt.zip">Fortnightly Portfolios as on August 31, 2026</a>'
    ppfas = b'<a href="/PPFAS_Monthly_Portfolio_Report_August_31_2026.xls">Consolidated</a><a href="/PPFAS_Monthly_Portfolio_Report_July_31_2026.xls">Consolidated</a>'
    session = Session({download.UTI_API: Response(uti), download.DSP_PAGE: Response(dsp),
                       download.PPFAS_PAGE: Response(ppfas)})
    assert download.discover_uti("2026-08", session)[0].name == "aug.zip"
    assert download.discover_dsp("2026-08", session)[0].name == "aug.zip"
    assert download.discover_ppfas("2026-08", session)[0].name.endswith("August_31_2026.xls")


def test_completed_download_rerun_checks_manifest_and_preserves_files(tmp_path):
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("book.xlsx", b"test")
    url = "https://example.test/aug.zip"
    files = [download.SourceFile("PPFAS AMC", "aug.zip", url)]
    session = Session({url: Response(payload.getvalue())})
    first = download.save_files(files, tmp_path, session)
    assert first[0].exists()
    assert download.save_files(files, tmp_path, session) == first
    expanded = tmp_path / "PPFAS AMC" / "_unzipped"
    expanded.mkdir()
    (expanded / "book.xlsx").write_bytes(b"parsed copy")
    assert download.save_files(files, tmp_path, session) == first
    first[0].write_bytes(b"changed")
    with pytest.raises(FileExistsError):
        download.save_files(files, tmp_path, session)
