"""Names for display; stored disclosure names and identifiers stay intact."""

import re


def stock_name(name: str | None) -> str:
    """Remove the disclosure's equity marker, preserving the company name."""
    return re.sub(r"^EQ\s*-\s*", "", (name or "").strip(), flags=re.IGNORECASE).strip()


def scheme_name(name: str | None, title: str | None = None) -> str:
    """Prefer the workbook's full fund title over its short sheet code."""
    label = (title or "").split(" (")[0].strip()
    return label or (name or "")
