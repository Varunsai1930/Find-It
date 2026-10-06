"""Explicit calculation versions supported by retained-release replay."""
from __future__ import annotations

RULE_VERSION = "readiness-2026-10-06"
LEGACY_RULE_VERSION = "readiness-2026-10-01"
SUPPORTED_RULE_VERSIONS = frozenset({RULE_VERSION, LEGACY_RULE_VERSION})


def require_supported_rules(version: str) -> str:
    if not isinstance(version, str) or version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError("release calculation rules are unsupported by this application version")
    return version
