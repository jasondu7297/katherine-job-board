from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_public_site import build as build_public_site
from scripts.build_source_registry import build as build_source_registry
from scripts.build_standalone import build as build_standalone
from scripts.refresh_jobs import (
    SourceRun,
    build_dynamic_job,
    build_source_specs,
    fetch_monitored_program,
    infer_country,
    make_email,
    merge_jobs,
    parse_canada_tracker_markdown,
    parse_federal_student_program_page,
    parse_internship_engine,
    parse_generic_rss,
    parse_job_bank_html,
    parse_job_bank_rss,
    parse_successfactors_search_page,
    parse_zapply_markdown,
    quality_gate_reasons,
    retention_allowed,
    safe_http_url,
)

AS_OF = datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc)


class RefreshPipelineTests(unittest.TestCase):
    def test_zapply_parser_keeps_relevant_role_and_rejects_software_role(self) -> None:
        markdown = """
| **Sony** | Legal Intern | New York, NY | Jul 10 | [Apply](https://example.com/sony-legal) |
| **Example Tech** | Software Engineer Intern | Austin, TX | Jul 11 | [Apply](https://example.com/swe) |
"""
        jobs = parse_zapply_markdown(markdown, AS_OF)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Sony")
        self.assertEqual(jobs[0]["lane"], "Legal & Contracts")
        self.assertEqual(jobs[0]["country"], "United States")

    def test_canada_tracker_parser(self) -> None:
        markdown = """
| Title | Company | Type | Term | Location | Link |
| Contract Administration Intern | Northstar | Business / Legal | Fall 2026 | Toronto, ON | [Apply](https://example.com/contract) |
"""
        jobs = parse_canada_tracker_markdown(markdown, AS_OF)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Northstar")
        self.assertEqual(jobs[0]["country"], "Canada")
        self.assertIn("Contract", jobs[0]["title"])

    def test_internship_engine_filters_and_flags_us_authorization(self) -> None:
        payload = {
            "jobs": [
                {
                    "id": "business-1",
                    "company": "Northstar",
                    "title": "Business Operations Intern",
                    "location": "Boston, MA",
                    "url": "https://example.com/business",
                    "posted_at": "2026-07-11T12:00:00Z",
                    "category": "Business",
                    "skills": ["Excel", "strategy"],
                    "sponsorship": "No sponsorship",
                },
                {
                    "id": "software-1",
                    "company": "Northstar",
                    "title": "Software Engineering Intern",
                    "location": "Boston, MA",
                    "url": "https://example.com/software",
                    "posted_at": "2026-07-11T12:00:00Z",
                    "category": "Engineering",
                },
            ]
        }
        jobs = parse_internship_engine(payload, AS_OF)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["eligibility_status"], "check")
        self.assertIn("sponsor", jobs[0]["eligibility_notes"].lower())

    def test_curated_role_wins_deduplication(self) -> None:
        seed = {
            "id": "curated",
            "company": "Example Co",
            "title": "Legal Intern",
            "location": "Toronto, ON",
            "country": "Canada",
            "posted_date": "2026-07-01",
            "fit_score": 95,
            "source_type": "official",
            "official": True,
            "eligibility_status": "likely",
            "email": {"subject": "Tailored", "body": "Tailored body"},
        }
        dynamic = dict(seed)
        dynamic.update({"id": "dynamic", "fit_score": 99, "email": {"subject": "Generated", "body": "Generated body"}})
        result = merge_jobs([seed], [dynamic], AS_OF, 45)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "curated")
        self.assertEqual(result[0]["email"]["subject"], "Tailored")

    def test_email_uses_candidate_not_completed_degree(self) -> None:
        job = {
            "company": "Example Co",
            "title": "Compliance Intern",
            "lane": "Compliance & Governance",
            "interest": "the role connects controls, risk, and business operations.",
            "skills": ["Governance", "Privacy", "Excel"],
            "eligibility_status": "likely",
            "contact": "[Name]",
        }
        email = make_email(job)
        self.assertIn("BCom/JD candidate", email["body"])
        self.assertNotIn("completed a BCom/JD", email["body"].lower())
        self.assertIn("15-minute conversation", email["body"])


    def test_conflict_email_uses_first_person_without_resume_note(self) -> None:
        job = {
            "company": "Example Bank",
            "title": "Finance Summer Analyst",
            "lane": "Finance, Tax & Risk",
            "interest": "the role combines finance, governance, and regulatory reporting.",
            "skills": ["Excel", "Governance"],
            "eligibility_status": "conflict",
            "eligibility_label": "Class-year and sponsorship conflict",
            "eligibility_notes": "Katherine’s résumé lists a later graduation date.",
            "country": "United States",
            "contact": "[Name]",
        }
        email = make_email(job)
        self.assertIn("I recognize that", email["body"])
        self.assertNotIn("Katherine’s résumé", email["body"])
        self.assertIn("future recruiting cycle", email["body"])


    def test_job_bank_html_parser_captures_official_student_role(self) -> None:
        html = """
        <html><body>
          <article id="article-3619000">
            <a class="resultJobItem" href="/jobsearch/jobposting/3619000">
              <span class="noctitle">Compliance and Policy Co-op Student</span>
            </a>
            <ul>
              <li class="business">National Capital Commission</li>
              <li class="location">Gatineau (QC)</li>
              <li class="date">July 10, 2026</li>
              <li class="salary">$18.69 hourly</li>
              <li class="source">Jobs.gc.ca</li>
            </ul>
          </article>
        </body></html>
        """
        result = parse_job_bank_html(
            html, AS_OF, search_name="Federal government student roles", force_student=True
        )
        self.assertEqual(result.retrieved, 1)
        self.assertEqual(len(result.jobs), 1)
        job = result.jobs[0]
        self.assertEqual(job["company"], "National Capital Commission")
        self.assertEqual(job["country"], "Canada")
        self.assertEqual(job["source_type"], "official_government")
        self.assertTrue(job["official"])
        self.assertEqual(job["posted_date"], "2026-07-10")

    def test_job_bank_rss_parser_captures_business_student_role(self) -> None:
        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>Job Bank</title><item>
          <title>Business Analyst Intern | Northstar Financial</title>
          <link>https://www.jobbank.gc.ca/jobsearch/jobposting/999</link>
          <pubDate>Fri, 10 Jul 2026 12:00:00 GMT</pubDate>
          <description><![CDATA[Employer: Northstar Financial Location: Toronto (ON) Salary: $25.00 hourly Source: Job Bank]]></description>
        </item></channel></rss>"""
        result = parse_job_bank_rss(
            rss, AS_OF, search_name="Business student roles", force_student=True
        )
        self.assertEqual(result.retrieved, 1)
        self.assertEqual(len(result.jobs), 1)
        job = result.jobs[0]
        self.assertEqual(job["company"], "Northstar Financial")
        self.assertEqual(job["location"], "Toronto (ON)")
        self.assertEqual(job["country"], "Canada")
        self.assertEqual(job["posted_date"], "2026-07-10")

    def test_federal_student_index_extracts_specialized_inventory_deadline(self) -> None:
        html = """
        <html><body>
          <h1>Federal Student Work Experience Program</h1>
          <p>Ongoing Student Recruitment Inventory</p>
          <h3>Student Border Services Officer (Summer 2027)</h3>
          <ul>
            <li>Organization: Canada Border Services Agency</li>
            <li>Deadline to apply: September 17, 2026</li>
            <li>Availability: Full-time from end of April 2027 to early September 2027</li>
          </ul>
          <a href="https://example.gc.ca/apply-border">Apply</a>
        </body></html>
        """
        result = parse_federal_student_program_page(
            html,
            "https://www.canada.ca/fswep",
            AS_OF,
            base_program={
                "id": "fswep",
                "title": "Federal Student Work Experience Program",
                "url": "https://www.canada.ca/fswep",
                "required_phrases": ["Federal Student Work Experience Program", "Ongoing Student Recruitment Inventory"],
            },
        )
        titles = {job["title"]: job for job in result.jobs}
        self.assertIn("Federal Student Work Experience Program", titles)
        self.assertIn("Student Border Services Officer (Summer 2027)", titles)
        specialized = titles["Student Border Services Officer (Summer 2027)"]
        self.assertEqual(specialized["deadline"], "2026-09-17")
        self.assertEqual(specialized["url"], "https://example.gc.ca/apply-border")
        self.assertTrue(specialized["official"])

    def test_generic_rss_parser_keeps_relevant_municipal_student_role(self) -> None:
        rss = """<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>Policy and Finance Co-op Student | City of Northstar</title>
        <link>https://example.ca/jobs/policy-student</link>
        <pubDate>Fri, 10 Jul 2026 12:00:00 GMT</pubDate>
        <description><![CDATA[Location: Ottawa (ON) Support municipal policy research, financial analysis and council reporting.]]></description>
        </item></channel></rss>"""
        result = parse_generic_rss(rss, {
            "name": "Civic jobs", "family": "civicjobs",
            "default_country": "Canada", "official": False,
        }, AS_OF)
        self.assertEqual(result.retrieved, 1)
        self.assertEqual(len(result.jobs), 1)
        job = result.jobs[0]
        self.assertEqual(job["company"], "City of Northstar")
        self.assertEqual(job["country"], "Canada")
        self.assertFalse(job["official"])

    def test_successfactors_parser_filters_official_student_jobs(self) -> None:
        html = """<html><body><table>
          <tr class="data-row"><td><a class="jobTitle-link" href="/job/Toronto-Compliance-Co-op-ON/123/">Compliance Co-op Student</a></td>
          <td class="jobLocation">Toronto, ON</td><td class="jobDate">July 10, 2026</td></tr>
          <tr class="data-row"><td><a href="/job/Toronto-Senior-Engineer-ON/456/">Senior Software Engineer</a></td>
          <td class="jobLocation">Toronto, ON</td></tr>
        </table></body></html>"""
        result = parse_successfactors_search_page(
            html, "https://careers.example.ca/go/student/",
            {"id": "example-students", "company": "Example Bank",
             "default_country": "Canada", "force_student": True},
            AS_OF,
        )
        self.assertEqual(result.retrieved, 2)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["title"], "Compliance Co-op Student")
        self.assertEqual(result.jobs[0]["source_family"], "successfactors")
        self.assertTrue(result.jobs[0]["official"])

    def test_monitored_law_firm_page_applies_future_cycle_override(self) -> None:
        class FakeResponse:
            text = "<html><body><h1>Law Student Programs</h1><p>Summer and articling programs.</p></body></html>"
            def raise_for_status(self) -> None:
                return None

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        program = {
            "id": "example-law-students",
            "company": "Example LLP",
            "title": "Example Law Student Programs",
            "url": "https://example.com/students",
            "location": "Toronto, ON",
            "country": "Canada",
            "source_name": "Official law-firm student recruiting page",
            "source_type": "official_recruiting_program",
            "source_family": "law_firm_programs",
            "provenance": "official Canadian law-firm student recruiting page",
            "required_phrases": ["Law Student Programs"],
            "eligibility_status": "future",
            "eligibility_label": "Future law-student recruiting cycle",
            "eligibility_notes": "Monitor office-specific recruiting dates.",
            "work_auth": "Must be eligible to work in Canada",
            "class_year": "Office-specific law-school year",
            "lane": "Legal & Contracts",
        }
        result = fetch_monitored_program(FakeSession(), program, AS_OF, timeout=1)
        self.assertEqual(len(result.jobs), 1)
        job = result.jobs[0]
        self.assertEqual(job["eligibility_status"], "future")
        self.assertEqual(job["source_family"], "law_firm_programs")
        self.assertEqual(job["source_type"], "official_recruiting_program")
        self.assertIn("later recruiting cycle", job["email"]["body"])

    def test_source_registry_document_is_generated_and_publicly_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_source_registry(Path(tmp) / "SOURCES.md")
            content = registry.read_text(encoding="utf-8")
            self.assertIn("Government of Canada", content)
            self.assertIn("official employer ATS boards", content)
            output = build_public_site(Path(tmp) / "site")
            self.assertTrue((output / "SOURCES.md").exists())

    def test_source_registry_is_broad_and_includes_government_canada(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "scripts" / "sources.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(config["government_canada"]["job_bank_searches"]), 20)
        self.assertGreaterEqual(len(config["government_canada"]["programs"]), 8)
        self.assertGreaterEqual(len(config["government_us"]["usajobs_queries"]), 10)
        self.assertGreaterEqual(len(config["monitored_opportunity_pages"]), 8)
        self.assertGreaterEqual(len(config["rss_feeds"]), 1)
        ats_total = sum(
            len(config[key])
            for key in (
                "greenhouse_boards", "lever_boards", "ashby_boards",
                "smartrecruiters_boards", "workday_boards", "successfactors_boards",
            )
        )
        self.assertGreaterEqual(ats_total, 80)
        government_specs, parallel_specs = build_source_specs(config, AS_OF, timeout=1)
        self.assertGreaterEqual(len(government_specs), 25)
        self.assertGreaterEqual(len(parallel_specs), 100)
        self.assertTrue(any(spec.name.startswith("Job Bank:") for spec in government_specs))
        self.assertTrue(any(spec.family == "successfactors" for spec in parallel_specs))
        self.assertTrue(any(spec.family == "civicjobs" for spec in parallel_specs))
        self.assertTrue(any(spec.family == "law_firm_programs" for spec in parallel_specs))

    def test_coast_guard_summer_2027_program_is_explicitly_monitored(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "scripts" / "sources.json").read_text(encoding="utf-8"))
        programs = {item["id"]: item for item in config["government_canada"]["programs"]}
        self.assertIn("coast-guard-irb-2027", programs)
        program = programs["coast-guard-irb-2027"]
        self.assertEqual(program["deadline"], "2026-11-02")
        self.assertEqual(program["status"], "open_program")
        self.assertIn("2027 Inshore Rescue Boat student program", program["required_phrases"])

        payload = json.loads((root / "data" / "seed_jobs.json").read_text(encoding="utf-8"))
        matches = [job for job in payload["jobs"] if "Inshore Rescue Boat Student Program" in job.get("title", "")]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["deadline"], "2026-11-02")
        self.assertEqual(matches[0]["source_family"], "government_canada")

    def test_current_parliamentary_and_future_justice_programs_are_monitored(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "scripts" / "sources.json").read_text(encoding="utf-8"))
        programs = {item["id"]: item for item in config["government_canada"]["programs"]}

        guide = programs["library-parliament-guide-2026-27"]
        self.assertEqual(guide["deadline"], "2026-07-23")
        self.assertEqual(guide["status"], "open_program")
        self.assertEqual(guide["fit_score"], 93)

        justice = programs["justice-legal-excellence-program"]
        self.assertEqual(justice["eligibility_status"], "future")
        self.assertEqual(justice["lane"], "Legal & Contracts")

        senate = programs["senate-student-employment"]
        self.assertEqual(senate["status"], "program_inventory")
        self.assertIn("throughout the year", senate["required_phrases"][0])

        payload = json.loads((root / "data" / "seed_jobs.json").read_text(encoding="utf-8"))
        by_title = {job["title"]: job for job in payload["jobs"]}
        guide_job = by_title["Parliamentary Guide Program — Fall-Winter 2026–2027"]
        self.assertEqual(guide_job["deadline"], "2026-07-23")
        self.assertIn("bilingual communication", guide_job["interest"].lower())
        justice_job = by_title["Legal Excellence Program — Law Student and Articling Recruitment"]
        self.assertEqual(justice_job["eligibility_status"], "future")
        self.assertIn("later recruiting cycle", justice_job["email"]["body"])

    def test_quality_gate_rejects_seed_only_or_single_family_refresh(self) -> None:
        runs = [
            SourceRun(
                name="GC program: FSWEP", family="government_canada", tier="core",
                status="success", retrieved=1, accepted=1, pages=1, duration_ms=10
            )
        ]
        reasons, core, families, job_bank = quality_gate_reasons(
            runs, unique_dynamic_jobs=1, minimum_successful_core_sources=3,
            minimum_successful_source_families=3, minimum_dynamic_jobs=10,
            require_job_bank_success=True,
        )
        self.assertEqual(core, 1)
        self.assertEqual(families, ["government_canada"])
        self.assertFalse(job_bank)
        self.assertTrue(any("Job Bank" in reason for reason in reasons))
        self.assertTrue(any("unique live" in reason for reason in reasons))

    def test_quality_gate_accepts_multiple_live_source_families(self) -> None:
        runs = [
            SourceRun("Job Bank: student", "government_canada", "core", "success", 20, 8, 1, 10),
            SourceRun("GC program: FSWEP", "government_canada", "core", "success", 1, 1, 1, 10),
            SourceRun("Greenhouse: Example", "greenhouse", "standard", "success", 25, 2, 1, 10),
            SourceRun("The Muse", "the_muse", "standard", "empty", 10, 0, 1, 10),
        ]
        reasons, core, families, job_bank = quality_gate_reasons(
            runs, unique_dynamic_jobs=12, minimum_successful_core_sources=2,
            minimum_successful_source_families=3, minimum_dynamic_jobs=10,
            require_job_bank_success=True,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(core, 2)
        self.assertEqual(set(families), {"government_canada", "greenhouse", "the_muse"})
        self.assertTrue(job_bank)

    def test_public_site_builder_redacts_personal_contact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = build_public_site(Path(tmp) / "site")
            jobs_text = (output / "data" / "jobs.json").read_text(encoding="utf-8")
            outreach_text = (output / "outreach" / "cold_emails.md").read_text(encoding="utf-8")
            combined = jobs_text + outreach_text
            self.assertNotIn("kdu039@uottawa.ca", combined)
            self.assertNotIn("613-447-2562", combined)
            self.assertIn("[your email]", combined)
            self.assertIn("[your phone]", combined)

    def test_standalone_builder_inlines_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = build_standalone(Path(__file__).resolve().parents[1], Path(tmp) / "board.html")
            html = output.read_text(encoding="utf-8")
            self.assertIn("window.JOB_BOARD_DATA", html)
            self.assertIn("Generated by scripts/build_standalone.py", html)
            self.assertNotIn('src="data/jobs.js"', html)
            self.assertNotIn('href="styles.css"', html)

    def test_public_site_standalone_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = build_public_site(Path(tmp) / "site")
            html = (output / "standalone.html").read_text(encoding="utf-8")
            self.assertNotIn("kdu039@uottawa.ca", html)
            self.assertNotIn("613-447-2562", html)
            self.assertIn("[your email]", html)

    def test_url_and_country_safety(self) -> None:
        self.assertIsNone(safe_http_url("javascript:alert(1)"))
        self.assertEqual(infer_country("Montréal, QC"), "Canada")
        self.assertEqual(infer_country("New York, NY"), "United States")

    def test_expired_deadline_is_removed(self) -> None:
        job = {
            "deadline": "2026-07-01",
            "posted_date": "2026-06-20",
            "_curated": True,
            "source_type": "official",
        }
        self.assertFalse(retention_allowed(job, AS_OF, 45))

    def test_offline_output_is_valid_json_shape(self) -> None:
        path = Path(__file__).resolve().parents[1] / "data" / "jobs.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("metadata", payload)
        self.assertGreaterEqual(len(payload.get("jobs", [])), 1)
        self.assertTrue(all(job.get("email", {}).get("body") for job in payload["jobs"]))


if __name__ == "__main__":
    unittest.main()
