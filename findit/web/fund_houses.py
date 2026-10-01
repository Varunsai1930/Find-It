"""The monthly overview's dated, explicitly selected fund-house roster.

Top five: AMFI April–June 2026 AAUM, excluding domestic FoFs. Nippon is
already in that group; the foreign-owned group adds three other houses.
Ownership sources and the ranking snapshot are recorded in docs/monthly-summary.md.
"""

AUM_SOURCE = "https://www.amfiindia.com/aum-data/average-aum"
AUM_PERIOD = "April–June 2026"

FUND_GROUPS = (
    ("India’s five largest fund houses", (
        ("SBI Mutual Fund", ("SBI AMC", "SBI Mutual Fund")),
        ("ICICI Prudential Mutual Fund", ("ICICI Prudential AMC", "ICICI Prudential Mutual Fund")),
        ("HDFC Mutual Fund", ("HDFC AMC", "HDFC Mutual Fund")),
        ("Nippon India Mutual Fund", ("Nippon India AMC", "Nippon India Mutual Fund")),
        ("Kotak Mahindra Mutual Fund", ("Kotak Mahindra AMC", "Kotak AMC", "Kotak Mahindra Mutual Fund")),
    )),
    ("Three more foreign-owned fund houses in India", (
        ("Mirae Asset Mutual Fund", ("Mirae Asset AMC", "Mirae Asset Mutual Fund")),
        ("Franklin Templeton Mutual Fund", ("Franklin Templeton AMC", "Franklin Templeton Mutual Fund")),
        ("HSBC Mutual Fund", ("HSBC AMC", "HSBC Mutual Fund")),
    )),
)


def selected_groups(activity: dict) -> list[dict]:
    """Include all eight houses even when a disclosure has not been loaded."""
    houses = activity["houses"]
    return [{"title": title, "funds": [
        {"name": name, "amc": next((amc for amc in aliases if amc in houses), aliases[0]), **next((houses[amc] for amc in aliases if amc in houses),
                              {"loaded": 0, "compared": 0, "biggest": None,
                               "validated_count": 0, "expected": None,
                               "coverage_state": "unavailable", "inventory_known": False})}
        for name, aliases in funds]} for title, funds in FUND_GROUPS]
