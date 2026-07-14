#!/usr/bin/env python3
"""Generate the human-readable source registry from scripts/sources.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "scripts" / "sources.json"
DEFAULT_OUTPUT = ROOT / "SOURCES.md"


def escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def link(label: str, url: str | None) -> str:
    return f"[{escape(label)}]({url})" if url else escape(label)


def table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    headers = list(headers)
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(escape(value) for value in row) + " |")
    return out


def query_text(search: dict[str, Any]) -> str:
    params = search.get("params") or {}
    parts = [f"{key}={value}" for key, value in params.items()]
    if search.get("force_student"):
        parts.append("student flag")
    return ", ".join(parts) or "broad search"


def build(output: Path = DEFAULT_OUTPUT) -> Path:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    gc = config.get("government_canada", {})
    us = config.get("government_us", {})
    ats_keys = (
        "greenhouse_boards", "lever_boards", "ashby_boards",
        "smartrecruiters_boards", "workday_boards", "successfactors_boards",
    )
    ats_total = sum(len(config.get(key, [])) for key in ats_keys)
    optional_total = sum(len(config.get(key, [])) for key in (
        "careeronestop_queries", "adzuna_queries", "jooble_queries"
    ))

    lines = [
        "# Internship Board Source Registry",
        "",
        "This file is generated from `scripts/sources.json`. It describes what the daily refresh attempts, which sources are official, which require credentials, and which sites are deliberately not scraped.",
        "",
        "## Coverage at a glance",
        "",
        f"- **{len(gc.get('job_bank_searches', []))}** Government of Canada Job Bank searches",
        f"- **{len(gc.get('programs', []))}** Canadian federal, parliamentary, provincial, and public-sector program pages",
        f"- **{len(us.get('usajobs_queries', []))}** USAJOBS searches and **{len(us.get('programs', []))}** U.S. federal program pages",
        f"- **{len(config.get('monitored_opportunity_pages', []))}** Canadian law-firm and pre-law recruiting pages",
        f"- **{ats_total}** official employer ATS boards across Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and SAP SuccessFactors",
        f"- **{len(config.get('rss_feeds', []))}** public-sector RSS feed(s) and **{len(config.get('public_feeds', {}))}** public internship feeds",
        f"- **{optional_total}** optional credentialed search queries, plus The Muse when enabled",
        "",
        "Runtime results are written to `data/refresh_report.json` and `data/source_coverage.csv`. Every configured source is logged as `success`, `empty`, `skipped`, or `error`, with retrieved and accepted counts. Seed records are excluded from the live-coverage quality gate.",
        "",
        "## Government of Canada — Job Bank searches",
        "",
    ]
    lines += table(
        ("Search", "Tier", "Filters / query", "Age window", "Fallback pages"),
        ((s.get("name"), s.get("tier", "standard"), query_text(s), f"{s.get('days', 60)} days", s.get("fallback_pages", 3))
         for s in gc.get("job_bank_searches", [])),
    )

    lines += ["", "## Canadian public-sector and student programs", ""]
    lines += table(
        ("Program", "Organization", "Scope", "Tier"),
        ((link(p.get("title", "Program"), p.get("url")), p.get("company", "Government of Canada"), p.get("location", "Canada"), p.get("tier", "standard"))
         for p in gc.get("programs", [])),
    )

    lines += ["", "## Canadian law-firm and pre-law recruiting pages", ""]
    lines += table(
        ("Program page", "Organization", "Scope", "Board treatment"),
        ((link(p.get("title", "Program"), p.get("url")), p.get("company"), p.get("location"),
          "future watchlist" if p.get("eligibility_status") == "future" else "requirements check")
         for p in config.get("monitored_opportunity_pages", [])),
    )

    lines += ["", "## United States federal sources", ""]
    lines += table(
        ("Source", "Organization", "Scope"),
        ((link(p.get("title", "Program"), p.get("url")), p.get("company", "U.S. Federal Government"), p.get("location", "United States"))
         for p in us.get("programs", [])),
    )
    lines += ["", "**USAJOBS keyword searches:** " + ", ".join(f"`{escape(q.get('keyword'))}`" for q in us.get("usajobs_queries", [])) + "."]
    lines += ["", "USAJOBS is an official API source but requires `USAJOBS_API_KEY` and `USAJOBS_EMAIL`. Without those secrets, it is recorded as skipped rather than silently treated as successful.", ""]

    platform_labels = {
        "greenhouse_boards": "Greenhouse",
        "lever_boards": "Lever",
        "ashby_boards": "Ashby",
        "smartrecruiters_boards": "SmartRecruiters",
        "workday_boards": "Workday",
        "successfactors_boards": "SAP SuccessFactors",
    }
    lines += ["## Official employer applicant-tracking systems", ""]
    lines += table(
        ("Platform", "Configured boards", "Employers"),
        ((platform_labels[key], len(config.get(key, [])), ", ".join(escape(b.get("company") or b.get("token") or b.get("identifier") or b.get("tenant") or b.get("id")) for b in config.get(key, [])))
         for key in ats_keys),
    )
    lines += [
        "",
        "These are official employer career endpoints. A board can still move, rename its tenant, block traffic, or return no internships; the refresh report makes those outcomes visible.",
        "",
        "## Public feeds and discovery APIs",
        "",
    ]
    discovery_rows: list[tuple[str, str, str, str]] = []
    for name, url in (config.get("public_feeds") or {}).items():
        discovery_rows.append((name, "Public feed", url, "No"))
    for feed in config.get("rss_feeds", []):
        discovery_rows.append((feed.get("name", "RSS feed"), "RSS/Atom", feed.get("url", ""), "No"))
    discovery_rows.extend([
        ("CareerOneStop", "Authorized U.S. job API", "Business/law internship keyword searches", "CAREERONESTOP_TOKEN + CAREERONESTOP_USER_ID"),
        ("Adzuna", "Authorized search API", "Canada and U.S. keyword searches", "ADZUNA_APP_ID + ADZUNA_APP_KEY"),
        ("Jooble", "Authorized search API", "Canada and U.S. keyword searches", "JOOBLE_API_KEY"),
        ("The Muse", "Public API", "Internship-level Canada/U.S. search", "No"),
    ])
    lines += table(("Source", "Type", "Coverage", "Credentials"), discovery_rows)
    lines += [
        "",
        "Aggregator and community-feed records are labelled as **discovery leads** and should be verified on the employer’s own page before applying.",
        "",
        "## Deliberately not scraped",
        "",
    ]
    lines += table(
        ("Site / category", "Reason", "Alternative"),
        ((item.get("site") or item.get("source"), item.get("reason"),
          item.get("alternative") or "Official employer feeds, authorized APIs, or manual verification")
         for item in config.get("not_scraped", [])),
    )
    lines += [
        "",
        "## Publication safeguards",
        "",
        "The scheduled workflow requires multiple independent live source families, multiple successful core sources, at least one accepted Job Bank result, and a minimum number of unique live opportunities. When those checks fail, the workflow writes diagnostics and stops before replacing the last good board.",
        "",
        "No crawler can guarantee every job posted on the internet. This registry makes the configured scope and its limitations auditable instead of presenting a seed list as a complete market scan.",
        "",
    ]
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = build(args.output.resolve())
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
