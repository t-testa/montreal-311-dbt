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
| staging | view | 1:1 with source, typed and cleaned |
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

_TODO: design, prompts, evaluation of proposed tests/docs._

## Data source

[Requêtes 311, Ville de Montréal open data portal](https://donnees.montreal.ca/dataset/requete-311).
