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
