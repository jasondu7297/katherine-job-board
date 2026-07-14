# Katherine Du — Canada + U.S. Business/Law Internship Board

A personalized, filterable internship pipeline for roles where commerce, law, compliance, contracts, policy, finance, governance, operations, communications, and analytics overlap.

The profile is based on Katherine’s résumé: University of Ottawa BCom/JD **candidate**, expected April 2031, 9.5/10 CGPA, RBC Client Advisor, English/French/Mandarin, and experience with Excel, Power BI, Python, SQL, and Pandas.

## What changed in this version

The first version contained a 51-role reviewed seed list but did not complete a live refresh. This version replaces that narrow discovery layer with an auditable multi-source pipeline:

- 28 Government of Canada Job Bank searches, including broad student listings, federal roles, Canada Summer Jobs, and business/law keyword scans
- 20 Canadian federal, parliamentary, provincial, and public-sector student-program pages
- FSWEP and its specialized inventories, Research Affiliate Program, and Post-Secondary Co-op/Internship Program
- USAJOBS official API searches and U.S. federal student/legal program pages
- 100 configured official employer ATS boards across Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and SAP SuccessFactors
- Canadian municipal/public-sector discovery through the CivicJobs.ca RSS feed
- 12 official Canadian law-firm and pre-law recruiting pages
- Optional authorized APIs for CareerOneStop, Adzuna, and Jooble, plus The Muse
- Source-by-source diagnostics, strict publication gates, deduplication, eligibility warnings, résumé-fit ranking, and a tailored cold email for each board record

See [`SOURCES.md`](SOURCES.md) for the complete registry.

## Important distinction: checked-in snapshot vs. live refresh

The included `standalone.html` and checked-in data are a reviewed fallback snapshot. The packaging environment used to build this ZIP could not resolve external hosts, so it was not possible to execute the public endpoints here. The parser suite, source registry, offline build, privacy build, and quality-gate logic are tested locally; the **live** search begins only after the repository is deployed somewhere with internet access, such as GitHub Actions.

The crawler does not claim that every posting on the internet can be captured. It provides broad coverage across configured sources and makes missing credentials, failed sources, empty results, retrieval counts, and accepted counts visible.

## Open the board

For a one-file preview, open:

```text
standalone.html
```

For the modular site, run:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Daily refresh workflow

The scheduled workflow in `.github/workflows/daily-refresh.yml` runs at 6:15 a.m. America/Los_Angeles. It:

1. Validates that the broad source registry is present.
2. Searches Government of Canada sources sequentially and politely throttles requests.
3. Queries independent official ATS feeds and permitted APIs in parallel.
4. Screens for Canada/U.S. student opportunities relevant to business or law.
5. Labels official postings, official program pages, and discovery leads separately.
6. Deduplicates overlapping records and preserves the most authoritative version.
7. Scores résumé fit and flags class-year, co-op, citizenship, sponsorship, and work-authorization concerns.
8. Generates a role-specific cold email for every retained record.
9. Writes detailed source diagnostics.
10. Publishes only when the live-coverage quality gate passes.

### Publication quality gate

The workflow is configured to block publication unless all of the following are true:

- at least 3 core sources complete;
- at least 3 independent source families complete;
- at least 10 unique live opportunities survive screening; and
- at least one Government of Canada Job Bank search produces an accepted relevant result.

Curated fallback records do **not** count toward those thresholds. If the gate fails, the workflow uploads diagnostics and exits before replacing the last successful board.

## Deploy to GitHub Pages

1. Create a private GitHub repository and place this project at the repository root.
2. In **Settings → Pages**, select **GitHub Actions** as the source.
3. Open **Actions → Refresh and deploy internship board** and run the workflow once.
4. Review `data/refresh_report.json` and `data/source_coverage.csv` in the workflow artifact.
5. After a successful quality-gated refresh, GitHub Pages publishes the privacy-redacted site.

A private repository is strongly recommended because the working project contains personalized cold-email signatures.

## API credentials

Most Government of Canada sources and official employer ATS feeds require no keys. The following secrets extend coverage:

