#!/usr/bin/env python3
"""Refresh Katherine Du's Canada + U.S. business/law internship board.

The refresh pipeline is deliberately source-transparent and fail-visible:
- Government of Canada student programs and Job Bank searches are first-class sources.
- U.S. federal jobs can be pulled from USAJOBS when official API credentials exist.
- Official Greenhouse, Lever, Ashby, SmartRecruiters, and Workday endpoints are queried.
- Optional CareerOneStop, Adzuna, Jooble, and The Muse discovery APIs broaden recall.
- Every source produces a health record; missing credentials are "skipped", not "success".
- Strict mode blocks publication when core sources fail or live discovery is implausibly empty.
- Curated records remain a reviewed fallback, but are never counted as a successful scrape.

Typical commands:
    python scripts/refresh_jobs.py
    python scripts/refresh_jobs.py --strict --minimum-successful-core-sources 3 --minimum-successful-source-families 3 --minimum-dynamic-jobs 10 --require-job-bank-success
    python scripts/refresh_jobs.py --offline --as-of 2026-07-13T16:00:00Z
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "scripts" / "sources.json"
SEED_PATH = DATA_DIR / "seed_jobs.json"
OUTPUT_JSON = DATA_DIR / "jobs.json"
OUTPUT_JS = DATA_DIR / "jobs.js"
OUTPUT_CSV = DATA_DIR / "jobs.csv"
REPORT_PATH = DATA_DIR / "refresh_report.json"
COVERAGE_CSV = DATA_DIR / "source_coverage.csv"
OUTREACH_PATH = ROOT / "outreach" / "cold_emails.md"

USER_AGENT = "KatherineDu-InternshipBoard/2.0 (+personal educational job search; respectful daily refresh)"

PROFILE = {
    "name": "Katherine Du",
    "email": "kdu039@uottawa.ca",
    "phone": "613-447-2562",
    "linkedin": "https://www.linkedin.com/in/katherine-du1",
    "school": "University of Ottawa",
    "program": "BCom/JD candidate",
    "graduation": "April 2031",
    "cgpa": "9.5/10.0",
    "current_role": "Client Advisor at RBC",
    "languages": ["English", "French", "Mandarin"],
    "technical": ["Excel", "Power BI", "Python", "SQL", "Pandas"],
}

INTERNSHIP_TERMS = (
    "intern", "internship", "co-op", "coop", "student", "law clerk",
    "summer associate", "summer law", "work term", "placement", "pathways",
    "fellow", "trainee", "articling", "campus", "new grad", "graduate program",
)
RELEVANCE_TERMS = (
    "legal", "law", "contract", "compliance", "governance", "privacy",
    "policy", "government relations", "government affairs", "regulatory",
    "risk", "audit", "tax", "finance", "financial", "accounting", "credit",
    "ratings", "commercial", "business", "strategy", "operations", "product",
    "analytics", "data analyst", "business intelligence", "trade", "customs",
    "esg", "procurement", "supply chain", "marketing", "sales", "project management",
    "program management", "human resources", "people operations", "communications",
    "public affairs", "administration", "administrative", "research", "economics",
    "economic", "banking", "investment", "insurance", "claims", "underwriting",
    "customer success", "client service", "partnerships", "business development",
    "fundraising", "public service", "information management", "evaluation",
)
TECHNICAL_EXCLUSIONS = (
    "software engineer", "software developer", "mechanical engineer", "electrical engineer",
    "hardware engineer", "firmware", "machine learning engineer", "data scientist",
    "research engineer", "civil engineer", "frontend", "front-end", "backend", "back-end",
    "devops", "site reliability", "embedded systems", "robotics engineer",
)
CANADA_HINTS = (
    "canada", "ontario", "quebec", "québec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland", "prince edward island",
    "northwest territories", "nunavut", "yukon", "ottawa", "toronto", "mississauga",
    "montreal", "montréal", "dorval", "laval", "vancouver", "richmond, bc", "richmond hill",
    "calgary", "edmonton", "waterloo", "markham", "north york", "longueuil", "bolton",
    "saint-laurent", "kanata", "gatineau", "winnipeg", "halifax", "regina", "saskatoon",
    ", on", ", qc", ", bc", ", ab", ", mb", ", sk", ", ns", ", nb", ", nl", ", pe",
)
US_HINTS = (
    "united states", "usa", "u.s.", "new york", "washington, dc", "district of columbia",
    "san jose", "austin, tx", "seattle", "philadelphia", "pittsburgh", "mclean", "alpharetta",
    "oak brook", "northridge", "california", "texas", "virginia", "illinois", "pennsylvania",
    "georgia", "massachusetts", "florida", "maryland", "colorado", "arizona", "ohio", "michigan",
    "north carolina", "south carolina", "tennessee", "new jersey", "connecticut", "minnesota",
    "wisconsin", "missouri", "oregon", "utah", "nevada", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "new hampshire", "new mexico", "oklahoma", "rhode island",
    "vermont", "west virginia", "alaska", "hawaii", "delaware", "idaho", "montana", "nebraska",
    "north dakota", "south dakota", "wyoming", "arkansas", "mississippi", "alabama",
    ", ny", ", ca", ", tx", ", wa", ", pa", ", va", ", il", ", ga", ", ma", ", fl",
    ", md", ", co", ", az", ", oh", ", mi", ", nc", ", sc", ", tn", ", nj", ", ct",
)


@dataclass
class SourceError:
    source: str
    error: str


@dataclass
class SourceResult:
    jobs: list[dict[str, Any]]
    retrieved: int | None = None
    pages: int = 1
    note: str | None = None


@dataclass
class SourceRun:
    name: str
    family: str
    tier: str
    status: str
    retrieved: int
    accepted: int
    pages: int
    duration_ms: int
    error: str | None = None
    note: str | None = None


@dataclass
class SourceSpec:
    name: str
    family: str
    tier: str
    call: Callable[[], SourceResult | list[dict[str, Any]]]


class RefreshError(RuntimeError):
    pass


class SourceSkipped(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Skip network sources and rebuild from reviewed seed data.")
    parser.add_argument("--max-age-days", type=int, default=60, help="Maximum age for dynamically discovered roles.")
    parser.add_argument("--timeout", type=int, default=16, help="HTTP timeout per request in seconds.")
    parser.add_argument("--as-of", help="Override current UTC time (ISO 8601) for deterministic testing.")
    parser.add_argument("--strict", action="store_true", help="Block publishing when live-source quality checks fail.")
    parser.add_argument("--minimum-successful-core-sources", type=int, default=1)
    parser.add_argument("--minimum-successful-source-families", type=int, default=0, help="Require multiple independent source families in strict mode.")
    parser.add_argument("--minimum-dynamic-jobs", type=int, default=0)
    parser.add_argument("--require-job-bank-success", action="store_true", help="In strict mode, require at least one Job Bank search to produce an accepted relevant opportunity.")
    parser.add_argument("--max-workers", type=int, default=8, help="Parallel workers for independent ATS/API sources.")
    parser.add_argument("--source-delay", type=float, default=0.25, help="Delay between Government of Canada requests.")
    return parser.parse_args()


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RefreshError(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RefreshError(f"Invalid JSON in {path}: {exc}") from exc


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize(value: Any) -> str:
    value = clean_text(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(value)).strip("-")[:180]


def safe_http_url(value: Any) -> str | None:
    url = clean_text(value)
    if url.startswith("https://") or url.startswith("http://"):
        return url
    return None


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,application/xml,text/xml,text/html,text/plain,*/*",
        "Accept-Language": "en-CA,en;q=0.9",
    })
    return session


def request_json(
    session: requests.Session,
    url: str,
    timeout: int,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> Any:
    if method.upper() == "POST":
        response = session.post(url, params=params, headers=headers, json=json_body, timeout=timeout)
    else:
        response = session.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def request_text(
    session: requests.Session,
    url: str,
    timeout: int,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    response = session.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_date_value(value: Any, as_of: datetime) -> tuple[date, str, str]:
    """Return (date, human label, precision)."""
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            return parsed.date(), "Posted " + parsed.strftime("%b %-d"), "exact"
        except (OverflowError, OSError, ValueError):
            pass

    text = clean_text(value)
    lower = text.lower()
    if not text or lower in {"recently", "new", "today", "posted today"}:
        return as_of.date(), "Live feed · verified " + as_of.strftime("%b %-d"), "verified"
    if lower == "yesterday" or "posted yesterday" in lower:
        d = as_of.date() - timedelta(days=1)
        return d, "Posted " + d.strftime("%b %-d"), "relative"
    match = re.search(r"(\d+)\s*(?:day|days|d)\s*ago", lower)
    if match:
        d = as_of.date() - timedelta(days=int(match.group(1)))
        return d, "Posted " + d.strftime("%b %-d"), "relative"
    match = re.search(r"(\d+)\s*(?:week|weeks|w)\s*ago", lower)
    if match:
        d = as_of.date() - timedelta(days=7 * int(match.group(1)))
        return d, "Posted about " + clean_text(value), "relative"
    match = re.search(r"posted\s+(\d{1,2})\s+days?\s+ago", lower)
    if match:
        d = as_of.date() - timedelta(days=int(match.group(1)))
        return d, "Posted " + d.strftime("%b %-d"), "relative"

    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.date(), "Posted " + parsed.strftime("%b %-d"), "exact"
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.date(), "Posted " + parsed.strftime("%b %-d"), "exact"
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in (
        "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d/%m/%Y",
        "%B %d %Y", "%b %d %Y", "%Y/%m/%d",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date(), "Posted " + parsed.strftime("%b %-d"), "exact"
        except ValueError:
            continue
    return as_of.date(), "Live feed · verified " + as_of.strftime("%b %-d"), "verified"


def parse_deadline_value(value: Any) -> tuple[str | None, str]:
    text = clean_text(value)
    if not text:
        return None, "See posting"
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.date().isoformat(), "Apply by " + parsed.strftime("%b %-d")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date().isoformat(), "Apply by " + parsed.strftime("%b %-d")
        except ValueError:
            continue
    match = re.search(
        r"(?:application deadline|deadline to apply|deadline|apply by|closing date)\s*[:\-]?\s*([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return parse_deadline_value(match.group(1))
    return None, text[:90]


def infer_country(location: str, default: str | None = None) -> str | None:
    lower = clean_text(location).lower()
    if any(hint in lower for hint in CANADA_HINTS):
        return "Canada"
    if any(hint in lower for hint in US_HINTS):
        return "United States"
    if lower in {"ca", "can", "canada remote", "remote - canada"}:
        return "Canada"
    if lower in {"us", "usa", "united states remote", "remote - united states"}:
        return "United States"
    return default


def infer_mode(text: str) -> str:
    lower = clean_text(text).lower()
    if "hybrid" in lower:
        return "Hybrid"
    if "remote" in lower or "telework" in lower:
        return "Remote"
    if "onsite" in lower or "on-site" in lower or "in person" in lower or "on site" in lower:
        return "Onsite"
    return "Not stated"


def infer_term(title: str, season: str | None = None) -> str:
    text = f"{title} {season or ''}".lower()
    year_match = re.search(r"20(?:26|27|28|29|30|31)", text)
    year = year_match.group(0) if year_match else ""
    for name in ("fall", "autumn", "summer", "spring", "winter"):
        if name in text:
            normalized = "Fall" if name == "autumn" else name.title()
            return f"{normalized} {year}".strip()
    if "year-round" in text or "ongoing" in text or "continuous" in text:
        return "Year-round / ongoing"
    return season or "Current / flexible"


def infer_lane(title: str, description: str = "") -> str:
    text = f" {normalize(f'{title} {description}')} "
    if any(term in text for term in (" legal", " law ", "law student", "law clerk", "contract administration", "contracts")):
        return "Legal & Contracts"
    if any(term in text for term in ("compliance", "governance", "privacy", "regulatory", "trade", "customs", "esg", "internal audit", "enterprise risk")):
        return "Compliance & Governance"
    if any(term in text for term in ("government relations", "government affairs", "public policy", " policy ", "public service", "legislative")):
        return "Policy & Government"
    if any(term in text for term in ("tax", "credit risk", "ratings", "accounting", "risk analyst", "finance", "financial", "banking", "investment", "insurance", "underwriting")):
        return "Finance, Tax & Risk"
    if any(term in text for term in ("analytics", "data analyst", "business intelligence", "strategy", "product analysis", "project management", "evaluation")):
        return "Analytics & Strategy"
    if any(term in text for term in ("human resources", "people operations", "communications", "marketing", "public affairs", "customer success", "sales")):
        return "People, Marketing & Communications"
    return "Business & Operations"


def relevant_role(title: str, description: str = "", category: str = "", *, force_student: bool = False) -> bool:
    title_norm = normalize(title)
    text = normalize(f"{title} {description} {category} {'student internship' if force_student else ''}")
    if not force_student and not any(term in text for term in INTERNSHIP_TERMS):
        return False
    if not any(term in text for term in RELEVANCE_TERMS):
        return False
    if any(term in title_norm for term in TECHNICAL_EXCLUSIONS):
        business_override = any(term in title_norm for term in (
            "business", "legal", "law", "compliance", "governance", "finance", "financial",
            "trade", "policy", "strategy", "operations", "product", "analytics", "audit",
            "risk", "tax", "marketing", "human resources", "communications", "procurement",
        ))
        if not business_override:
            return False
    return True


def lane_content(lane: str) -> tuple[str, str, list[str], list[str], int]:
    mapping = {
        "Legal & Contracts": (
            "The role connects legal research, contract work, documentation, and practical business operations.",
            "Support legal or contract matters, research, documentation, and business-facing legal operations.",
            ["Combined BCom/JD profile", "RBC compliance and confidential-data handling", "Client communication and organization"],
            ["Legal research", "Contracts", "Documentation", "Stakeholder communication"],
            88,
        ),
        "Compliance & Governance": (
            "The work sits at the intersection of regulation, risk, governance, data, and commercial execution.",
            "Support compliance, governance, controls, risk analysis, and cross-functional operational processes.",
            ["RBC regulated-environment experience", "BCom/JD perspective on risk and obligations", "Excel, Power BI, Python and SQL"],
            ["Compliance", "Governance", "Risk", "Controls"],
            87,
        ),
        "Policy & Government": (
            "The role translates public policy and regulatory developments into practical business and stakeholder decisions.",
            "Research policy and regulatory developments and help turn them into clear business or public-sector recommendations.",
            ["Business-law education", "English, French and Mandarin communication", "Research and client-facing judgment"],
            ["Policy research", "Regulation", "Writing", "Stakeholder relations"],
            86,
        ),
        "Finance, Tax & Risk": (
            "The opportunity combines finance, regulatory discipline, analysis, and risk-conscious business judgment.",
            "Support finance, tax, credit, ratings, or risk work through analysis, reporting, and controls.",
            ["RBC financial-services experience", "Commerce education", "Excel, Power BI, Python and SQL"],
            ["Financial analysis", "Risk", "Reporting", "Controls"],
            85,
        ),
        "Analytics & Strategy": (
            "The role uses analytics, reporting, and business judgment to support strategy and operating decisions.",
            "Analyze business data, build reporting, and translate findings into practical recommendations.",
            ["Power BI, Python, SQL, Pandas and Excel", "Commerce education", "Client service and process-improvement experience"],
            ["Business analytics", "Strategy", "Dashboards", "Presentations"],
            84,
        ),
        "People, Marketing & Communications": (
            "The opportunity combines stakeholder communication, commercial judgment, and organized program execution.",
            "Support people, communications, marketing, client, or program initiatives with research, coordination, and analysis.",
            ["Trilingual communication", "RBC client-service experience", "Team leadership and high-volume execution"],
            ["Communications", "Stakeholder service", "Program coordination", "Business writing"],
            82,
        ),
        "Business & Operations": (
            "The role offers hands-on exposure to commercial analysis, operations, and cross-functional decision-making.",
            "Support business analysis, reporting, operations, and stakeholder coordination.",
            ["RBC client and financial-services experience", "Commerce education", "High-volume execution and communication"],
            ["Business analysis", "Operations", "Excel", "Communication"],
            83,
        ),
        "Business & Finance": (
            "The role offers hands-on exposure to commercial analysis, operations, finance, and cross-functional decision-making.",
            "Support business or finance analysis, reporting, operations, and stakeholder coordination.",
            ["RBC client and financial-services experience", "Commerce education", "High-volume execution and communication"],
            ["Business analysis", "Finance", "Operations", "Communication"],
            83,
        ),
    }
    return mapping.get(lane, mapping["Business & Operations"])


def infer_eligibility(
    country: str,
    title: str,
    description: str,
    sponsorship: str | None = None,
) -> tuple[str, str, str, str, str, str | None]:
    text = normalize(f"{title} {description} {sponsorship or ''}")
    class_year = "Current student"
    warning = None

    if country == "United States":
        work_auth = "Check U.S. authorization and sponsorship"
        status = "check"
        label = "Check U.S. requirements"
        notes = "Confirm class year, degree timing, citizenship, and whether independent U.S. work authorization is required."
        if any(phrase in text for phrase in ("us citizenship required", "must be a us citizen", "u s citizen required", "citizenship is required")) or sponsorship == "citizens-only":
            status, label = "conflict", "U.S. citizenship requirement"
            notes = "The source indicates that U.S. citizenship is required."
            work_auth = "U.S. citizenship required"
            warning = notes
        elif sponsorship == "no-sponsorship" or "no sponsorship" in text or "will not sponsor" in text:
            status, label = "check", "Independent U.S. authorization likely required"
            notes = "The source indicates no employment-immigration sponsorship; verify whether you already have qualifying U.S. authorization."
            work_auth = "No sponsorship indicated"
        if re.search(r"\b(?:1l|2l|3l)\b", text):
            class_match = re.search(r"\b(1l|2l|3l)\b", text)
            class_year = f"{class_match.group(1).upper()} law student" if class_match else class_year
            status = "conflict"
            label = f"{class_year} requirement"
            notes = "The role targets a specific U.S. law-school year; confirm whether your Canadian combined-program standing is accepted."
            warning = notes
        if "aba accredited" in text:
            status, label = "conflict", "ABA-accredited law school required"
            notes = "The posting requires enrollment at an ABA-accredited law school."
            warning = notes
        return status, label, notes, work_auth, class_year, warning

    work_auth = "Must be eligible to work in Canada"
    status, label = "likely", "Likely match"
    notes = "Confirm term dates, academic registration, and Canadian work authorization on the official posting."
    if "co op" in text or "coop" in text:
        status, label = "check", "Check co-op enrollment"
        notes = "Confirm that your University of Ottawa program can register or approve this co-op work term."
        class_year = "Co-op student"
    if "return to full time studies" in text or "returning to full time studies" in text:
        status, label = "likely", "Likely student-program match"
        notes = "Confirm full-time status, return-to-studies eligibility, and any citizenship or residency preference stated by the program."
    if re.search(r"\b(?:1l|2l|3l)\b", text):
        match = re.search(r"\b(1l|2l|3l)\b", text)
        class_year = f"{match.group(1).upper()} law student" if match else class_year
        status, label = "conflict", f"{class_year} requirement"
        notes = "The posting targets a specific law-school year; verify whether your combined-program standing qualifies."
        warning = notes
    if "three years" in text or "3 years of studies" in text:
        status, label = "check", "Check completed-study requirement"
        notes = "The source appears to require roughly three completed years of study."
        warning = notes
    return status, label, notes, work_auth, class_year, warning


def profile_paragraph(lane: str) -> str:
    base = "I’m Katherine Du, a University of Ottawa BCom/JD candidate with a 9.5/10 CGPA and a Client Advisor at RBC. "
    if lane in {"Analytics & Strategy", "Business & Operations", "Business & Finance", "Finance, Tax & Risk"}:
        return base + (
            "In my current role, I support more than 50 clients per shift, identify needs-based financial solutions, "
            "and handle confidential information under banking privacy and compliance procedures. I also work with "
            "Excel, Power BI, Python, SQL, and Pandas."
        )
    if lane == "Compliance & Governance":
        return base + (
            "At RBC, I work in a regulated environment, support more than 50 clients per shift, and handle confidential "
            "information under privacy and compliance procedures. My technical toolkit includes Excel, Power BI, Python, SQL, and Pandas."
        )
    if lane == "Policy & Government":
        return base + (
            "My experience at RBC has strengthened my ability to translate rules and client needs into clear, practical action, "
            "while my English, French, and Mandarin skills help me communicate across audiences."
        )
    if lane == "People, Marketing & Communications":
        return base + (
            "At RBC, I communicate with a high volume of clients, identify needs, and explain financial services clearly in a regulated setting. "
            "I also speak English, French, and Mandarin."
        )
    return base + (
        "At RBC, I support more than 50 clients per shift, identify needs-based financial solutions, and handle confidential "
        "information in accordance with privacy and compliance procedures."
    )


def bridge_paragraph(lane: str, skills: Iterable[str]) -> str:
    top_skills = [clean_text(skill) for skill in skills if clean_text(skill)][:3]
    skill_text = ", ".join(top_skills[:-1])
    if len(top_skills) > 1:
        skill_text += f", and {top_skills[-1]}"
    elif top_skills:
        skill_text = top_skills[0]

    lane_copy = {
        "Legal & Contracts": "The combination of legal analysis, process discipline, and business-facing communication is exactly the kind of work I hope to develop.",
        "Compliance & Governance": "I would bring a compliance-minded client-service perspective, careful handling of sensitive information, and an interest in how controls support sound business decisions.",
        "Finance, Tax & Risk": "I would bring experience serving clients in a regulated financial environment, strong analytical skills, and an interest in how commercial decisions interact with legal and regulatory requirements.",
        "Policy & Government": "My combined business-law studies, multilingual communication skills, and experience translating rules into practical client guidance would provide a useful foundation for this work.",
        "Analytics & Strategy": "I would bring a customer-focused business perspective together with hands-on analytical tools, including Excel, Power BI, Python, SQL, and Pandas.",
        "Business & Operations": "I would bring hands-on financial-services experience, a strong commerce foundation, and a disciplined approach to analysis, communication, and follow-through.",
        "Business & Finance": "I would bring hands-on financial-services experience, a strong commerce foundation, and a disciplined approach to analysis, communication, and follow-through.",
        "People, Marketing & Communications": "I would bring trilingual client communication, team leadership, and experience maintaining service quality in fast-moving environments.",
        "Law Student Recruiting": "My long-term goal is to build a practice-ready understanding of how legal advice supports clients, transactions, and organizational decision-making.",
    }
    paragraph = lane_copy.get(lane, "I would bring a combined business-law perspective, regulated client-service experience, and strong analytical and communication skills.")
    if skill_text and lane not in {"Analytics & Strategy"}:
        paragraph += f" I am particularly interested in strengthening my capabilities in {skill_text}."
    return paragraph


def make_email(job: dict[str, Any]) -> dict[str, str]:
    salutation = f"Dear {job.get('contact') or '[Name]'},"
    interest = clean_text(job.get("interest")) or "the work combines business judgment with legal, regulatory, or analytical responsibilities."
    if interest and interest[-1] not in ".!?":
        interest += "."
    status = job.get("eligibility_status")
    if status == "conflict":
        label = normalize(job.get("eligibility_label"))
        concerns: list[str] = []
        if any(token in label for token in ("degree", "education", "field")):
            concerns.append("academic-field requirement")
        elif any(token in label for token in ("class", "year", "second-year", "2l", "1l")):
            concerns.append("academic-timing requirement")
        if any(token in label for token in ("aba", "law-school", "law school", "accredit")):
            concerns.append("law-school accreditation requirement")
        if "citizenship" in label:
            concerns.append("citizenship requirement")
        elif any(token in label for token in ("authorization", "sponsorship", "visa", "work permit")) or job.get("country") == "United States":
            concerns.append("work-authorization requirement")
        if not concerns:
            concerns.append("formal eligibility requirements")
        concern_text = concerns[0] if len(concerns) == 1 else " and ".join(concerns[:2])
        requirements = (
            f"I recognize that the posting’s {concern_text} may make this specific cycle unavailable to me, "
            "so I would not want to presume eligibility. Even so, I would value learning how someone with a Canadian "
            "combined business-law background can prepare for a future recruiting cycle."
        )
    elif status == "future":
        requirements = (
            "I understand that this opportunity is best treated as advance planning for a later recruiting cycle. "
            "I would value learning what experiences the team prioritizes so I can prepare thoughtfully before the relevant application window opens."
        )
    elif status == "check":
        if job.get("country") == "United States":
            requirements = (
                "I am reviewing the formal requirements carefully before applying, including the U.S. work-authorization "
                "and academic-timing requirements. I would appreciate a clearer view of how the team evaluates candidates."
            )
        else:
            requirements = (
                "I am reviewing the formal requirements carefully before applying, including how the work term and any "
                "co-op-registration requirements align with my combined program at the University of Ottawa. I would "
                "appreciate a clearer view of how the team evaluates candidates."
            )
    else:
        requirements = (
            "I plan to apply through the formal process and would appreciate your perspective on the team’s current priorities "
            "and what distinguishes interns who contribute quickly."
        )
    body = "\n\n".join([
        salutation,
        profile_paragraph(job.get("lane", "Business & Operations")),
        f"I’m reaching out about the {job.get('title', 'internship')} opportunity. I was especially drawn to it because {interest[0].lower() + interest[1:] if interest else interest}",
        bridge_paragraph(job.get("lane", "Business & Operations"), job.get("skills") or []),
        requirements,
        "Would you be open to a 15-minute conversation in the next two weeks? I would be grateful for the chance to hear about your experience and ask a few focused questions. I am happy to work around your schedule.",
        "Best regards,\nKatherine Du\nBCom/JD Candidate, University of Ottawa\nkdu039@uottawa.ca | 613-447-2562\nlinkedin.com/in/katherine-du1",
    ])
    return {
        "subject": f"BCom/JD candidate interested in {job.get('company')}’s {job.get('title')} opportunity",
        "body": body,
    }


def build_dynamic_job(
    *,
    company: str,
    title: str,
    location: str,
    url: str,
    source_name: str,
    source_type: str,
    as_of: datetime,
    posted_value: Any = None,
    deadline_value: Any = None,
    description: str = "",
    category: str = "",
    season: str | None = None,
    salary: str | None = None,
    sponsorship: str | None = None,
    default_country: str | None = None,
    official: bool = True,
    source_id: str | None = None,
    source_family: str | None = None,
    provenance: str | None = None,
    discovery_query: str | None = None,
    force_student: bool = False,
    force_include: bool = False,
    status: str = "open_when_verified",
) -> dict[str, Any] | None:
    company = clean_text(company)
    title = clean_text(title)
    location = clean_text(location) or ("Canada (multiple locations)" if default_country == "Canada" else "United States (multiple locations)")
    description = clean_text(description)
    url = safe_http_url(url) or ""
    if not company or not title or not location or not url:
        return None
    if not force_include and not relevant_role(title, description, category, force_student=force_student):
        return None
    country = infer_country(location, default_country)
    if country not in {"Canada", "United States"}:
        return None

    posted_date, posted_label, date_precision = parse_date_value(posted_value, as_of)
    deadline, deadline_label = parse_deadline_value(deadline_value)
    lane = infer_lane(title, f"{description} {category}")
    interest, generic_description, fit_reasons, skills, base_score = lane_content(lane)
    title_norm = normalize(title)
    if "legal" in title_norm or "contract" in title_norm:
        base_score += 2
    if "compliance" in title_norm or "governance" in title_norm:
        base_score += 2
    if country == "Canada" and any(city in normalize(location) for city in ("ottawa", "montreal", "toronto", "gatineau")):
        base_score += 1
    if source_type == "official_program":
        base_score += 1
    fit_score = min(base_score, 95)
    eligibility = infer_eligibility(country, title, description, sponsorship)
    eligibility_status, label, notes, work_auth, class_year, warning = eligibility

    job = {
        "id": slug(f"{source_id or source_name}-{company}-{title}-{location}"),
        "company": company,
        "title": title,
        "location": location,
        "country": country,
        "lane": lane,
        "term": infer_term(title, season),
        "mode": infer_mode(f"{title} {location} {description}"),
        "posted_date": posted_date.isoformat(),
        "posted_label": posted_label,
        "posted_precision": date_precision,
        "verified_at": as_of.isoformat().replace("+00:00", "Z"),
        "deadline": deadline,
        "deadline_label": deadline_label,
        "compensation": clean_text(salary) or None,
        "url": url,
        "source_name": source_name,
        "source_type": source_type,
        "source_family": source_family or source_type,
        "provenance": provenance or ("official employer or government source" if official else "discovery source; verify on employer site"),
        "discovery_query": clean_text(discovery_query) or None,
        "official": bool(official),
        "status": status,
        "fit_score": fit_score,
        "fit_label": "Strong match" if fit_score >= 90 else "Good match" if fit_score >= 82 else "Exploratory match",
        "fit_reasons": fit_reasons,
        "eligibility_status": eligibility_status,
        "eligibility_label": label,
        "eligibility_notes": notes,
        "work_auth": work_auth,
        "class_year": class_year,
        "warning": warning,
        "skills": skills,
        "description": generic_description,
        "interest": interest,
        "contact": "[Name]",
        "contact_email": None,
        "_curated": False,
    }
    job["email"] = make_email(job)
    return job


# ----------------------------- Public feed parsers -----------------------------

def parse_zapply_markdown(text: str, as_of: datetime) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    company_matches = list(re.finditer(r"\|\s*\*\*([^*|]+)\*\*\s*\|", text))
    for index, match in enumerate(company_matches):
        segment_end = company_matches[index + 1].start() if index + 1 < len(company_matches) else len(text)
        segment = text[match.end():segment_end]
        fields = [clean_text(part) for part in segment.split("|")]
        if len(fields) < 3:
            continue
        title, location, posted = fields[:3]
        urls = re.findall(r"\]\((https?://[^)\s]+)\)", segment)
        if not urls:
            continue
        job = build_dynamic_job(
            company=match.group(1), title=title, location=location, url=urls[-1],
            source_name="Zapply Internships 2027", source_type="public_feed", source_family="public_feeds",
            as_of=as_of, posted_value=posted, default_country=None, official=False,
            provenance="community-maintained discovery feed; verify on employer site", source_id=f"zapply-{index}",
        )
        if job:
            jobs.append(job)
    return jobs


def parse_canada_tracker_markdown(text: str, as_of: datetime) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        if "[Apply]" not in line or not line.lstrip().startswith("|"):
            continue
        fields = [clean_text(part) for part in line.strip().strip("|").split("|")]
        if len(fields) < 6 or normalize(fields[0]) in {"title", "---"}:
            continue
        title, company = fields[0], fields[1]
        details = " ".join(fields[2:-2])
        location = fields[-2]
        urls = re.findall(r"\]\((https?://[^)\s]+)\)", line)
        if not urls:
            continue
        job = build_dynamic_job(
            company=company, title=title, location=location, url=urls[-1],
            source_name="Daily Canadian Internship Tracker", source_type="public_feed", source_family="public_feeds",
            as_of=as_of, posted_value="Recently", description=details, default_country="Canada", official=False,
            provenance="community-maintained discovery feed; verify on employer site", source_id=f"canada-tracker-{index}",
        )
        if job:
            jobs.append(job)
    return jobs


def parse_internship_engine(payload: Any, as_of: datetime) -> list[dict[str, Any]]:
    rows = payload.get("jobs", []) if isinstance(payload, dict) else []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        job = build_dynamic_job(
            company=row.get("company", ""), title=row.get("title", ""), location=row.get("location", ""),
            url=row.get("url", ""), source_name="Automated Internship Engine", source_type="public_feed",
            source_family="public_feeds", as_of=as_of, posted_value=row.get("posted_at") or row.get("first_seen_at"),
            description=" ".join(row.get("skills") or []), category=row.get("category", ""),
            season=row.get("season"), salary=row.get("salary"), sponsorship=row.get("sponsorship"),
            default_country="United States", official=False,
            provenance="community-maintained discovery feed; verify on employer site", source_id=row.get("id"),
        )
        if job:
            jobs.append(job)
    return jobs


# --------------------------- Government of Canada -----------------------------

def _text_after_label(text: str, labels: Sequence[str]) -> str:
    escaped = "|".join(re.escape(label) for label in labels)
    stop = r"(?=(?:Employer|Company|Location|Salary|Date posted|Posted on|Job source|Source|Job number|$)\s*[:\-]?)"
    match = re.search(rf"(?:{escaped})\s*[:\-]?\s*(.+?){stop}", text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def parse_job_bank_html(
    text: str,
    as_of: datetime,
    *,
    search_name: str = "Job Bank",
    force_student: bool = False,
    default_status: str = "open_when_verified",
) -> SourceResult:
    soup = BeautifulSoup(text, "html.parser")
    cards = soup.select("article[id^='article-'], article.resultJobItem, li.resultJobItem")
    if not cards:
        cards = [link.parent for link in soup.select("a.resultJobItem") if link.parent]
    jobs: list[dict[str, Any]] = []
    seen_nodes: set[int] = set()
    for index, card in enumerate(cards):
        if id(card) in seen_nodes:
            continue
        seen_nodes.add(id(card))
        link = card.select_one("a.resultJobItem[href]") or card.select_one("a[href*='/jobsearch/jobposting/']")
        if not link:
            continue
        title_node = card.select_one(".noctitle, .job-title, h3, h2")
        business_node = card.select_one(".business, .company, [itemprop='hiringOrganization']")
        location_node = card.select_one(".location, [itemprop='jobLocation']")
        date_node = card.select_one(".date, time, [itemprop='datePosted']")
        salary_node = card.select_one(".salary, [itemprop='baseSalary']")
        source_node = card.select_one(".source, .job-source")
        card_text = clean_text(card.get_text(" ", strip=True))
        title = clean_text(title_node.get_text(" ", strip=True) if title_node else link.get("title") or "")
        company = clean_text(business_node.get_text(" ", strip=True) if business_node else "")
        location = clean_text(location_node.get_text(" ", strip=True) if location_node else "Canada")
        posted = clean_text(date_node.get("datetime") if date_node and date_node.get("datetime") else date_node.get_text(" ", strip=True) if date_node else "")
        salary = clean_text(salary_node.get_text(" ", strip=True) if salary_node else "")
        external_source = clean_text(source_node.get_text(" ", strip=True) if source_node else "")
        if not company:
            company = _text_after_label(card_text, ("Employer", "Company"))
        if not location or location.lower() == "location":
            location = _text_after_label(card_text, ("Location",)) or "Canada"
        if not posted:
            posted = _text_after_label(card_text, ("Date posted", "Posted on"))
        if not salary:
            salary = _text_after_label(card_text, ("Salary",))
        href = urljoin("https://www.jobbank.gc.ca", link.get("href", ""))
        official_gc = "jobs.gc.ca" in external_source.lower() or "government of canada" in company.lower()
        source_name = "Government of Canada Jobs" if official_gc else "Government of Canada Job Bank"
        job = build_dynamic_job(
            company=company or "Employer listed on Job Bank", title=title, location=location,
            url=href, source_name=source_name,
            source_type="official_government" if official_gc else "government_aggregator",
            source_family="government_canada", as_of=as_of, posted_value=posted,
            description=card_text, category=f"{search_name} {'student internship co-op' if force_student else ''}",
            salary=salary, default_country="Canada", official=official_gc,
            provenance="federal government posting indexed by Job Bank" if official_gc else "Job Bank discovery record; verify employer posting",
            discovery_query=search_name, force_student=force_student,
            source_id=f"jobbank-html-{search_name}-{index}-{href}", status=default_status,
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(cards), pages=1)


def _rss_child_text(item: ET.Element, name: str) -> str:
    for child in item:
        tag = child.tag.split("}")[-1].lower()
        if tag == name.lower():
            return clean_text(child.text)
    return ""


def parse_job_bank_rss(
    text: str,
    as_of: datetime,
    *,
    search_name: str = "Job Bank",
    force_student: bool = False,
    default_status: str = "open_when_verified",
) -> SourceResult:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RefreshError(f"Job Bank RSS XML could not be parsed: {exc}") from exc
    items = [node for node in root.iter() if node.tag.split("}")[-1].lower() == "item"]
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        raw_title = _rss_child_text(item, "title")
        link = _rss_child_text(item, "link") or _rss_child_text(item, "guid")
        description_html = ""
        for child in item:
            tag = child.tag.split("}")[-1].lower()
            if tag in {"description", "encoded", "summary"}:
                description_html = child.text or ""
                if description_html:
                    break
        description = clean_text(description_html)
        posted = _rss_child_text(item, "pubDate") or _rss_child_text(item, "date")
        author = _rss_child_text(item, "author") or _rss_child_text(item, "creator")
        source_text = _rss_child_text(item, "source")

        title = raw_title
        title = re.sub(r"\s*[|–—-]\s*Job Bank.*$", "", title, flags=re.IGNORECASE)
        company = _text_after_label(description, ("Employer", "Company")) or author
        location = _text_after_label(description, ("Location",))
        salary = _text_after_label(description, ("Salary",))
        if not company:
            parts = [clean_text(part) for part in re.split(r"\s+[|–—]\s+", raw_title) if clean_text(part)]
            if len(parts) >= 2:
                title, company = parts[0], parts[1]
            else:
                company = "Employer listed on Job Bank"
        if not location:
            location_match = re.search(r"\b[A-Za-z .'-]+\s*\((?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\)", description)
            location = location_match.group(0) if location_match else "Canada"
        official_gc = any(term in f"{source_text} {description} {link}".lower() for term in ("jobs.gc.ca", "government of canada"))
        job = build_dynamic_job(
            company=company, title=title, location=location, url=link,
            source_name="Government of Canada Jobs" if official_gc else "Government of Canada Job Bank",
            source_type="official_government" if official_gc else "government_aggregator",
            source_family="government_canada", as_of=as_of, posted_value=posted,
            description=description, category=f"{search_name} {'student internship co-op' if force_student else ''}",
            salary=salary, default_country="Canada", official=official_gc,
            provenance="federal government posting indexed by Job Bank" if official_gc else "Job Bank discovery record; verify employer posting",
            discovery_query=search_name, force_student=force_student,
            source_id=f"jobbank-rss-{search_name}-{index}-{link}", status=default_status,
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(items), pages=1)


def fetch_job_bank_search(
    session: requests.Session,
    search: dict[str, Any],
    as_of: datetime,
    timeout: int,
) -> SourceResult:
    name = search["name"]
    params = {str(k): str(v) for k, v in (search.get("params") or {}).items() if v is not None}
    params.setdefault("sort", "D")
    params.setdefault("fage", str(search.get("days", 60)))
    params.setdefault("rows", str(search.get("rows", 100)))
    force_student = bool(search.get("force_student"))
    status = search.get("status", "open_when_verified")
    rss_url = search.get("rss_url") or "https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed"
    html_url = search.get("html_url") or "https://www.jobbank.gc.ca/jobsearch/jobsearch"

    rss_error: Exception | None = None
    try:
        rss_text = request_text(session, rss_url, timeout, params=params)
        result = parse_job_bank_rss(rss_text, as_of, search_name=name, force_student=force_student, default_status=status)
        if result.retrieved:
            result.note = "RSS feed"
            return result
    except Exception as exc:
        rss_error = exc

    max_pages = int(search.get("fallback_pages", 3))
    all_jobs: list[dict[str, Any]] = []
    retrieved = 0
    pages_used = 0
    seen_urls: set[str] = set()
    for page in range(1, max_pages + 1):
        page_params = dict(params)
        if page > 1:
            page_params["page"] = str(page)
        page_text = request_text(session, html_url, timeout, params=page_params)
        parsed = parse_job_bank_html(page_text, as_of, search_name=name, force_student=force_student, default_status=status)
        pages_used += 1
        retrieved += parsed.retrieved or 0
        new_jobs = []
        for job in parsed.jobs:
            if job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                new_jobs.append(job)
        all_jobs.extend(new_jobs)
        if not parsed.retrieved or not new_jobs:
            break
        time.sleep(0.15)
    note = "HTML fallback"
    if rss_error:
        note += f" after RSS error: {type(rss_error).__name__}"
    return SourceResult(jobs=all_jobs, retrieved=retrieved, pages=pages_used, note=note)


def _section_text_until_next_heading(heading: Any) -> str:
    pieces: list[str] = []
    for sibling in heading.next_siblings:
        name = getattr(sibling, "name", None)
        if name in {"h2", "h3", "h4"}:
            break
        if hasattr(sibling, "get_text"):
            pieces.append(sibling.get_text(" ", strip=True))
        else:
            pieces.append(str(sibling))
    return clean_text(" ".join(pieces))


def parse_federal_student_program_page(
    text: str,
    page_url: str,
    as_of: datetime,
    *,
    base_program: dict[str, Any] | None = None,
) -> SourceResult:
    soup = BeautifulSoup(text, "html.parser")
    jobs: list[dict[str, Any]] = []
    retrieved = 0
    if base_program:
        required = [normalize(item) for item in base_program.get("required_phrases", [])]
        page_norm = normalize(soup.get_text(" ", strip=True))
        if required and not all(item in page_norm for item in required):
            raise RefreshError(f"Required program-page text not found for {base_program.get('title')}")
        base_job = build_dynamic_job(
            company=base_program.get("company", "Government of Canada"),
            title=base_program["title"], location=base_program.get("location", "Across Canada"),
            url=base_program.get("url") or page_url, source_name=base_program.get("source_name", "Government of Canada student programs"),
            source_type="official_program", source_family="government_canada", as_of=as_of,
            posted_value=base_program.get("posted") or "Recently", deadline_value=base_program.get("deadline"),
            description=base_program.get("description") or page_norm[:2000], category="student internship public service government policy business law",
            season=base_program.get("season", "Year-round / ongoing"), salary=base_program.get("compensation"),
            default_country="Canada", official=True, provenance="official Government of Canada student program",
            source_id=base_program.get("id") or slug(base_program["title"]), force_student=True, force_include=True,
            status=base_program.get("status", "ongoing_program"),
        )
        retrieved += 1
        if base_job:
            jobs.append(base_job)

    for index, heading in enumerate(soup.find_all(["h2", "h3", "h4"])):
        title = clean_text(heading.get_text(" ", strip=True))
        title_norm = normalize(title)
        if not title or not any(term in title_norm for term in ("student", "intern", "co op", "recruitment", "research affiliate")):
            continue
        if title_norm in {"students", "student jobs", "eligibility", "how to apply", "related links"}:
            continue
        section_text = _section_text_until_next_heading(heading)
        if not section_text:
            continue
        link_node = heading.find("a", href=True)
        if not link_node:
            link_node = heading.find_next("a", href=True)
        href = urljoin(page_url, link_node.get("href")) if link_node else page_url
        deadline_match = re.search(
            r"(?:application deadline|deadline to apply|apply by|deadline)\s*[:\-]?\s*([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2})",
            section_text,
            flags=re.IGNORECASE,
        )
        compensation_match = re.search(r"\$[\d,.]+\s*(?:to|–|-)\s*\$[\d,.]+\s*(?:per hour|hourly)?", section_text, flags=re.IGNORECASE)
        job = build_dynamic_job(
            company="Government of Canada", title=title, location="Across Canada", url=href,
            source_name="Government of Canada student programs", source_type="official_program",
            source_family="government_canada", as_of=as_of, posted_value="Recently",
            deadline_value=deadline_match.group(1) if deadline_match else None,
            description=section_text, category="student internship public service government policy business law",
            season=None, salary=compensation_match.group(0) if compensation_match else None,
            default_country="Canada", official=True, provenance="official Government of Canada student program",
            source_id=f"gc-program-index-{index}-{title}", force_student=True, force_include=True,
            status="program_inventory",
        )
        retrieved += 1
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=retrieved, pages=1)


def fetch_federal_student_index(
    session: requests.Session,
    program: dict[str, Any],
    as_of: datetime,
    timeout: int,
) -> SourceResult:
    text = request_text(session, program["url"], timeout)
    return parse_federal_student_program_page(
        text, program["url"], as_of, base_program=program
    )


def fetch_monitored_program(
    session: requests.Session,
    program: dict[str, Any],
    as_of: datetime,
    timeout: int,
) -> SourceResult:
    url = program["url"]
    text = request_text(session, url, timeout)
    page_text = clean_text(BeautifulSoup(text, "html.parser").get_text(" ", strip=True))
    page_norm = normalize(page_text)
    required = [normalize(item) for item in program.get("required_phrases", [])]
    if required and not all(item in page_norm for item in required):
        raise RefreshError("Program page loaded but required identifying text was absent")
    if any(normalize(marker) in page_norm for marker in program.get("closed_markers", [])) and not program.get("allow_closed"):
        return SourceResult(jobs=[], retrieved=1, note="Program page indicates that applications are closed")
    deadline = program.get("deadline")
    if program.get("parse_deadline", True):
        match = re.search(
            r"(?:application deadline|deadline to apply|apply by|deadline|closing date)\s*[:\-]?\s*([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2})",
            page_text,
            flags=re.IGNORECASE,
        )
        if match:
            deadline = match.group(1)
    job = build_dynamic_job(
        company=program.get("company", "Government of Canada"), title=program["title"],
        location=program.get("location", "Across Canada"), url=url,
        source_name=program.get("source_name", "Official government student program"),
        source_type=program.get("source_type", "official_program"),
        source_family=program.get("source_family", "government_canada"),
        as_of=as_of, posted_value=program.get("posted") or "Recently", deadline_value=deadline,
        description=program.get("description") or page_text[:2500],
        category=program.get("category", "student internship business law policy public service"),
        season=program.get("season"), salary=program.get("compensation"),
        default_country=program.get("country", "Canada"), official=bool(program.get("official", True)),
        provenance=program.get("provenance", "official government program page"),
        source_id=program.get("id") or slug(program["title"]),
        force_student=bool(program.get("force_student", True)),
        force_include=bool(program.get("force_include", True)),
        status=program.get("status", "program_inventory"),
    )
    if job:
        # Program and recruiting pages often describe a future cycle rather than an
        # immediately open vacancy. Configuration can therefore override the generic
        # eligibility inference without changing ordinary posting logic.
        override_fields = (
            "eligibility_status", "eligibility_label", "eligibility_notes",
            "work_auth", "class_year", "warning", "contact", "contact_email",
            "fit_score", "fit_label", "fit_reasons", "skills",
            "description", "interest", "term", "mode",
        )
        for field in override_fields:
            if field in program:
                job[field] = program[field]
        if program.get("lane"):
            job["lane"] = program["lane"]
        job["email"] = make_email(job)
    return SourceResult(jobs=[job] if job else [], retrieved=1, pages=1)


# ----------------------------- Official ATS feeds -----------------------------

def fetch_greenhouse(session: requests.Session, board: dict[str, str], as_of: datetime, timeout: int) -> SourceResult:
    token = board["token"]
    payload = request_json(session, f"https://boards-api.greenhouse.io/v1/boards/{quote(token)}/jobs?content=true", timeout)
    rows = payload.get("jobs", []) if isinstance(payload, dict) else []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        location = clean_text((row.get("location") or {}).get("name"))
        job = build_dynamic_job(
            company=board.get("company") or token, title=row.get("title", ""), location=location,
            url=row.get("absolute_url", ""), source_name=f"{board.get('company') or token} Careers",
            source_type="official_ats", source_family="greenhouse", as_of=as_of,
            posted_value=row.get("first_published") or row.get("updated_at"), description=row.get("content", ""),
            default_country=board.get("default_country"), official=True,
            provenance="official Greenhouse career endpoint", source_id=f"greenhouse-{token}-{row.get('id')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(rows))


def fetch_lever(session: requests.Session, board: dict[str, str], as_of: datetime, timeout: int) -> SourceResult:
    token = board["token"]
    rows = request_json(session, f"https://api.lever.co/v0/postings/{quote(token)}?mode=json", timeout)
    rows = rows if isinstance(rows, list) else []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        categories = row.get("categories") or {}
        location = categories.get("location") or row.get("workplaceType") or ""
        description = " ".join(filter(None, [row.get("descriptionPlain"), row.get("additionalPlain")]))
        job = build_dynamic_job(
            company=board.get("company") or token, title=row.get("text", ""), location=location,
            url=row.get("hostedUrl") or row.get("applyUrl") or "", source_name=f"{board.get('company') or token} Careers",
            source_type="official_ats", source_family="lever", as_of=as_of, posted_value=row.get("createdAt"),
            description=description, default_country=board.get("default_country"), official=True,
            provenance="official Lever career endpoint", source_id=f"lever-{token}-{row.get('id')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(rows))


def fetch_ashby(session: requests.Session, board: dict[str, str], as_of: datetime, timeout: int) -> SourceResult:
    token = board["token"]
    payload = request_json(session, f"https://api.ashbyhq.com/posting-api/job-board/{quote(token)}", timeout)
    rows = payload.get("jobs", []) if isinstance(payload, dict) else []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        location: Any = row.get("location") or row.get("secondaryLocations") or ""
        if isinstance(location, list):
            location = ", ".join(clean_text(item.get("location") if isinstance(item, dict) else item) for item in location)
        description = " ".join(filter(None, [row.get("descriptionPlain"), row.get("descriptionHtml")]))
        job = build_dynamic_job(
            company=board.get("company") or token, title=row.get("title", ""), location=location,
            url=row.get("jobUrl") or row.get("applyUrl") or "", source_name=f"{board.get('company') or token} Careers",
            source_type="official_ats", source_family="ashby", as_of=as_of, posted_value=row.get("publishedAt"),
            description=description, default_country=board.get("default_country"), official=True,
            provenance="official Ashby career endpoint", source_id=f"ashby-{token}-{row.get('id')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(rows))


def fetch_smartrecruiters(session: requests.Session, board: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    identifier = board["identifier"]
    max_pages = int(board.get("max_pages", 3))
    limit = min(int(board.get("limit", 100)), 100)
    jobs: list[dict[str, Any]] = []
    retrieved = 0
    pages = 0
    for page in range(max_pages):
        payload = request_json(
            session,
            f"https://api.smartrecruiters.com/v1/companies/{quote(identifier)}/postings",
            timeout,
            params={"limit": limit, "offset": page * limit},
        )
        rows = payload.get("content", []) if isinstance(payload, dict) else []
        pages += 1
        retrieved += len(rows)
        for row in rows:
            loc = row.get("location") or {}
            location = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            employment = clean_text((row.get("typeOfEmployment") or {}).get("label"))
            function = clean_text((row.get("function") or {}).get("label"))
            job_id = row.get("id")
            url = row.get("ref") or f"https://jobs.smartrecruiters.com/{identifier}/{job_id}"
            job = build_dynamic_job(
                company=board.get("company") or identifier, title=row.get("name", ""), location=location,
                url=url, source_name=f"{board.get('company') or identifier} Careers",
                source_type="official_ats", source_family="smartrecruiters", as_of=as_of,
                posted_value=row.get("releasedDate"), description=f"{employment} {function}", category=function,
                default_country=board.get("default_country"), official=True,
                provenance="official SmartRecruiters career endpoint", source_id=f"smartrecruiters-{identifier}-{job_id}",
            )
            if job:
                jobs.append(job)
        total = int(payload.get("totalFound", retrieved) or retrieved) if isinstance(payload, dict) else retrieved
        if not rows or retrieved >= total:
            break
    return SourceResult(jobs=jobs, retrieved=retrieved, pages=pages)


def fetch_workday(session: requests.Session, board: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    """Query an official Workday CXS endpoint with targeted student terms.

    General career sites are searched using several internship/student phrases so a large
    employer cannot hide relevant roles beyond an arbitrary first-page cutoff. Dedicated
    campus/student sites can opt into a complete scan with ``scan_all``.
    """
    host = board["host"].replace("https://", "").rstrip("/")
    tenant = board["tenant"]
    site = board["site"]
    limit = min(max(int(board.get("limit", 20)), 1), 20)
    endpoint = f"https://{host}/wday/cxs/{quote(tenant)}/{quote(site)}/jobs"

    if board.get("scan_all"):
        search_texts = [clean_text(board.get("search_text", ""))]
        max_pages_per_search = int(board.get("max_pages", 20))
    else:
        configured_terms = board.get("search_texts")
        search_texts = configured_terms if isinstance(configured_terms, list) and configured_terms else [
            "intern", "student", "co-op", "summer analyst", "summer associate",
        ]
        max_pages_per_search = int(board.get("max_pages_per_search", board.get("max_pages", 6)))

    jobs: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    retrieved = 0
    pages = 0
    completed_queries: list[str] = []

    for raw_search_text in search_texts:
        search_text = clean_text(raw_search_text)
        completed_queries.append(search_text or "all jobs")
        for page in range(max(1, max_pages_per_search)):
            payload = request_json(
                session,
                endpoint,
                timeout,
                method="POST",
                json_body={
                    "appliedFacets": {},
                    "limit": limit,
                    "offset": page * limit,
                    "searchText": search_text,
                },
            )
            rows = payload.get("jobPostings", []) if isinstance(payload, dict) else []
            pages += 1
            retrieved += len(rows)
            for row in rows:
                external_path = clean_text(row.get("externalPath"))
                if not external_path:
                    continue
                path_key = external_path.lower()
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                public_url = f"https://{host}/{site}{external_path}"
                location = row.get("locationsText") or row.get("location") or board.get("fallback_location", "")
                bullet_fields = row.get("bulletFields") or []
                if not isinstance(bullet_fields, list):
                    bullet_fields = [bullet_fields]
                description = " ".join(
                    filter(None, [
                        " ".join(clean_text(value) for value in bullet_fields),
                        clean_text(row.get("timeType")),
                        clean_text(row.get("jobFamily")),
                        clean_text(row.get("workerSubType")),
                    ])
                )
                job = build_dynamic_job(
                    company=board.get("company") or tenant,
                    title=row.get("title", ""),
                    location=location,
                    url=public_url,
                    source_name=f"{board.get('company') or tenant} Careers",
                    source_type="official_ats",
                    source_family="workday",
                    as_of=as_of,
                    posted_value=row.get("postedOn"),
                    description=description,
                    category=f"Workday search: {search_text}",
                    default_country=board.get("default_country"),
                    official=True,
                    provenance="official Workday career endpoint",
                    discovery_query=search_text or "all jobs",
                    source_id=f"workday-{tenant}-{site}-{external_path}",
                )
                if job:
                    jobs.append(job)
            total = int(payload.get("total", retrieved) or 0) if isinstance(payload, dict) else 0
            if not rows or (total and (page + 1) * limit >= total):
                break

    note = "Targeted Workday queries: " + ", ".join(completed_queries)
    return SourceResult(jobs=jobs, retrieved=retrieved, pages=pages, note=note)


def _iter_jsonld_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_jsonld_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_jsonld_nodes(child)


def parse_jsonld_job_page(text: str, page_url: str, as_of: datetime, page: dict[str, Any]) -> SourceResult:
    soup = BeautifulSoup(text, "html.parser")
    nodes: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            parsed = json.loads(script.string or script.get_text() or "")
        except json.JSONDecodeError:
            continue
        for node in _iter_jsonld_nodes(parsed):
            node_type = node.get("@type")
            if node_type == "JobPosting" or (isinstance(node_type, list) and "JobPosting" in node_type):
                nodes.append(node)
    jobs: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        org = node.get("hiringOrganization") or {}
        company = org.get("name") if isinstance(org, dict) else org
        location_value = node.get("jobLocation") or node.get("applicantLocationRequirements") or ""
        locations: list[str] = []
        for loc in location_value if isinstance(location_value, list) else [location_value]:
            if isinstance(loc, dict):
                address = loc.get("address") or loc
                if isinstance(address, dict):
                    locations.append(", ".join(filter(None, [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")])))
                else:
                    locations.append(clean_text(address))
            else:
                locations.append(clean_text(loc))
        location = "; ".join(filter(None, locations)) or page.get("fallback_location", "")
        job = build_dynamic_job(
            company=company or page.get("company", ""), title=node.get("title", ""), location=location,
            url=node.get("url") or page_url, source_name=f"{page.get('company') or company} Careers",
            source_type="official_page", source_family="jsonld", as_of=as_of,
            posted_value=node.get("datePosted"), deadline_value=node.get("validThrough"),
            description=node.get("description", ""), category=node.get("occupationalCategory", ""),
            salary=clean_text(node.get("baseSalary")), default_country=page.get("default_country"), official=True,
            provenance="official career page structured data", source_id=f"jsonld-{page.get('company')}-{index}-{node.get('url')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(nodes), pages=1)


def fetch_jsonld_page(session: requests.Session, page: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    text = request_text(session, page["url"], timeout)
    return parse_jsonld_job_page(text, page["url"], as_of, page)


def _xml_direct_text(node: ET.Element, names: Sequence[str]) -> str:
    wanted = {name.lower() for name in names}
    for child in node:
        if child.tag.split("}")[-1].lower() in wanted:
            return clean_text(child.text or "")
    return ""


def parse_generic_rss(text: str, feed: dict[str, Any], as_of: datetime) -> SourceResult:
    """Parse a standards-based RSS or Atom job feed.

    This adapter is deliberately conservative: it only retains records that satisfy the
    normal student/internship and business-law relevance screen. Feed records remain
    discovery leads unless the configured feed is an official employer/government feed.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RefreshError(f"RSS/Atom XML could not be parsed: {exc}") from exc

    items = [
        node for node in root.iter()
        if node.tag.split("}")[-1].lower() in {"item", "entry"}
    ]
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        raw_title = _xml_direct_text(item, ("title",))
        link = _xml_direct_text(item, ("link", "guid", "id"))
        if not link:
            for child in item:
                if child.tag.split("}")[-1].lower() == "link" and child.get("href"):
                    link = clean_text(child.get("href"))
                    if link:
                        break
        description = _xml_direct_text(item, ("description", "summary", "content", "encoded"))
        posted = _xml_direct_text(item, ("pubDate", "published", "updated", "date"))
        author = _xml_direct_text(item, ("author", "creator"))
        categories = " ".join(
            clean_text(child.text or child.get("term") or "")
            for child in item
            if child.tag.split("}")[-1].lower() == "category"
        )

        title = raw_title
        company = clean_text(feed.get("company")) or _text_after_label(description, ("Employer", "Company", "Organization")) or author
        location = _text_after_label(description, ("Location", "City", "Region")) or clean_text(feed.get("fallback_location"))
        if not company:
            parts = [clean_text(part) for part in re.split(r"\s+[|–—]\s+", raw_title) if clean_text(part)]
            if len(parts) >= 2:
                title, company = parts[0], parts[1]
            else:
                company = clean_text(feed.get("fallback_company")) or f"Employer listed on {feed.get('name', 'job feed')}"
        if not location:
            province_match = re.search(
                r"[A-Za-z .'-]+\s*\((?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\)",
                description,
            )
            location = province_match.group(0) if province_match else ("Canada" if feed.get("default_country") == "Canada" else "United States")

        official = bool(feed.get("official", False))
        source_family = clean_text(feed.get("family")) or "rss"
        job = build_dynamic_job(
            company=company, title=title, location=location, url=link,
            source_name=feed.get("name", "Public job feed"),
            source_type=feed.get("source_type", "public_job_board"), source_family=source_family,
            as_of=as_of, posted_value=posted, description=description, category=categories,
            default_country=feed.get("default_country"), official=official,
            provenance=feed.get("provenance", "public RSS/Atom discovery feed; verify the employer posting"),
            discovery_query=feed.get("name"), force_student=bool(feed.get("force_student", False)),
            source_id=f"rss-{source_family}-{index}-{link}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(items), pages=1)


def fetch_rss_feed(session: requests.Session, feed: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    text = request_text(session, feed["url"], timeout)
    return parse_generic_rss(text, feed, as_of)


def _successfactors_container(anchor: Any) -> Any:
    for name in ("tr", "article", "li"):
        parent = anchor.find_parent(name)
        if parent is not None:
            return parent
    for parent in anchor.parents:
        if getattr(parent, "name", None) != "div":
            continue
        classes = " ".join(parent.get("class", []))
        if re.search(r"job|result|listing|data-row", classes, flags=re.IGNORECASE):
            return parent
    return anchor.parent or anchor


def parse_successfactors_search_page(
    text: str, page_url: str, board: dict[str, Any], as_of: datetime
) -> SourceResult:
    """Parse server-rendered SAP SuccessFactors career-search results."""
    soup = BeautifulSoup(text, "html.parser")
    anchors = []
    seen_links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href"))
        lower = href.lower()
        if not ("/job/" in lower or "career_job_req_id" in lower or "jobid=" in lower):
            continue
        absolute = urljoin(page_url, href)
        if absolute in seen_links:
            continue
        title = clean_text(anchor.get_text(" ", strip=True) or anchor.get("title"))
        if not title or normalize(title) in {"apply", "view job", "learn more", "details"}:
            continue
        seen_links.add(absolute)
        anchors.append((anchor, absolute, title))

    jobs: list[dict[str, Any]] = []
    for index, (anchor, absolute, title) in enumerate(anchors):
        card = _successfactors_container(anchor)
        card_text = clean_text(card.get_text(" ", strip=True))
        location_node = card.select_one(
            ".jobLocation, .job-location, .location, [data-careersite-propertyid='location'], [itemprop='jobLocation']"
        )
        date_node = card.select_one(
            ".jobDate, .job-date, .date, time, [data-careersite-propertyid='date'], [itemprop='datePosted']"
        )
        location = clean_text(location_node.get_text(" ", strip=True) if location_node else "")
        if not location:
            location = _text_after_label(card_text, ("Location", "Primary Location", "Work Location"))
        posted = clean_text(
            date_node.get("datetime") if date_node and date_node.get("datetime")
            else date_node.get_text(" ", strip=True) if date_node else ""
        )
        if not posted:
            date_match = re.search(
                r"(?:Date posted|Posted|Publication date)\s*[:\-]?\s*([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2}|20\d{2}-\d{2}-\d{2})",
                card_text, flags=re.IGNORECASE,
            )
            posted = date_match.group(1) if date_match else ""
        job = build_dynamic_job(
            company=board.get("company", "Employer"), title=title,
            location=location or board.get("fallback_location", ""), url=absolute,
            source_name=f"{board.get('company', 'Employer')} Careers",
            source_type="official_ats", source_family="successfactors", as_of=as_of,
            posted_value=posted, description=card_text, category=board.get("category", ""),
            default_country=board.get("default_country"), official=True,
            provenance="official SAP SuccessFactors career page",
            discovery_query=board.get("search_label") or board.get("id"),
            force_student=bool(board.get("force_student", False)),
            source_id=f"successfactors-{board.get('id')}-{index}-{absolute}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(anchors), pages=1)


def fetch_successfactors(
    session: requests.Session, board: dict[str, Any], as_of: datetime, timeout: int
) -> SourceResult:
    search_url = board["search_url"]
    max_pages = max(1, int(board.get("max_pages", 4)))
    page_size = max(1, int(board.get("page_size", 25)))
    offset_param = board.get("offset_param", "startrow")
    base_params = {str(key): str(value) for key, value in (board.get("params") or {}).items()}
    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    retrieved = 0
    pages = 0
    for page in range(max_pages):
        params = dict(base_params)
        if page:
            params[offset_param] = str(page * page_size)
        text = request_text(session, search_url, timeout, params=params)
        parsed = parse_successfactors_search_page(text, search_url, board, as_of)
        pages += 1
        retrieved += parsed.retrieved or 0
        new_jobs = []
        for job in parsed.jobs:
            if job["url"] in seen_urls:
                continue
            seen_urls.add(job["url"])
            new_jobs.append(job)
        all_jobs.extend(new_jobs)
        if not parsed.retrieved or (page and not new_jobs):
            break
    return SourceResult(jobs=all_jobs, retrieved=retrieved, pages=pages)


# -------------------------- U.S. government and APIs --------------------------

def fetch_usajobs(session: requests.Session, query: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    api_key = os.getenv("USAJOBS_API_KEY")
    email_address = os.getenv("USAJOBS_EMAIL")
    if not api_key or not email_address:
        raise SourceSkipped("Set USAJOBS_API_KEY and USAJOBS_EMAIL repository secrets")
    params = {
        "Keyword": query["keyword"],
        "DatePosted": query.get("days", 60),
        "ResultsPerPage": min(int(query.get("results_per_page", 500)), 500),
        "Page": 1,
    }
    if query.get("location"):
        params["LocationName"] = query["location"]
    if query.get("hiring_path"):
        params["HiringPath"] = query["hiring_path"]
    payload = request_json(
        session,
        "https://data.usajobs.gov/api/search",
        timeout,
        params=params,
        headers={"Host": "data.usajobs.gov", "User-Agent": email_address, "Authorization-Key": api_key},
    )
    result = payload.get("SearchResult", {}) if isinstance(payload, dict) else {}
    rows = result.get("SearchResultItems", []) or []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        descriptor = row.get("MatchedObjectDescriptor") or {}
        details = ((descriptor.get("UserArea") or {}).get("Details") or {})
        location = descriptor.get("PositionLocationDisplay") or "; ".join(
            clean_text(item.get("LocationName")) for item in descriptor.get("PositionLocation", []) if isinstance(item, dict)
        )
        remuneration = descriptor.get("PositionRemuneration") or []
        salary = ""
        if remuneration:
            pay = remuneration[0]
            salary = f"{pay.get('MinimumRange', '')}–{pay.get('MaximumRange', '')} {pay.get('RateIntervalCode', '')}".strip()
        description = " ".join(filter(None, [
            details.get("JobSummary"), details.get("MajorDuties"), details.get("Requirements"),
            details.get("Education"), descriptor.get("QualificationSummary"), details.get("HiringPath"),
        ]))
        job = build_dynamic_job(
            company=descriptor.get("OrganizationName") or "U.S. Federal Government",
            title=descriptor.get("PositionTitle", ""), location=location or "United States",
            url=descriptor.get("PositionURI", ""), source_name="USAJOBS",
            source_type="official_government", source_family="government_us", as_of=as_of,
            posted_value=descriptor.get("PublicationStartDate"), deadline_value=descriptor.get("ApplicationCloseDate"),
            description=description, category=f"USAJOBS {query.get('keyword')} student internship pathways",
            salary=salary, default_country="United States", official=True,
            provenance="official U.S. federal jobs API", discovery_query=query.get("keyword"),
            force_student=True, source_id=f"usajobs-{row.get('MatchedObjectId')}",
        )
        if job:
            jobs.append(job)
    total = int((result.get("SearchResultCountAll") or len(rows)))
    return SourceResult(jobs=jobs, retrieved=len(rows), pages=1, note=f"{total} total API matches before pagination/filtering")


def fetch_careeronestop(session: requests.Session, query: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    token = os.getenv("CAREERONESTOP_TOKEN")
    user_id = os.getenv("CAREERONESTOP_USER_ID")
    if not token or not user_id:
        raise SourceSkipped("Set CAREERONESTOP_TOKEN and CAREERONESTOP_USER_ID repository secrets")
    keyword = quote(query["keyword"], safe="")
    location = quote(query.get("location", "United States"), safe="")
    endpoint = (
        f"https://api.careeronestop.org/v2/jobsearch/{quote(user_id, safe='')}/{keyword}/{location}/"
        f"{query.get('radius', 0)}/acquisitiondate/DESC/0/{query.get('page_size', 100)}/{query.get('days', 60)}"
    )
    payload = request_json(
        session, endpoint, timeout,
        params={"showFilters": "false", "enableJobDescriptionSnippet": "true", "enableMetaData": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = payload.get("Jobs", []) if isinstance(payload, dict) else []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        job = build_dynamic_job(
            company=row.get("Company", ""), title=row.get("JobTitle", ""), location=row.get("Location", ""),
            url=row.get("URL", ""), source_name="CareerOneStop", source_type="government_aggregator",
            source_family="careeronestop", as_of=as_of, posted_value=row.get("AcquisitionDate"),
            description=row.get("DescriptionSnippet", ""), category=f"{query.get('keyword')} student internship",
            default_country="United States", official=False,
            provenance="U.S. Department of Labor-sponsored discovery API; verify employer posting",
            discovery_query=query.get("keyword"), source_id=f"careeronestop-{row.get('JvId')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(rows), pages=1)


def fetch_adzuna(session: requests.Session, query: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise SourceSkipped("Set ADZUNA_APP_ID and ADZUNA_APP_KEY repository secrets")
    country_code = query.get("country_code", "ca")
    country_name = "Canada" if country_code == "ca" else "United States"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": query.get("results_per_page", 50),
        "sort_by": "date",
        "max_days_old": query.get("days", 45),
        "what": query["keyword"],
    }
    payload = request_json(session, f"https://api.adzuna.com/v1/api/jobs/{country_code}/search/1", timeout, params=params)
    rows = payload.get("results", []) if isinstance(payload, dict) else []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        company = clean_text((row.get("company") or {}).get("display_name"))
        location = clean_text((row.get("location") or {}).get("display_name"))
        salary = ""
        if row.get("salary_min") or row.get("salary_max"):
            salary = f"{row.get('salary_min') or ''}–{row.get('salary_max') or ''}".strip("–")
        job = build_dynamic_job(
            company=company, title=row.get("title", ""), location=location, url=row.get("redirect_url", ""),
            source_name="Adzuna", source_type="search_api", source_family="adzuna", as_of=as_of,
            posted_value=row.get("created"), description=row.get("description", ""), salary=salary,
            default_country=country_name, official=False, provenance="job-search API; verify employer posting",
            discovery_query=query["keyword"], source_id=f"adzuna-{country_code}-{row.get('id')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(rows))


def fetch_jooble(session: requests.Session, query: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    api_key = os.getenv("JOOBLE_API_KEY")
    if not api_key:
        raise SourceSkipped("Set JOOBLE_API_KEY repository secret")
    payload = request_json(
        session, f"https://jooble.org/api/{quote(api_key)}", timeout, method="POST",
        json_body={"keywords": query["keyword"], "location": query["location"], "page": "1"},
    )
    rows = payload.get("jobs", []) if isinstance(payload, dict) else []
    default_country = "Canada" if "canada" in query["location"].lower() else "United States"
    jobs: list[dict[str, Any]] = []
    for row in rows:
        job = build_dynamic_job(
            company=row.get("company", ""), title=row.get("title", ""), location=row.get("location", ""),
            url=row.get("link", ""), source_name="Jooble", source_type="search_api", source_family="jooble",
            as_of=as_of, posted_value=row.get("updated"), description=row.get("snippet", ""), salary=row.get("salary"),
            category=f"{query['keyword']} student internship", default_country=default_country, official=False,
            provenance="job-search API; verify employer posting", discovery_query=query["keyword"],
            source_id=f"jooble-{row.get('id')}",
        )
        if job:
            jobs.append(job)
    return SourceResult(jobs=jobs, retrieved=len(rows))


def fetch_the_muse(session: requests.Session, config: dict[str, Any], as_of: datetime, timeout: int) -> SourceResult:
    max_pages = int(config.get("max_pages", 10))
    jobs: list[dict[str, Any]] = []
    retrieved = 0
    pages = 0
    for page in range(1, max_pages + 1):
        payload = request_json(
            session, "https://www.themuse.com/api/public/jobs", timeout,
            params={"page": page, "level": config.get("level", "Internship")},
        )
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        pages += 1
        retrieved += len(rows)
        for row in rows:
            locations = "; ".join(clean_text(item.get("name")) for item in row.get("locations", []) if isinstance(item, dict))
            categories = " ".join(clean_text(item.get("name")) for item in row.get("categories", []) if isinstance(item, dict))
            company = clean_text((row.get("company") or {}).get("name"))
            url = clean_text((row.get("refs") or {}).get("landing_page"))
            job = build_dynamic_job(
                company=company, title=row.get("name", ""), location=locations, url=url,
                source_name="The Muse", source_type="search_api", source_family="the_muse", as_of=as_of,
                posted_value=row.get("publication_date"), description=row.get("contents", ""), category=categories,
                default_country=None, official=False, provenance="job discovery API; verify employer posting",
                source_id=f"muse-{row.get('id')}", force_student=True,
            )
            if job:
                jobs.append(job)
        if not rows:
            break
    return SourceResult(jobs=jobs, retrieved=retrieved, pages=pages)


# -------------------------- Deduplication and output --------------------------

def dedupe_key(job: dict[str, Any]) -> str:
    company = normalize(job.get("company"))
    title = normalize(job.get("title"))
    title = re.sub(r"\b(?:fall|summer|spring|winter)\s+20\d{2}\b", "", title)
    title = re.sub(r"\b(?:internship|intern|co op|coop|student)\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return f"{company}|{title}|{job.get('country')}"


def retention_allowed(job: dict[str, Any], as_of: datetime, max_age_days: int) -> bool:
    deadline = job.get("deadline")
    if deadline:
        try:
            if date.fromisoformat(deadline) < as_of.date():
                return False
        except ValueError:
            pass
    if job.get("source_type") == "official_program" and job.get("status") in {"ongoing_program", "program_inventory", "program_index", "open_program", "open_when_verified"}:
        return True
    if not job.get("_curated"):
        try:
            posted = date.fromisoformat(job["posted_date"])
            age = (as_of.date() - posted).days
            return -7 <= age <= max_age_days
        except (KeyError, ValueError):
            return True
    try:
        posted = date.fromisoformat(job["posted_date"])
        age = (as_of.date() - posted).days
    except (KeyError, ValueError):
        return True
    if job.get("eligibility_status") == "future" or job.get("source_type") in {"official", "official_program"}:
        return age <= 180
    return age <= max(max_age_days + 20, 80)


def merge_jobs(seed_jobs: list[dict[str, Any]], dynamic_jobs: list[dict[str, Any]], as_of: datetime, max_age_days: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for seed in deepcopy(seed_jobs):
        seed["_curated"] = True
        seed.setdefault("source_family", "curated")
        seed.setdefault("provenance", "manually reviewed source record")
        seed.setdefault("posted_precision", "curated")
        if not seed.get("email"):
            seed["email"] = make_email(seed)
        candidates.append(seed)
    candidates.extend(dynamic_jobs)

    merged: dict[str, dict[str, Any]] = {}
    for job in candidates:
        if not retention_allowed(job, as_of, max_age_days):
            continue
        key = dedupe_key(job)
        current = merged.get(key)
        if current is None:
            merged[key] = job
            continue
        if current.get("_curated") and not job.get("_curated"):
            # Preserve manually reviewed copy, but improve its URL/source when the live official record is stronger.
            if job.get("official") and not current.get("official"):
                for field in ("url", "source_name", "source_type", "source_family", "official", "verified_at", "provenance"):
                    current[field] = job.get(field)
            continue
        if job.get("_curated") and not current.get("_curated"):
            merged[key] = job
            continue
        current_rank = (bool(current.get("official")), int(current.get("fit_score", 0)), current.get("posted_date", ""))
        new_rank = (bool(job.get("official")), int(job.get("fit_score", 0)), job.get("posted_date", ""))
        if new_rank > current_rank:
            merged[key] = job

    output = []
    for job in merged.values():
        clean = {key: value for key, value in job.items() if not key.startswith("_")}
        output.append(clean)
    output.sort(key=lambda item: (int(item.get("fit_score", 0)), item.get("posted_date", "")), reverse=True)
    return output


def execute_source(spec: SourceSpec) -> tuple[SourceRun, list[dict[str, Any]]]:
    started = time.monotonic()
    try:
        value = spec.call()
        result = value if isinstance(value, SourceResult) else SourceResult(jobs=value, retrieved=len(value))
        duration = round((time.monotonic() - started) * 1000)
        status = "success" if result.jobs else "empty"
        run = SourceRun(
            name=spec.name, family=spec.family, tier=spec.tier, status=status,
            retrieved=int(result.retrieved if result.retrieved is not None else len(result.jobs)),
            accepted=len(result.jobs), pages=result.pages, duration_ms=duration, note=result.note,
        )
        return run, result.jobs
    except SourceSkipped as exc:
        duration = round((time.monotonic() - started) * 1000)
        return SourceRun(spec.name, spec.family, spec.tier, "skipped", 0, 0, 0, duration, note=str(exc)), []
    except Exception as exc:
        duration = round((time.monotonic() - started) * 1000)
        error = f"{type(exc).__name__}: {exc}"
        return SourceRun(spec.name, spec.family, spec.tier, "error", 0, 0, 0, duration, error=error), []


def execute_specs(specs: list[SourceSpec], max_workers: int) -> tuple[list[SourceRun], list[dict[str, Any]]]:
    runs: list[SourceRun] = []
    jobs: list[dict[str, Any]] = []
    if not specs:
        return runs, jobs
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        future_map = {pool.submit(execute_source, spec): spec for spec in specs}
        for future in as_completed(future_map):
            run, source_jobs = future.result()
            runs.append(run)
            jobs.extend(source_jobs)
    runs.sort(key=lambda item: (item.family, item.name))
    return runs, jobs


def write_source_coverage(runs: list[SourceRun]) -> None:
    fields = ["name", "family", "tier", "status", "retrieved", "accepted", "pages", "duration_ms", "error", "note"]
    with COVERAGE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow(asdict(run))


def build_report(
    *,
    as_of: datetime,
    offline: bool,
    runs: list[SourceRun],
    seed_count: int,
    dynamic_jobs: list[dict[str, Any]],
    unique_dynamic_jobs: int,
    final_jobs: list[dict[str, Any]] | None,
    publish_blocked: bool,
    block_reasons: list[str],
) -> dict[str, Any]:
    status_counts = {status: sum(run.status == status for run in runs) for status in ("success", "empty", "skipped", "error")}
    family_counts: dict[str, dict[str, int]] = {}
    for run in runs:
        bucket = family_counts.setdefault(run.family, {"sources": 0, "retrieved": 0, "accepted": 0, "errors": 0})
        bucket["sources"] += 1
        bucket["retrieved"] += run.retrieved
        bucket["accepted"] += run.accepted
        bucket["errors"] += int(run.status == "error")
    return {
        "generated_at": as_of.isoformat().replace("+00:00", "Z"),
        "offline": offline,
        "publish_blocked": publish_blocked,
        "block_reasons": block_reasons,
        "seed_records": seed_count,
        "dynamic_candidates_raw": len(dynamic_jobs),
        "dynamic_candidates_unique": unique_dynamic_jobs,
        "total_jobs": len(final_jobs or []),
        "successful_core_sources": sum(run.tier == "core" and run.status in {"success", "empty"} for run in runs),
        "successful_source_families": sorted({run.family for run in runs if run.status in {"success", "empty"}}),
        "job_bank_completed": any(run.name.startswith("Job Bank:") and run.status == "success" for run in runs),
        "status_counts": status_counts,
        "family_counts": family_counts,
        "source_runs": [asdict(run) for run in runs],
        "errors": [{"source": run.name, "error": run.error} for run in runs if run.error],
    }


def write_report(report: dict[str, Any], runs: list[SourceRun]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_source_coverage(runs)


def write_outputs(payload: dict[str, Any], as_of: datetime) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    OUTPUT_JSON.write_text(serialized, encoding="utf-8")
    OUTPUT_JS.write_text("window.JOB_BOARD_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")

    fields = [
        "company", "title", "location", "country", "lane", "term", "mode", "posted_date",
        "posted_label", "deadline", "compensation", "fit_score", "fit_label", "eligibility_label",
        "eligibility_notes", "work_auth", "source_family", "source_name", "source_type", "official",
        "provenance", "url",
    ]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(payload["jobs"])
    write_outreach(payload["jobs"], as_of)


def write_outreach(jobs: list[dict[str, Any]], as_of: datetime) -> None:
    lines = [
        "# Katherine Du — Cold Email Library",
        "",
        f"Generated from the current board on {as_of.strftime('%B %-d, %Y')}. Replace `[Name]` with a specific recruiter, lawyer, manager, public servant, or alumnus whenever possible. For roles marked as an eligibility conflict, the email intentionally asks about a future cycle rather than implying current eligibility.",
        "",
    ]
    for index, job in enumerate(jobs, 1):
        email = job.get("email") or make_email(job)
        lines.extend([
            f"## {index}. {job.get('company')} — {job.get('title')}",
            "",
            f"**Eligibility note:** {job.get('eligibility_label')} — {job.get('eligibility_notes')}",
            "",
            f"**Source:** {job.get('source_name')} — {job.get('url')}",
            "",
            f"**Subject:** {email.get('subject')}",
            "",
            email.get("body", ""),
            "",
            "---",
            "",
        ])
    OUTREACH_PATH.write_text("\n".join(lines), encoding="utf-8")


def _make_session_call(function: Callable[..., SourceResult], *args: Any, **kwargs: Any) -> Callable[[], SourceResult]:
    def call() -> SourceResult:
        with create_session() as session:
            return function(session, *args, **kwargs)
    return call


def _fetch_text_feed(
    session: requests.Session,
    url: str,
    parser: Callable[[str, datetime], list[dict[str, Any]]],
    as_of: datetime,
    timeout: int,
) -> SourceResult:
    text = request_text(session, url, timeout)
    jobs = parser(text, as_of)
    return SourceResult(jobs=jobs, retrieved=len(jobs), pages=1)


def _fetch_json_feed(
    session: requests.Session,
    url: str,
    parser: Callable[[Any, datetime], list[dict[str, Any]]],
    as_of: datetime,
    timeout: int,
) -> SourceResult:
    payload = request_json(session, url, timeout)
    jobs = parser(payload, as_of)
    return SourceResult(jobs=jobs, retrieved=len(jobs), pages=1)


def build_source_specs(config: dict[str, Any], as_of: datetime, timeout: int) -> tuple[list[SourceSpec], list[SourceSpec]]:
    """Return (government/core sequential specs, parallel independent specs)."""
    government_specs: list[SourceSpec] = []
    parallel_specs: list[SourceSpec] = []

    gc = config.get("government_canada", {})
    for search in gc.get("job_bank_searches", []):
        if not search.get("enabled", True):
            continue
        government_specs.append(SourceSpec(
            name=f"Job Bank: {search['name']}", family="government_canada", tier=search.get("tier", "core"),
            call=_make_session_call(fetch_job_bank_search, search, as_of, timeout),
        ))
    for program in gc.get("programs", []):
        if not program.get("enabled", True):
            continue
        program_fetcher = fetch_federal_student_index if program.get("parser") == "federal_index" else fetch_monitored_program
        government_specs.append(SourceSpec(
            name=f"GC program: {program['title']}", family="government_canada", tier=program.get("tier", "core"),
            call=_make_session_call(program_fetcher, program, as_of, timeout),
        ))

    for program in config.get("government_us", {}).get("programs", []):
        if not program.get("enabled", True):
            continue
        parallel_specs.append(SourceSpec(
            name=f"U.S. program: {program['title']}", family="government_us", tier=program.get("tier", "standard"),
            call=_make_session_call(fetch_monitored_program, program, as_of, timeout),
        ))
    for query in config.get("government_us", {}).get("usajobs_queries", []):
        parallel_specs.append(SourceSpec(
            name=f"USAJOBS: {query['keyword']}", family="government_us", tier="optional",
            call=_make_session_call(fetch_usajobs, query, as_of, timeout),
        ))

    for program in config.get("monitored_opportunity_pages", []):
        if not program.get("enabled", True):
            continue
        family = program.get("source_family", "opportunity_programs")
        parallel_specs.append(SourceSpec(
            name=f"Program page: {program['title']}", family=family,
            tier=program.get("tier", "standard"),
            call=_make_session_call(fetch_monitored_program, program, as_of, timeout),
        ))

    feeds = config.get("public_feeds", {})
    if feeds.get("zapply_markdown"):
        parallel_specs.append(SourceSpec(
            "Zapply Internships 2027", "public_feeds", "standard",
            _make_session_call(_fetch_text_feed, feeds["zapply_markdown"], parse_zapply_markdown, as_of, timeout),
        ))
    if feeds.get("internship_engine_json"):
        parallel_specs.append(SourceSpec(
            "Automated Internship Engine", "public_feeds", "standard",
            _make_session_call(_fetch_json_feed, feeds["internship_engine_json"], parse_internship_engine, as_of, timeout),
        ))
    if feeds.get("canada_tracker_markdown"):
        parallel_specs.append(SourceSpec(
            "Daily Canadian Internship Tracker", "public_feeds", "standard",
            _make_session_call(_fetch_text_feed, feeds["canada_tracker_markdown"], parse_canada_tracker_markdown, as_of, timeout),
        ))

    for board in config.get("greenhouse_boards", []):
        if board.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"Greenhouse: {board.get('company') or board['token']}", "greenhouse", "standard",
                _make_session_call(fetch_greenhouse, board, as_of, timeout),
            ))
    for board in config.get("lever_boards", []):
        if board.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"Lever: {board.get('company') or board['token']}", "lever", "standard",
                _make_session_call(fetch_lever, board, as_of, timeout),
            ))
    for board in config.get("ashby_boards", []):
        if board.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"Ashby: {board.get('company') or board['token']}", "ashby", "standard",
                _make_session_call(fetch_ashby, board, as_of, timeout),
            ))
    for board in config.get("smartrecruiters_boards", []):
        if board.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"SmartRecruiters: {board.get('company') or board['identifier']}", "smartrecruiters", "standard",
                _make_session_call(fetch_smartrecruiters, board, as_of, timeout),
            ))
    for board in config.get("workday_boards", []):
        if board.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"Workday: {board.get('company') or board['tenant']}", "workday", "standard",
                _make_session_call(fetch_workday, board, as_of, timeout),
            ))
    for board in config.get("successfactors_boards", []):
        if board.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"SuccessFactors: {board.get('company') or board['id']}", "successfactors", "standard",
                _make_session_call(fetch_successfactors, board, as_of, timeout),
            ))
    for feed in config.get("rss_feeds", []):
        if feed.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"RSS: {feed.get('name') or feed['url']}", feed.get("family", "rss"), feed.get("tier", "standard"),
                _make_session_call(fetch_rss_feed, feed, as_of, timeout),
            ))
    for page in config.get("jsonld_pages", []):
        if page.get("enabled", True):
            parallel_specs.append(SourceSpec(
                f"Official page: {page.get('company')}", "jsonld", "standard",
                _make_session_call(fetch_jsonld_page, page, as_of, timeout),
            ))

    for query in config.get("careeronestop_queries", []):
        parallel_specs.append(SourceSpec(
            f"CareerOneStop: {query['keyword']}", "careeronestop", "optional",
            _make_session_call(fetch_careeronestop, query, as_of, timeout),
        ))
    for query in config.get("adzuna_queries", []):
        parallel_specs.append(SourceSpec(
            f"Adzuna {query.get('country_code', 'ca').upper()}: {query['keyword']}", "adzuna", "optional",
            _make_session_call(fetch_adzuna, query, as_of, timeout),
        ))
    for query in config.get("jooble_queries", []):
        parallel_specs.append(SourceSpec(
            f"Jooble {query['location']}: {query['keyword']}", "jooble", "optional",
            _make_session_call(fetch_jooble, query, as_of, timeout),
        ))
    muse = config.get("the_muse")
    if muse and muse.get("enabled", True):
        parallel_specs.append(SourceSpec(
            "The Muse internship API", "the_muse", "standard",
            _make_session_call(fetch_the_muse, muse, as_of, timeout),
        ))
    return government_specs, parallel_specs


