#!/usr/bin/env python3
"""Build a privacy-safer static copy for GitHub Pages.

The working project keeps Katherine's personalized cold-email signatures. Public hosting
should not expose a personal phone number or email by default, so this build step replaces
those fields with obvious placeholders while preserving the full local version.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

try:
    from scripts.build_standalone import build as build_standalone
except ModuleNotFoundError:  # Support ``python scripts/build_public_site.py``.
    from build_standalone import build as build_standalone

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_OUTPUT = ROOT / "_site"

REPLACEMENTS = {
    "kdu039@uottawa.ca": "[your email]",
    "613-447-2562": "[your phone]",
    "https://www.linkedin.com/in/katherine-du1": "[your LinkedIn URL]",
    "linkedin.com/in/katherine-du1": "[your LinkedIn]",
}


def redact_text(value: str) -> str:
    for private, placeholder in REPLACEMENTS.items():
        value = value.replace(private, placeholder)
    return value


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def build(output: Path, *, include_contact: bool = False) -> Path:
    if output.exists():
        shutil.rmtree(output)
    (output / "data").mkdir(parents=True)
    (output / "outreach").mkdir(parents=True)

    for filename in ("index.html", "styles.css", "app.js", "README.md", "SOURCES.md"):
        shutil.copy2(ROOT / filename, output / filename)

    jobs_payload = json.loads((DATA_DIR / "jobs.json").read_text(encoding="utf-8"))
    public_payload = jobs_payload if include_contact else redact(jobs_payload)
    serialized = json.dumps(public_payload, indent=2, ensure_ascii=False) + "\n"
    (output / "data" / "jobs.json").write_text(serialized, encoding="utf-8")
    (output / "data" / "jobs.js").write_text(
        "window.JOB_BOARD_DATA = " + json.dumps(public_payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    for filename in ("jobs.csv", "refresh_report.json", "source_coverage.csv"):
        shutil.copy2(DATA_DIR / filename, output / "data" / filename)

    outreach = (ROOT / "outreach" / "cold_emails.md").read_text(encoding="utf-8")
    if not include_contact:
        outreach = redact_text(outreach)
    (output / "outreach" / "cold_emails.md").write_text(outreach, encoding="utf-8")

    marker = {
        "personal_contact_included": include_contact,
        "note": (
            "This public build includes the personalized contact signature."
            if include_contact
            else "Personal email, phone, and LinkedIn details were replaced with placeholders."
        ),
    }
    (output / "build-info.json").write_text(
        json.dumps(marker, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    build_standalone(output, output / "standalone.html")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--include-contact",
        action="store_true",
        help="Keep personal contact details in the public output. Not recommended for a public repository.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = build(args.output.resolve(), include_contact=args.include_contact)
    print(f"Built public site at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