| Secret | Enables |
|---|---|
| `USAJOBS_API_KEY` | Official USAJOBS Search API |
| `USAJOBS_EMAIL` | Required USAJOBS requester email header |
| `CAREERONESTOP_TOKEN` | CareerOneStop Job Search API |
| `CAREERONESTOP_USER_ID` | CareerOneStop user identifier |
| `ADZUNA_APP_ID` | Adzuna Canada/U.S. searches |
| `ADZUNA_APP_KEY` | Adzuna authentication |
| `JOOBLE_API_KEY` | Jooble Canada/U.S. searches |

Add them under **Repository Settings → Secrets and variables → Actions**. Sources without credentials are logged as `skipped`; they are not falsely counted as successful.

## Run locally

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run a live refresh:

```bash
python scripts/refresh_jobs.py \
  --strict \
  --max-age-days 60 \
  --minimum-successful-core-sources 3 \
  --minimum-successful-source-families 3 \
  --minimum-dynamic-jobs 10 \
  --require-job-bank-success
```

Run a deterministic offline rebuild from the reviewed fallback records:

```bash
python scripts/refresh_jobs.py --offline --as-of 2026-07-13T16:00:00Z
```

Regenerate documentation and the standalone board:

```bash
python scripts/build_source_registry.py
python scripts/build_standalone.py
```

Run all checks:

```bash
python -m py_compile scripts/*.py
python -m unittest discover -s tests -v
node --check app.js
node tests/front_end_smoke.js
```

## Outputs

A successful refresh writes:

- `data/jobs.json` — complete structured board data
- `data/jobs.js` — browser-loadable copy
- `data/jobs.csv` — spreadsheet-friendly current opportunities
- `data/refresh_report.json` — run-level quality gate and source summary
- `data/source_coverage.csv` — one row per attempted source
- `outreach/cold_emails.md` — tailored outreach library
- `standalone.html` — self-contained board

The source diagnostics distinguish:

- `success` — source responded and produced at least one accepted opportunity;
- `empty` — source responded but no relevant opportunity survived screening;
- `skipped` — credentials or an optional prerequisite were unavailable;
- `error` — request or parser failed.

## Source policy and provenance

The project favors official government pages and official employer ATS endpoints. Aggregator and community-feed records are marked as discovery leads and should be verified on the employer’s own posting before applying.

It deliberately does not bypass anti-bot controls or scrape sites where automated collection is prohibited. LinkedIn and Indeed are used only for manual discovery/verification. GovernmentJobs/NEOGOV is not scraped; municipal and state jobs should enter through an authorized API, public RSS feed, or an agency’s official career feed.

## Eligibility logic

The board retains useful roles even when Katherine may not qualify in the current cycle, but it surfaces the concern instead of implying eligibility. The screening flags include:

- U.S. authorization or no-sponsorship language;
- U.S. citizenship requirements;
- ABA-accredited-school language;
- 1L, 2L, 3L, or office-specific law-student timing;
- Canadian co-op registration and return-to-studies conditions;
- required years completed; and
- future-cycle law-firm recruiting programs.

These labels are a research aid, not legal or immigration advice. Always read the official posting before applying.

## Cold-email workflow

Each record includes an editable email that:

- accurately describes Katherine as a BCom/JD candidate;
- connects RBC, client service, regulated-data handling, analytical tools, and multilingual communication to the role;
- explains the role-specific interest;
- handles eligibility uncertainty without overstating qualifications; and
- asks for a focused 15-minute conversation.

Before sending, replace `[Name]`, confirm the recipient’s relationship to the team, add one concrete organization-specific detail, and still apply through the formal channel.

## Privacy-safe public build

The local project contains Katherine’s email, phone number, and LinkedIn signature. Build a redacted public site with:

```bash
python scripts/build_public_site.py
```

The public build replaces personal contact details with placeholders and generates `_site/standalone.html`. The original résumé PDF is never copied into the website.

Using a public source repository would still expose committed personalized source files. Keep the repository private or remove personal details before publishing the repository itself.
