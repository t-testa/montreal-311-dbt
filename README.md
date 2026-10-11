# Montreal 311: dbt analytics engineering project

An analytics engineering project built on the City of Montréal's open 311 service-request data
(2022 to date), using **dbt-core + DuckDB**. A later phase adds an AI agent that proposes dbt tests
and documentation for the models.

## Goal

Turn the raw, all-text 311 export (~3.2M rows, French column names, mixed quality) into a tested,
documented dimensional model that answers questions such as: which services generate the most
requests, how long do requests take to close, and how does that vary by borough and channel.

## Architecture

```
 Montréal open data CSV
          │  ingestion/load_311.py
          ▼
   raw.requetes311            all columns as text, plus raw._load_log
          │  dbt
          ▼
   staging/                   rename (FR→EN), cast types, trim/normalize, dedupe
          ▼
   intermediate/              business logic: request lifecycle, borough reconciliation
          ▼
   marts/                     facts and dimensions for analysis
```

| Layer | Materialization | Purpose |
|-------|-----------------|---------|
| raw | table (Python load) | Untouched source data, all `VARCHAR` |
| staging | table | 1:1 with source, typed and cleaned (table because hashing 3.2M rows is costly to recompute) |
| intermediate | view | Joins and derived business logic |
| marts | table | Consumption-ready models |

## Repository layout

```
ingestion/        download + load script
profiling/        raw data profiler
docs/             profiling.md (generated column-by-column profile)
montreal_311/     dbt project (models/staging, intermediate, marts)
data/             gitignored: raw CSV and the DuckDB file
```

## How to run

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. Download the CSV (~840 MB) and load raw.requetes311 into data/montreal_311.duckdb
python ingestion/load_311.py

# 2. Profile the raw table -> docs/profiling.md
python profiling/profile_raw.py

# 3. dbt (models arrive in phase 2)
cd montreal_311
dbt debug --profiles-dir .
dbt build --profiles-dir .
```

## Data profile

See [docs/profiling.md](docs/profiling.md) for every column's meaning, null rate, cardinality and
the data quality issues found in the raw data.

## Results

_TODO: staging/mart row counts, test coverage, example insights._

## dbt tests and documentation

_TODO: summary of tests and docs._

## AI agent for tests and docs

`ai/propose_tests.py` proposes dbt tests for a model, proves them against the data, and lets a
human decide.

```powershell
copy .env.example .env        # then add ANTHROPIC_API_KEY
python ai/propose_tests.py fct_service_requests --propose-only   # propose + validate, no writes
python ai/propose_tests.py fct_service_requests --review         # interactive y/n review
```

1. **Profile**: per-column aggregates from DuckDB (type, null %, distinct count, min/max,
   string lengths, top 10 values for low-cardinality columns).
2. **Propose**: the LLM (`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`) receives the model SQL,
   its yml, the profile and the existing tests, and returns structured JSON: column, test type
   (`not_null`, `unique`, `accepted_values`, `relationships` or a singular SQL test), config,
   rationale and evidence.
3. **Validate**: every proposal runs as a real `dbt test` in a throwaway dbt project, so each
   one has a pass/fail result and failing row count before you see it.
4. **Review**: you approve (`y`), reject (`n`) or, for failing tests, add as `severity: warn`
   (`w`). Approved generic tests are written into the model's yml; approved singular tests are
   written to `montreal_311/tests/`. SQL models are never edited.
5. **Log**: every proposal, result and decision is appended to `ai/logs/decisions.jsonl`, and each
   run's full proposal list and exact LLM payload is saved in `ai/logs/proposals/`. These are
   committed as evaluation evidence.

**Privacy:** only aggregate statistics are sent to the LLM, never raw rows. The data contains
street addresses and postal codes, so columns with sensitive-looking names and all
high-cardinality columns never get value lists. The exact payload for each run is saved so it
can be audited.

## Data source

[Requêtes 311, Ville de Montréal open data portal](https://donnees.montreal.ca/dataset/requete-311).
