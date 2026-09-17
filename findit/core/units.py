"""Pure NAV unit normalization. No DB/IO."""

from typing import Iterable, Optional


def normalize_nav(values: Iterable[float], scale: Optional[str] = None):
    """Normalize raw NAV weights to canonical percent values.

    Returns (list[float], scale: str, warnings: list).

    Scale inference uses ONLY the sum of raw values:
      - 0.95..1.05 -> fraction (multiply each by 100)
      - 95..105    -> percent (as-is)
      - else raise ValueError (never silently guess)

    An explicit scale param ("fraction" | "percent") overrides inference.

    Idempotent-safe contract: caller always passes raw values; this
    function never tries to detect already-normalized inputs beyond the
    explicit sum rule above. Pure function: does not mutate its input.
    """

    if values is None:
        raise ValueError("ambiguous NAV scale: values is None")

    try:
        raw = list(values)
    except TypeError as exc:
        raise ValueError(f"ambiguous NAV scale: values not iterable: {exc}") from exc

    if len(raw) == 0:
        raise ValueError("ambiguous NAV scale: empty values")

    try:
        nums = [float(v) for v in raw]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ambiguous NAV scale: non-numeric value: {exc}") from exc

    warnings: list = []

    if scale is not None:
        if not isinstance(scale, str):
            raise ValueError(f"ambiguous NAV scale: bad scale param {scale!r}")
        s = scale.strip().lower()
        if s == "fraction":
            return [v * 100.0 for v in nums], "fraction", warnings
        if s == "percent":
            return [float(v) for v in nums], "percent", warnings
        raise ValueError(f"ambiguous NAV scale: unknown scale {scale!r}")

    total = sum(nums)
    if 0.95 <= total <= 1.05:
        return [v * 100.0 for v in nums], "fraction", warnings
    if 95.0 <= total <= 105.0:
        return [float(v) for v in nums], "percent", warnings
    raise ValueError(
        f"ambiguous NAV scale: sum={total!r} outside 0.95-1.05 and 95-105; "
        "pass explicit scale='fraction' or scale='percent'"
    )
