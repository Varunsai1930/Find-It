# Readiness coverage audit

Baseline reviewed 1 October 2026. Original databases remain unchanged. Repairs use `real_data/readiness/working.db`.

The official eligible denominator is not yet established. Loaded counts below are active-classified funds, including those without domestic equity. They are not claims of complete coverage.

| House | July loaded | August loaded | August validated | August compared | Expected |
|---|---:|---:|---:|---:|---|
| SBI AMC | 39 | 40 | 40 | 39 | Unknown |
| ICICI Prudential AMC | 45 | 45 | 45 | 44 | Unknown |
| HDFC AMC | 2 | 30 | 30 | 2 | Unknown |
| Nippon India AMC | 26 | 26 | 26 | 23 | Unknown |
| Kotak Mahindra AMC | 0 | 0 | 0 | 0 | Unknown |
| Mirae Asset AMC | 0 | 15 | 15 | 0 | Unknown |
| Franklin Templeton AMC | 0 | 0 | 0 | 0 | Unknown |
| HSBC AMC | 0 | 0 | 0 | 0 | Unknown |

## HDFC diagnosis

Only MIDCAP (scheme 232) and HDFCT2 (233) have July snapshots. Both identities also exist in August and both passed validation. The other August portfolios lack July holdings in the original database; no identity merge or validation relaxation is indicated. The former 29 count included only active-classified portfolios with domestic equity; the new loaded count also exposes one active-classified portfolio without domestic equity.

## Source checks and remaining work

- [HDFC official archive](https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio): August listing available; July archive control being examined.
- [Mirae official archive](https://www.miraeassetmf.co.in/downloads/portfolio): 97 July workbooks obtained, original bytes and hashes retained separately; loading and validation pending.
- [Kotak official archive](https://www.kotakmf.com/Information/forms-and-downloads): access presents a CAPTCHA. No bypass attempted.
- [Franklin official reports](https://www.franklintempletonindia.com/reports): monthly disclosure control accessible; dated download discovery pending.
- [HSBC official library](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library): access timed out. This is an access limitation, not proof of source absence.

The baseline fund matrix is in the ignored local `real_data/readiness/baseline-audit.json`. `findit coverage` reproduces it and imports explicitly reviewed official inventories. Complete requires inventories for both months, every eligible fund mapped and compared, both validation passes, and no unexpected contributing fund. Partial/unavailable/unknown-completeness are distinct. All-market completeness remains unknown.

Phase B remains open until official expected scope is verified and missing sources are accounted for. No mentor-readiness or customer-validation acceptance result has been claimed.
