# Wikipedia AI Policy Shock: Presumptive Removal & Collateral Fact Auditor

An end-to-end data pipeline, agentic citation verification engine, and econometric
analysis framework investigating the empirical trade-offs of Wikipedia's
**"Presumptive Removal"** policy (`WP:LLMPROD` / `WP:AINB`).

## 📌 Executive Summary

Historically, Wikipedia adhered to an individualized "preservationist" ethos: edits
and citations could only be deleted if proven erroneous on their own merits. In
response to the influx of plausibly phrased but synthetic, hallucinated LLM text,
the community introduced a structural policy shock: **Presumptive Mass Removal**.
Under this guideline, once an editor is identified as an unvetted LLM contributor
on the **AI Noticeboard (`WP:AINB`)**, peer patrollers are empowered to
blanket-revert *all* of their historical edits without reading or verifying each
citation individually.

This project investigates the central socio-technical trade-off of algorithmic
platform governance:

> **Research Question:** *What is the collateral damage rate (Type I errors:
> accurate, verified citations deleted without cause) of presumptive mass-reverts
> compared to traditional behavioral dispute procedures, and how did it affect
> reviewer latency and knowledge retention?*

## 🏛️ System Architecture

The pipeline decouples into four discrete, testable architectural layers:



+---------------------------------------------------------------+
|              1. **Ingestion & Discovery**                    |
|   WP:AINB Scraper      WP:ANI Scraper      Admin Block Logs   |
|   (Treated Cohort)     (Control Cohort)    API Parser         |
+------------------------------+----------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|          2.  **Revision & Diff Extraction**                 |
|  usercontribs Fetcher --> Diff Comparator --> Wikitext AST    |
|  (Async HTTPX Pool)       (action=compare)     Parser/Splitter|
+------------------------------+----------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|            3. **Verification**                    |
|  SUBSYSTEM 3A: Re-Insertion   |  SUBSYSTEM 3B: Academic       |
|   Tracer                      |   Auditor                     |
|  - Scans T0+30d/90d revisions |  - Crossref & OpenAlex tools  |
|  - Jaccard & DOI match score  |  - LLM abstract grounding     |
+------------------------------+----------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|         4. **Econometrics & Analytics**                   |
|  DuckDB/Parquet --> Panel Assembler --> DiD / Event Study     |
|  Storage Layer      (Panel by User-Time)   Models             |
+---------------------------------------------------------------+


## 📂 Repository Structure

```text
wiki-ai-collateral-auditor/
├── config/
│   └── settings.yaml            # API headers, rate limits, model endpoints
├── data/
│   ├── raw/                     # Raw JSON responses from MediaWiki/Crossref
│   └── processed/               # Cleaned Parquet panel tables
├── src/
│   ├── ingestion/
│   │   ├── noticeboard_scraper.py   # Scrapes WP:AINB and WP:ANI archives
│   │   └── wiki_client.py           # Async MediaWiki Action API client with backoff
│   ├── extraction/
│   │   ├── diff_parser.py           # wikitextparser / mwparserfromhell extraction
│   │   └── reinsertion_tracer.py    # Longitudinal re-addition audit
│   ├── verification/
│   │   ├── academic_tools.py        # OpenAlex, Crossref & Open Library API connectors
│   │   └── grounding_agent.py       # LangChain/LLM audit & hallucination classifier
│   └── econometrics/
│       ├── panel_builder.py         # Assembles (User, Revision, Time) panel
│       └── estimation.py            # Fixed-effects DiD and Event-Study regressions
├── tests/
│   ├── test_diff_parser.py
│   └── test_academic_tools.py
├── notebooks/
│   └── exploratory_analysis.ipynb
├── requirements.txt
└── README.md
```

## ⚡ Quickstart & Installation

### 1. Prerequisites

- Python 3.11+
- DuckDB or SQLite for local analytics

### 2. Setup

```bash
git clone https://github.com/your-username/wiki-ai-collateral-auditor.git
cd wiki-ai-collateral-auditor
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Configuration

Edit `config/settings.yaml` to include your academic email (required by
OpenAlex/Crossref polite pools):

```yaml
wikipedia:
  user_agent: "WikiAICitationAuditor/1.0 (mailto:your-email@university.edu)"
  max_retries: 5
  rate_limit_per_second: 10

openalex:
  mailto: "your-email@university.edu"

llm:
  provider: "ollama"   # or "openai", "anthropic"
  model: "llama3"      # runs locally for $0 cost
```

### 4. Running the Pipeline

```bash
# Step 1: Ingest cases and extract cohorts
python -m src.ingestion.noticeboard_scraper

# Step 2: Extract revisions and diffs
python -m src.extraction.diff_parser

# Step 3: Run the re-insertion tracer and agentic academic verification
python -m src.extraction.reinsertion_tracer
python -m src.verification.grounding_agent

# Step 4: Estimate econometric models
python -m src.econometrics.estimation
```

## 🔬 Econometric Specification

To estimate whether presumptive removal increased collateral destruction of
factual content relative to standard administrative bans, we estimate a
difference-in-differences / event-study model with user and month-year fixed
effects, clustering standard errors at the user level. See
`src/econometrics/estimation.py` and the System Design Document §6 for the full
specification and primary metrics (Collateral Damage Ratio, Review Latency
Delta, Re-insertion Rate).

## 📊 Public APIs Used

- **MediaWiki Action API** — revision histories, user contributions, and
  side-by-side wikitext diffs.
- **OpenAlex API** — real-time DOI, title, and author matching across 250M+
  scholarly works (100k requests/day free).
- **Crossref API** — verification of academic journal metadata via the public
  polite pool.
- **Open Library Books API** — ISBN and title validation for non-journal book
  references.
