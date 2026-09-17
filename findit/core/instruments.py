"""Pure ISIN classification. No DB/IO."""


def classify_isin(isin: str) -> str:
    """Classify an ISIN into a coarse instrument bucket.

    Rules:
      - must start with IN else foreign
      - 3rd char 0 -> tbill_or_gsec, 9 -> sgsec, F -> mf_units
      - if 3rd char E then chars 8-9 (isin[7:9]):
          01 -> equity, 02 -> preference,
          07/08/09/10/11/12 -> ncd, 14/16 -> cp_or_cd,
          else debt_other
      - else other

    Normalizes upper/strip, handles non-str/short safely.
    """

    if not isinstance(isin, str):
        return "other"
    s = isin.strip().upper()
    if not s.startswith("IN"):
        return "foreign"
    if len(s) < 3:
        return "other"
    third = s[2]
    if third == "0":
        return "tbill_or_gsec"
    if third == "9":
        return "sgsec"
    if third == "F":
        return "mf_units"
    if third == "E":
        if len(s) < 9:
            return "debt_other"
        sub = s[7:9]
        if sub == "01":
            return "equity"
        if sub == "02":
            return "preference"
        if sub in ("07", "08", "09", "10", "11", "12"):
            return "ncd"
        if sub in ("14", "16"):
            return "cp_or_cd"
        return "debt_other"
    return "other"
