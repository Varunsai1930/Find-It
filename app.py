"""Protected Vercel entrypoint; no artifact acquisition occurs at runtime."""
from pathlib import Path
import os
import re

from tools.prepare_vercel_release import load_descriptor, validate_artifacts
from findit.web.app import create_app


def hosted_app(root: Path | None = None):
    root = root or Path(__file__).resolve().parent
    if os.environ.get("VERCEL_ENV") == "production":
        raise ValueError("This deployment adapter is restricted to protected previews")
    hosts = []
    for key in ("VERCEL_URL", "VERCEL_BRANCH_URL"):
        value = os.environ.get(key, "")
        if value:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*\.vercel\.app", value):
                raise ValueError("Vercel staging requires exact deployment hostnames")
            hosts.append(value.lower())
    if not hosts:
        raise ValueError("Vercel deployment hostnames are required")
    descriptor = load_descriptor(root / "deployment" / "releases.json")
    directory = root / "deploy-data"
    validate_artifacts(descriptor, directory)
    return create_app(directory / (descriptor["current_release"] + ".db"), directory,
                      allowed_hosts=tuple(dict.fromkeys(hosts)), hosted=True,
                      expected_month=descriptor["month"])


app = hosted_app()