def quality_gate_reasons(
    runs: list[SourceRun],
    *,
    unique_dynamic_jobs: int,
    minimum_successful_core_sources: int,
    minimum_successful_source_families: int,
    minimum_dynamic_jobs: int,
    require_job_bank_success: bool,
) -> tuple[list[str], int, list[str], bool]:
    """Evaluate publication safeguards without treating seed records as live coverage."""
    completed_statuses = {"success", "empty"}
    successful_core = sum(
        run.tier == "core" and run.status in completed_statuses for run in runs
    )
    successful_families = sorted({
        run.family for run in runs if run.status in completed_statuses
    })
    job_bank_completed = any(
        run.name.startswith("Job Bank:") and run.status == "success" for run in runs
    )
    reasons: list[str] = []
    if successful_core < minimum_successful_core_sources:
        reasons.append(
            f"Only {successful_core} core source(s) completed; required {minimum_successful_core_sources}."
        )
    if len(successful_families) < minimum_successful_source_families:
        reasons.append(
            f"Only {len(successful_families)} independent source family/families completed; "
            f"required {minimum_successful_source_families}."
        )
    if require_job_bank_success and not job_bank_completed:
        reasons.append("No Government of Canada Job Bank search produced an accepted relevant opportunity.")
    if unique_dynamic_jobs < minimum_dynamic_jobs:
        reasons.append(
            f"Only {unique_dynamic_jobs} unique live candidate(s) were accepted; required {minimum_dynamic_jobs}."
        )
    if runs and all(run.status in {"error", "skipped"} for run in runs):
        reasons.append("Every configured online source failed or was skipped.")
    return reasons, successful_core, successful_families, job_bank_completed


