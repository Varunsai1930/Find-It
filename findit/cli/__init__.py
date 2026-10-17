"""Every command, as ``python3 -m findit.cli.<name>``. Each parses arguments and
prints; the work lives in the layers below (see the README's Layout section).

Only ``shareholding`` and ``prices`` use the network.
"""
import sys

# Output piped to a file or another program uses the system code page on
# Windows, which has no ₹ or →; a print would then stop the command midway.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure") and (
            (_stream.encoding or "").replace("-", "").lower() != "utf8"):
        _stream.reconfigure(encoding="utf-8")
