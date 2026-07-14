# Internship Board Source Registry

This file is generated from `scripts/sources.json`. It describes what the daily refresh attempts, which sources are official, which require credentials, and which sites are deliberately not scraped.

## Coverage at a glance

- **28** Government of Canada Job Bank searches
- **20** Canadian federal, parliamentary, provincial, and public-sector program pages
- **14** USAJOBS searches and **4** U.S. federal program pages
- **12** Canadian law-firm and pre-law recruiting pages
- **100** official employer ATS boards across Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and SAP SuccessFactors
- **1** public-sector RSS feed(s) and **3** public internship feeds
- **42** optional credentialed search queries, plus The Muse when enabled

Runtime results are written to `data/refresh_report.json` and `data/source_coverage.csv`. Every configured source is logged as `success`, `empty`, `skipped`, or `error`, with retrieved and accepted counts. Seed records are excluded from the live-coverage quality gate.

## Government of Canada — Job Bank searches

| Search | Tier | Filters / query | Age window | Fallback pages |
| --- | --- | --- | --- | --- |
| Federal government student and internship roles | core | fet=3, searchstring=student | 90 days | 5 |
| All Canadian student jobs — broad scan | core | fjsf=1, student flag | 45 days | 5 |
| Business and operations student roles | core | fjsf=1, searchstring=business, student flag | 60 days | 3 |
| Finance and banking student roles | core | fjsf=1, searchstring=finance, student flag | 60 days | 3 |
| Accounting and tax student roles | standard | fjsf=1, searchstring=accounting, student flag | 60 days | 3 |
| Legal and law student roles | core | fjsf=1, searchstring=legal, student flag | 60 days | 3 |
| Compliance and regulatory student roles | core | fjsf=1, searchstring=compliance, student flag | 60 days | 3 |
| Policy and public administration student roles | core | fjsf=1, searchstring=policy, student flag | 60 days | 3 |
| Audit and controls student roles | standard | fjsf=1, searchstring=audit, student flag | 60 days | 3 |
| Communications and public affairs student roles | standard | fjsf=1, searchstring=communications, student flag | 60 days | 3 |
| Marketing and commercial student roles | standard | fjsf=1, searchstring=marketing, student flag | 60 days | 3 |
| Data and business analytics student roles | standard | fjsf=1, searchstring=data analyst, student flag | 60 days | 3 |
| Human resources student roles | standard | fjsf=1, searchstring=human resources, student flag | 60 days | 3 |
| Procurement and contracts student roles | standard | fjsf=1, searchstring=procurement, student flag | 60 days | 3 |
| Administration and program support student roles | standard | fjsf=1, searchstring=administrative, student flag | 60 days | 3 |
| Insurance and risk student roles | standard | fjsf=1, searchstring=insurance, student flag | 60 days | 3 |
| Federal government internships | core | fet=3, searchstring=intern | 90 days | 4 |
| Federal government co-op roles | core | fet=3, searchstring=co-op | 90 days | 4 |
| Federal government legal roles for students | standard | fet=3, searchstring=legal | 90 days | 4 |
| Federal government policy roles for students | standard | fet=3, searchstring=policy | 90 days | 4 |
| Federal government finance roles for students | standard | fet=3, searchstring=finance | 90 days | 4 |
| Federal government compliance roles for students | standard | fet=3, searchstring=compliance | 90 days | 4 |
| Federal government communications roles for students | standard | fet=3, searchstring=communications | 90 days | 4 |
| Canada Summer Jobs — business | standard | fsrc=21, searchstring=business, student flag | 60 days | 3 |
| Canada Summer Jobs — administration | standard | fsrc=21, searchstring=administrative, student flag | 60 days | 3 |
| Canada Summer Jobs — finance | standard | fsrc=21, searchstring=finance, student flag | 60 days | 3 |
| Canada Summer Jobs — legal | standard | fsrc=21, searchstring=legal, student flag | 60 days | 3 |
| Canada Summer Jobs — marketing and communications | standard | fsrc=21, searchstring=marketing, student flag | 60 days | 3 |