def main() -> int:
    args = parse_args()
    as_of = parse_as_of(args.as_of)
    seed_payload = load_json(SEED_PATH)
    config = load_json(CONFIG_PATH)
    seed_jobs = seed_payload.get("jobs", [])
    dynamic_jobs: list[dict[str, Any]] = []
    runs: list[SourceRun] = []

    if not args.offline:
        government_specs, parallel_specs = build_source_specs(config, as_of, args.timeout)
        # Government of Canada calls are deliberately sequential and lightly delayed.
        for index, spec in enumerate(government_specs):
            run, source_jobs = execute_source(spec)
            runs.append(run)
            dynamic_jobs.extend(source_jobs)
            if index + 1 < len(government_specs):
                time.sleep(max(0.0, args.source_delay))
        parallel_runs, parallel_jobs = execute_specs(parallel_specs, args.max_workers)
        runs.extend(parallel_runs)
        dynamic_jobs.extend(parallel_jobs)

    unique_dynamic = merge_jobs([], dynamic_jobs, as_of, args.max_age_days)
    jobs = merge_jobs(seed_jobs, dynamic_jobs, as_of, args.max_age_days)
    if args.strict and not args.offline:
        block_reasons, successful_core, successful_families, job_bank_completed = quality_gate_reasons(
            runs,
            unique_dynamic_jobs=len(unique_dynamic),
            minimum_successful_core_sources=args.minimum_successful_core_sources,
            minimum_successful_source_families=args.minimum_successful_source_families,
            minimum_dynamic_jobs=args.minimum_dynamic_jobs,
            require_job_bank_success=args.require_job_bank_success,
        )
    else:
        _, successful_core, successful_families, job_bank_completed = quality_gate_reasons(
            runs,
            unique_dynamic_jobs=len(unique_dynamic),
            minimum_successful_core_sources=0,
            minimum_successful_source_families=0,
            minimum_dynamic_jobs=0,
            require_job_bank_success=False,
        )
        block_reasons = []
    publish_blocked = bool(block_reasons)

    report = build_report(
        as_of=as_of, offline=args.offline, runs=runs, seed_count=len(seed_jobs),
        dynamic_jobs=dynamic_jobs, unique_dynamic_jobs=len(unique_dynamic), final_jobs=jobs, publish_blocked=publish_blocked,
        block_reasons=block_reasons,
    )
    write_report(report, runs)

    if publish_blocked:
        print("Refresh quality gate blocked publication:", file=sys.stderr)
        for reason in block_reasons:
            print(f"- {reason}", file=sys.stderr)
        print(f"Source diagnostics: {REPORT_PATH.relative_to(ROOT)}", file=sys.stderr)
        return 2

    counts = {
        "total": len(jobs),
        "canada": sum(job.get("country") == "Canada" for job in jobs),
        "united_states": sum(job.get("country") == "United States" for job in jobs),
        "likely": sum(job.get("eligibility_status") == "likely" for job in jobs),
        "government_canada": sum(job.get("source_family") == "government_canada" for job in jobs),
        "official": sum(bool(job.get("official")) for job in jobs),
    }
    generated_at = as_of.isoformat().replace("+00:00", "Z")
    metadata = deepcopy(seed_payload.get("metadata", {}))
    metadata.update({
        "generated_at": generated_at,
        "timezone": "America/Los_Angeles",
        "profile": PROFILE,
        "counts": counts,
        "coverage": {
            "description": "Government of Canada programs and Job Bank, U.S. federal sources, official ATS endpoints, and optional job-search APIs, followed by relevance screening and deduplication.",
            "guarantee": "No crawler can guarantee every job on the internet. Source health, failures, credentials, and provenance are recorded so coverage is auditable.",
            "recency_window_days": args.max_age_days,
            "configured_source_families": sorted({run.family for run in runs}) if runs else [],
        },
        "refresh": {
            "offline": args.offline,
            "dynamic_candidates_raw": len(dynamic_jobs),
            "dynamic_candidates_unique": len(unique_dynamic),
            "successful_sources": sum(run.status == "success" for run in runs),
            "empty_sources": sum(run.status == "empty" for run in runs),
            "failed_sources": sum(run.status == "error" for run in runs),
            "skipped_sources": sum(run.status == "skipped" for run in runs),
            "successful_core_sources": successful_core,
            "successful_source_families": successful_families,
            "job_bank_completed": job_bank_completed,
            "max_age_days": args.max_age_days,
            "degraded": any(run.status == "error" and run.tier == "core" for run in runs),
            "report": "data/refresh_report.json",
        },
    })
    payload = {"metadata": metadata, "jobs": jobs}
    write_outputs(payload, as_of)

    print(
        f"Refreshed {len(jobs)} jobs ({counts['canada']} Canada, {counts['united_states']} U.S.; "
        f"{len(unique_dynamic)} unique live candidates from {len(dynamic_jobs)} accepted source records)."
    )
    error_count = sum(run.status == "error" for run in runs)
    if error_count:
        print(f"{error_count} source(s) failed; see {REPORT_PATH.relative_to(ROOT)}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
