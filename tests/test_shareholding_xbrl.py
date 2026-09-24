"""BSE shareholding history: XBRL instance parsing, MF %, publication times.

Synthetic documents shaped like BSE's real 2016 and 2020+ filings; no network.
"""

import pytest

import fetch_shareholding as fs


def _xbrl(facts: dict, style: str = "_ContextI") -> str:
    """An XBRL instance with one percentage fact per category context."""
    body = "\n".join(
        f'  <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares '
        f'contextRef="{name}{style}" unitRef="pure" decimals="2">{value}'
        f'</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>'
        for name, value in facts.items())
    return (
        '﻿<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xbrli:xbrl xmlns:in-bse-shp="http://www.bseindia.com/xbrl/shp/2022-09-30/in-bse-shp" '
        'xmlns:xbrli="http://www.xbrl.org/2003/instance">\n'
        f"{body}\n"
        # A named holder's context must never be read as a category.
        '  <in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares '
        'contextRef="OthersIndianShareholders_Context15">11.24'
        '</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares>\n'
        "</xbrli:xbrl>")


RECENT = {
    "ShareholdingOfPromoterAndPromoterGroup": "50.11",
    "PublicShareholding": "49.89",
    "MutualFundsOrUti": "9.21",
    "Banks": "0.06",
    "InsuranceCompanies": "8.99",
    "OtherFinancialInstitutions": "0.00",
    "ProvidentFundsOrPensionFunds": "0.93",  # outside the DII definition
    "InstitutionsForeignPortfolioInvestorCatergoryOne": "18.25",
    "InstitutionsForeignPortfolioInvestorCatergoryTwo": "0.72",
    "InstitutionsForeign": "19.06",  # includes FDI: never used as FII
}


def test_recent_xbrl_layout():
    out = fs.parse_bse_shareholding_xbrl(_xbrl(RECENT))
    assert out == {"promoter_pct": 50.11, "public_pct": 49.89, "mf_pct": 9.21,
                   "fii_pct": 18.97, "dii_pct": pytest.approx(18.26)}


def test_2016_xbrl_layout_with_bare_instant_suffix():
    facts = {
        "ShareholdingOfPromoterAndPromoterGroup": "46.49",
        "PublicShareholding": "53.51",
        "MutualFundsOrUti": "2.93",
        "FinancialInstitutionOrBanks": "0.15",
        "InsuranceCompanies": "10.15",
        "InstitutionsForeignPortfolioInvestor": "8.24",
    }
    out = fs.parse_bse_shareholding_xbrl(_xbrl(facts, style="I"))
    assert out["mf_pct"] == 2.93
    assert out["fii_pct"] == 8.24
    assert out["dii_pct"] == pytest.approx(13.23)


def test_fpi_categories_are_not_added_to_their_aggregate():
    facts = dict(RECENT, InstitutionsForeignPortfolioInvestor="18.97")
    assert fs.parse_bse_shareholding_xbrl(_xbrl(facts))["fii_pct"] == 18.97


def test_missing_mf_line_is_no_data_not_zero():
    facts = {k: v for k, v in RECENT.items() if k != "MutualFundsOrUti"}
    assert fs.parse_bse_shareholding_xbrl(_xbrl(facts))["mf_pct"] is None


def test_missing_promoter_total_fails_loudly():
    facts = {k: v for k, v in RECENT.items() if k != "ShareholdingOfPromoterAndPromoterGroup"}
    with pytest.raises(fs.ShareholdingFetchError, match="promoter/public"):
        fs.parse_bse_shareholding_xbrl(_xbrl(facts))


def test_conflicting_duplicate_facts_fail_loudly():
    doc = _xbrl(RECENT).replace(
        "</xbrli:xbrl>",
        '<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares '
        'contextRef="MutualFundsOrUti_ContextI">4.00'
        '</in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares></xbrli:xbrl>')
    with pytest.raises(fs.ShareholdingFetchError, match="Conflicting"):
        fs.parse_bse_shareholding_xbrl(doc)


