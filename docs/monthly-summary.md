# Monthly fund-house summary

The overview sits above Monthly activity and follows the selected month.
It shows five of India's largest fund houses plus three additional
foreign-owned fund houses operating in India, without duplicating Nippon.

## Selection snapshot

The top five use AMFI's **April–June 2026 quarterly average AUM**, excluding
domestic fund-of-funds assets. This is a dated selection, not a live ranking.
Verified on 1 October 2026 using [AMFI's official Average AUM report](https://www.amfiindia.com/aum-data/average-aum):
select Fundwise, April 2026–March 2027, then April–June 2026. That was the
latest period offered by the report during verification.

| Rank | Fund house | Average AUM (₹ lakh, excluding domestic FoFs) |
| --- | --- | ---: |
| 1 | SBI Mutual Fund | 125,735,228.88 |
| 2 | ICICI Prudential Mutual Fund | 111,454,413.14 |
| 3 | HDFC Mutual Fund | 93,510,038.04 |
| 4 | Nippon India Mutual Fund | 75,151,871.19 |
| 5 | Kotak Mahindra Mutual Fund | 59,045,999.53 |

The three additional selections are Mirae Asset, Franklin Templeton and
HSBC. They are a selected group, not a claim about the three largest
foreign-owned houses. Ownership was checked against the fund houses'
own disclosures:

- [Mirae Asset Statement of Additional Information](https://www.miraeassetmf.co.in/docs/default-source/sai/sai_mirae-asset-mutual-fund-as-on-september-30-2025.pdf?sfvrsn=3b570e5_2): its South Korean sponsor indirectly holds the AMC's entire equity capital.
- [Franklin Templeton India terms](https://www.franklintempletonindia.com/terms-and-conditions): identifies the US parent of its Indian asset manager.
- [HSBC India governance](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/about-us/our-governance): the Indian AMC is ultimately wholly owned by HSBC Holdings plc.

The roster is maintained in `findit/web/fund_houses.py`. Update its dated
selection and this source record together when a new ranking is adopted.

## What the cards measure

- **Increased / reduced:** number of equities whose total shares held rose
  or fell across the fund house's compared schemes. Opposing trades within
  the same house offset one another. An unchanged holding is neither.
- **Biggest move:** the stock with the largest absolute estimated net
  trading value across those schemes, including new positions and exits.
  Share changes and their percentage relative to the prior share count
  accompany the stock name. This percentage is a change in the fund's
  holding; it is not the fund's percentage ownership of the company.
- **Estimated value:** uses the pipeline's month-end price convention,
  with full exits valued at their previous recorded value. It is not a
  reported execution price or an AUM change caused by price movements.
- **Scope:** equities only; the Active stock-pickers filter applies. The
  buying/selling, rows, instrument and column controls affect the activity
  table, rather than changing which eight houses appear in the overview.

Each comparison requires the immediately preceding calendar month and
actual holdings snapshots. Validation withholding on either month excludes
that scheme. Confirmed or inferred split/bonus ratios adjust the previous
share count using the existing corporate-action rules. Missing prices
exclude that stock from the biggest-move ranking and are disclosed.

Coverage counts are shown on every card. These are changes among the
loaded, comparable schemes, not necessarily the AMC's complete portfolio.
Missing disclosures and missing comparisons display an unavailable state,
never a zero-change claim. The local August dataset currently supports four
of the eight selected houses. Mirae lacks July; Kotak, Franklin Templeton
and HSBC have no loaded portfolios.