## Canadian public-sector and student programs

| Program | Organization | Scope | Tier |
| --- | --- | --- | --- |
| [Federal Student Work Experience Program (FSWEP)](https://www.canada.ca/en/public-service-commission/jobs/services/recruitment/students/federal-student-work-program.html) | Government of Canada | Across Canada | core |
| [Research Affiliate Program](https://www.canada.ca/en/public-service-commission/jobs/services/recruitment/students/research-affiliate-program.html) | Government of Canada | Across Canada | core |
| [Post-Secondary Co-op/Internship Program](https://www.canada.ca/en/public-service-commission/jobs/services/recruitment/students/coop-internship.html) | Government of Canada | Across Canada | core |
| [Government of Canada Youth and Student Employment Hub](https://www.canada.ca/en/services/jobs/youth.html) | Government of Canada | Across Canada | core |
| [Student Border Services Officer — Summer 2027](https://www.cbsa-asfc.gc.ca/job-emploi/student-etudiant/sbso-aesf-eng.html) | Canada Border Services Agency | Various locations in Canada | core |
| [Parks Canada Student and Youth Employment](https://parks.canada.ca/agence-agency/emplois-jobs/etudiants-students) | Parks Canada | Across Canada | standard |
| [CRA Student and Graduate Hiring](https://www.canada.ca/en/revenue-agency/corporate/careers-cra/browse-job-types/student-graduate-hiring.html) | Canada Revenue Agency | Across Canada | standard |
| [NRC Student Employment Program](https://nrc.canada.ca/en/corporate/careers/nrc-student-employment-program) | National Research Council Canada | Across Canada | standard |
| [CFIA Student Opportunities](https://inspection.canada.ca/en/about-cfia/job-opportunities/how-apply/students) | Canadian Food Inspection Agency | Across Canada | standard |
| [Student Work Placement Program](https://www.canada.ca/en/employment-social-development/services/student-work-placements-wage-subsidies.html) | Employment and Social Development Canada | Across Canada | standard |
| [Bank of Canada Students and Recent Graduates](https://www.bankofcanada.ca/careers/students-and-recent-graduates/) | Bank of Canada | Ottawa and other Canadian locations | standard |
| [House of Commons Student Employment Program](https://www.ourcommons.ca/en/employment/students) | House of Commons of Canada | Ottawa, ON | standard |
| [Parliamentary Guide Program — Fall-Winter 2026–2027](https://lop.parl.ca/sites/jobs/default/en_CA/guides) | Library of Parliament | Ottawa, ON | core |
| [Senate of Canada Student Employment Program](https://sencanada.ca/en/about/careers/student-employment/) | Senate of Canada | Ottawa, ON | core |
| [Legal Excellence Program — Law Student and Articling Recruitment](https://www.justice.gc.ca/eng/abt-apd/recru/lep-pea/index.html) | Department of Justice Canada | Multiple offices across Canada | core |
| [BC Public Service Co-op Employment Program](https://www2.gov.bc.ca/gov/content/careers-myhr/job-seekers/internship-co-op-opportunities/co-op) | Government of British Columbia | British Columbia | standard |
| [BC Public Service Student Employment Program](https://www2.gov.bc.ca/gov/content/careers-myhr/job-seekers/internship-co-op-opportunities/student-employment-program) | Government of British Columbia | British Columbia | standard |
| [Alberta Public Service Student Opportunities and Internships](https://www.alberta.ca/find-student-opportunities-and-internships) | Government of Alberta | Alberta | standard |
| [Quebec Public Service Student Jobs and Internships](https://www.quebec.ca/gouvernement/travailler-gouvernement/emplois-fonction-publique/emplois-etudiants-stages) | Government of Quebec | Quebec | standard |
| [Inshore Rescue Boat Student Program — Summer 2027](https://www.canada.ca/en/canadian-coast-guard/services/search-rescue/inshore-rescue-boat-service/apply-student-program-how.html) | Canadian Coast Guard | Coastal locations across Canada | core |

## Canadian law-firm and pre-law recruiting pages

| Program page | Organization | Scope | Board treatment |
| --- | --- | --- | --- |
| [Blakes Pre-Law Internship Opportunities](https://www.joinblakes.com/undergrad-students/) | Blake, Cassels & Graydon LLP | Canada — office and program dependent | requirements check |
| [Blakes Law Student Programs](https://www.joinblakes.com/law-students/) | Blake, Cassels & Graydon LLP | Toronto, Ottawa, Montréal, Calgary and Vancouver | future watchlist |
| [Osler Law Student Programs](https://www.osler.com/en/law-students/) | Osler, Hoskin & Harcourt LLP | Toronto, Ottawa, Montréal, Calgary and Vancouver | future watchlist |
| [Torys Student Program](https://www.torys.com/careers/student-program) | Torys LLP | Toronto and Calgary | future watchlist |
| [McCarthy Tétrault Student Programs](https://www.mccarthy.ca/en/careers/students) | McCarthy Tétrault LLP | Vancouver, Calgary, Toronto, Ottawa, Montréal and Québec City | future watchlist |
| [Fasken Law Student Programs](https://www.fasken.com/en/careers) | Fasken Martineau DuMoulin LLP | Canada — multiple offices | future watchlist |
| [Dentons Canada Summer and Articling Programs](https://students.dentons.com/opportunities-around-the-globe/canada/) | Dentons Canada LLP | Calgary, Edmonton, Montréal, Ottawa, Toronto and Vancouver | future watchlist |
| [Norton Rose Fulbright Canada Student Programs](https://www.nortonrosefulbright.com/en-ca/careers/students/programs) | Norton Rose Fulbright Canada LLP | Calgary, Montréal, Ottawa, Québec City, Toronto and Vancouver | future watchlist |
| [Bennett Jones Law Student Programs](https://www.bennettjones.com/Student-Recruiting) | Bennett Jones LLP | Calgary, Edmonton, Ottawa, Toronto and Vancouver | future watchlist |
| [BLG Student Programs](https://www.blg.com/en/student-programs) | Borden Ladner Gervais LLP | Calgary, Montréal, Ottawa, Toronto and Vancouver | future watchlist |
| [Stikeman Elliott Law Student Programs](https://stikeman.com/en-CA/careers/students/toronto) | Stikeman Elliott LLP | Toronto, ON and other Canadian offices | future watchlist |
| [Gowling WLG Canada Student Programs](https://gowlingwlg.com/en/careers/canada/students) | Gowling WLG (Canada) LLP | Canada — multiple offices | future watchlist |

## United States federal sources

| Source | Organization | Scope |
| --- | --- | --- |
| [Pathways Internship Program](https://www.opm.gov/policy-data-oversight/hiring-information/students-recent-graduates/) | U.S. Federal Government | United States |
| [Volunteer Legal Internships](https://www.justice.gov/legal-careers/volunteer-legal-internships) | U.S. Department of Justice | United States |
| [Bureau of Competition Legal Internships](https://www.ftc.gov/about-ftc/bureaus-offices/bureau-competition/careers-bureau-competition/legal-internships) | Federal Trade Commission | Washington, DC and other U.S. locations |
| [Student Internship Program](https://careers.state.gov/interns-fellows/student-internship-program/) | U.S. Department of State | United States and overseas |

**USAJOBS keyword searches:** `student trainee`, `pathways intern`, `legal intern`, `law clerk intern`, `business intern`, `finance intern`, `policy intern`, `compliance intern`, `audit intern`, `contract specialist intern`, `program analyst intern`, `public affairs intern`, `human resources intern`, `management intern`.

USAJOBS is an official API source but requires `USAJOBS_API_KEY` and `USAJOBS_EMAIL`. Without those secrets, it is recorded as skipped rather than silently treated as successful.

## Official employer applicant-tracking systems

| Platform | Configured boards | Employers |
| --- | --- | --- |
| Greenhouse | 34 | Connor, Clark & Lunn Financial Group, Cloudflare, Walleye Capital, Wealthsimple, Clio, PointClickCare, StackAdapt, ApplyBoard, Cohere, Stripe, Plaid, Coinbase, Robinhood, Affirm, Airbnb, DoorDash, Lyft, Asana, Dropbox, Pinterest, Reddit, MongoDB, Okta, CrowdStrike, Datadog, Instacart, The Trade Desk, Two Sigma, Point72, Flow Traders, Rocket Lab, Gotion, Eulerity, Per Scholas |
| Lever | 15 | Canva, Veeva Systems, Highspot, Gusto, SeatGeek, Anduril Industries, Chainalysis, Palantir, Yelp, Upwork, Samsara, Hootsuite, ecobee, FreshBooks, Bench Accounting |
| Ashby | 16 | 1Password, Ramp, Notion, Anthropic, OpenAI, Deel, Remote, Replit, Linear, Perplexity, Harvey, Etched, Watershed, Intercom, Glean, Brex |
| SmartRecruiters | 8 | Bosch, Visa, Experian, ServiceNow, Sia Partners, Publicis Groupe, NBCUniversal, Ubisoft |
| Workday | 25 | Sony, Interac, Maple Leaf Sports & Entertainment, CIBC Campus, HARMAN International, S&P Global, Comcast, ABB, TD Bank, BMO Campus, Manulife, Sun Life Campus, Intact Financial, OMERS, Ontario Teachers' Pension Plan, PSP Investments, CPP Investments, TMX Group, Thomson Reuters, McKesson, OLG Student Opportunities, Brookfield, 407 ETR, RBC Early Talent, Desjardins |
| SAP SuccessFactors | 2 | Scotiabank, Bank of Canada |

These are official employer career endpoints. A board can still move, rename its tenant, block traffic, or return no internships; the refresh report makes those outcomes visible.

## Public feeds and discovery APIs

| Source | Type | Coverage | Credentials |
| --- | --- | --- | --- |
| zapply_markdown | Public feed | https://raw.githubusercontent.com/zapplyjobs/Internships-2027/main/README.md | No |
| internship_engine_json | Public feed | https://zshah101.github.io/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/api/jobs.json | No |
| canada_tracker_markdown | Public feed | https://raw.githubusercontent.com/hanzili/canada_sde_intern_position/main/README.md | No |
| CivicJobs.ca — Canadian municipal and public-sector jobs | RSS/Atom | https://www.civicjobs.ca/rss/careers | No |
| CareerOneStop | Authorized U.S. job API | Business/law internship keyword searches | CAREERONESTOP_TOKEN + CAREERONESTOP_USER_ID |
| Adzuna | Authorized search API | Canada and U.S. keyword searches | ADZUNA_APP_ID + ADZUNA_APP_KEY |
| Jooble | Authorized search API | Canada and U.S. keyword searches | JOOBLE_API_KEY |
| The Muse | Public API | Internship-level Canada/U.S. search | No |

Aggregator and community-feed records are labelled as **discovery leads** and should be verified on the employer’s own page before applying.

## Deliberately not scraped

| Site / category | Reason | Alternative |
| --- | --- | --- |
| LinkedIn and Indeed | Used only for human discovery/verification; this project does not bypass anti-bot controls or scrape pages contrary to site terms. | Official employer feeds, authorized APIs, or manual verification |
| GovernmentJobs / NEOGOV | Not scraped because the public terms prohibit automated scraping. Municipal and state roles should be added through an authorized API or employer-specific official feed. | Official employer feeds, authorized APIs, or manual verification |

## Publication safeguards

The scheduled workflow requires multiple independent live source families, multiple successful core sources, at least one accepted Job Bank result, and a minimum number of unique live opportunities. When those checks fail, the workflow writes diagnostics and stops before replacing the last good board.

No crawler can guarantee every job posted on the internet. This registry makes the configured scope and its limitations auditable instead of presenting a seed list as a complete market scan.