def test_out_of_range_percentage_fails_loudly():
    with pytest.raises(fs.ShareholdingFetchError, match="outside 0-100"):
        fs.parse_bse_shareholding_xbrl(_xbrl(dict(RECENT, MutualFundsOrUti="921")))


def test_dispatch_picks_the_parser_by_document_shape():
    assert fs.parse_shareholding_document(_xbrl(RECENT).encode("utf-8"))["mf_pct"] == 9.21
    html = (
        "<html><body><table>"
        "<tr><td>Total Shareholding of Promoter and Promoter Group</td>"
        "<td><ix:nonFraction name='in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares'>"
        "50.00</ix:nonFraction></td></tr>"
        "<tr><td>(B)=(B)(1)+(B)(2)+(B)(3)</td>"
        "<td><ix:nonFraction name='in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares'>"
        "50.00</ix:nonFraction></td></tr>"
        "<tr><td>(a)</td><td>Mutual Funds</td>"
        "<td><ix:nonFraction name='in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares'>"
        "9.78</ix:nonFraction></td></tr>"
        "</table></body></html>")
    out = fs.parse_shareholding_document(html)
    assert out["mf_pct"] == 9.78
    assert out["dii_pct"] == 9.78


@pytest.mark.parametrize("filing, expected", [
    ({"D": "2026-07-16T19:24:00"}, "2026-07-16T19:24"),
    ({"broadcastTime": "Jul 16 2026  7:24PM"}, "2026-07-16T19:24"),
    ({"D": "garbage", "broadcastTime": "Jan 20 2018 10:32AM"}, "2018-01-20T10:32"),
    ({}, None),
])
def test_filing_publication_time(filing, expected):
    assert fs.filing_published_at(filing) == expected


class _IndexSession:
    def __init__(self, table):
        self.table = table

    def get(self, url, **kwargs):
        table = self.table

        class R:
            def json(self):
                return {"Table": table}
        return R()


def _filing(end, d, attachment="/XBRL1/x_SHP.xml"):
    return {"EndDate": f"{end}T00:00:00", "D": f"{d}T18:00:00", "IsXBRL": 1,
            "XBRLAttachment": attachment}


def test_history_returns_every_usable_filing_with_its_publication_time():
    table = [_filing("2016-03-31", "2016-04-28", attachment="/XBRL1/"),  # bare dir
             _filing("2019-03-31", "2019-05-15"),
             _filing("2019-06-30", "2019-07-21"),
             _filing("2019-09-30", "2019-10-19")]
    everything = fs.get_bse_filings(_IndexSession(table), "500325", max_filings=None)
    assert [f["quarter_end"] for f in everything] == ["2019-09-30", "2019-06-30", "2019-03-31"]
    assert everything[-1]["published_at"] == "2019-05-15T18:00"
    newest_two = fs.get_bse_filings(_IndexSession(table), "500325")
    assert [f["quarter_end"] for f in newest_two] == ["2019-09-30", "2019-06-30"]


def test_no_promoter_group_reads_as_zero_only_when_public_holds_everything():
    widely_held = {k: v for k, v in RECENT.items()
                   if k != "ShareholdingOfPromoterAndPromoterGroup"}
    widely_held.update(PublicShareholding="100.00", ShareholdingPattern="100.00")
    assert fs.parse_bse_shareholding_xbrl(_xbrl(widely_held))["promoter_pct"] == 0.0
    del widely_held["ShareholdingPattern"]  # 2016 filings carry no total line
    assert fs.parse_bse_shareholding_xbrl(_xbrl(widely_held, style="I"))["promoter_pct"] == 0.0
    widely_held["PublicShareholding"] = "60.00"  # 40% belongs to someone unnamed
    with pytest.raises(fs.ShareholdingFetchError, match="promoter/public"):
        fs.parse_bse_shareholding_xbrl(_xbrl(widely_held))
    widely_held["EmployeeBenefitsTrusts"] = "40.00"  # ...unless trusts hold it (IEX)
    assert fs.parse_bse_shareholding_xbrl(_xbrl(widely_held))["promoter_pct"] == 0.0


def test_2025_taxonomy_states_shares_as_fractions():
    # NSE archive layout: fractions of 1, "UTI" capitalised, "Category" spelled right.
    facts = {"ShareholdingOfPromoterAndPromoterGroup": "0.5048", "PublicShareholding": "0.4952",
             "ShareholdingPattern": "1", "MutualFundsOrUTI": "0.1011", "Banks": "0.0005",
             "InsuranceCompanies": "0.092", "OtherFinancialInstitutions": "0",
             "InstitutionsForeignPortfolioInvestorCategoryOne": "0.1652",
             "InstitutionsForeignPortfolioInvestorCategoryTwo": "0.0054"}
    assert fs.parse_bse_shareholding_xbrl(_xbrl(facts)) == {
        "promoter_pct": 50.48, "public_pct": 49.52, "mf_pct": 10.11,
        "fii_pct": 17.06, "dii_pct": 19.36}


def test_a_total_that_is_neither_fraction_nor_percent_fails_loudly():
    with pytest.raises(fs.ShareholdingFetchError, match="neither a fraction"):
        fs.parse_bse_shareholding_xbrl(_xbrl(dict(RECENT, ShareholdingPattern="50")))


def test_2016_no_promoter_filing_with_non_public_holders_outside_the_total():
    # ITC, June 2016: public is the whole 100%, and the DR/trust line (0.22)
    # is stated outside it, as 2016 filings did.
    facts = {"PublicShareholding": "100", "MutualFundsOrUti": "2.51",
             "SharesHeldByNonPromoterNonPublicShareholders": "0.22",
             "InstitutionsForeignPortfolioInvestor": "20.6"}
    out = fs.parse_bse_shareholding_xbrl(_xbrl(facts, style="I"))
    assert out["promoter_pct"] == 0.0 and out["mf_pct"] == 2.51


class _NseSession(_IndexSession):
    """NSE's index is a bare list, not BSE's {"Table": [...]}."""

    def get(self, url, **kwargs):
        rows = self.table

        class R:
            def json(self):
                return rows
        return R()


def _nse_row(date, broadcast, xbrl="https://nsearchives.nseindia.com/corporate/xbrl/SHP_1_WEB.xml"):
    return {"date": date, "broadcastDate": broadcast, "xbrl": xbrl}


def test_nse_index_keeps_the_original_filing_of_a_revised_quarter():
    rows = [
        _nse_row("30-JUN-2026", "16-JUL-2026 19:24:44", "https://x/original.xml"),
        _nse_row("30-JUN-2026", "02-SEP-2026 10:00:00", "https://x/revised.xml"),
        _nse_row("31-MAR-2026", "21-APR-2026 13:25:14"),
        _nse_row("31-DEC-2025", "not a date"),          # never guessed
        _nse_row("30-SEP-2025", "20-OCT-2025 18:00:00", xbrl="-"),  # no XBRL file
    ]
    filings = fs.get_nse_filings(_NseSession(rows), "X", max_filings=None)
    assert [(f["quarter_end"], f["published_at"]) for f in filings] == [
        ("2026-06-30", "2026-07-16T19:24"), ("2026-03-31", "2026-04-21T13:25")]
    assert filings[0]["url"] == "https://x/original.xml"
    assert len(fs.get_nse_filings(_NseSession(rows), "X")) == 2


def test_unknown_source_is_rejected(tmp_path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("CREATE TABLE shareholding_quarterly (isin TEXT, quarter_end TEXT)")
    with pytest.raises(ValueError, match="source must be one of"):
        fs.fetch_shareholding(conn, None, tmp_path, source="yahoo")
    conn.close()
