"""AI test writer: propose dbt tests for a model, validate them against the data, review them.

Usage:
    python ai/propose_tests.py <model_name>                  # propose + validate + review
    python ai/propose_tests.py <model_name> --propose-only   # stop before review (saves a proposals file)
    python ai/propose_tests.py <model_name> --review [FILE]  # review a saved proposals file (default: latest)

PRIVACY: this script sends the LLM ONLY aggregate statistics (types, null %, distinct counts,
min/max, string lengths, and top values for low-cardinality columns) plus the model SQL and
yml. It NEVER sends raw rows. The data contains street addresses and postal codes, so columns
whose names look sensitive never get value lists (see NO_VALUES_PATTERN), and high-cardinality
columns never get value lists either. The exact payload is saved with every run in
ai/logs/proposals/ so it can be audited.

Safety rules:
    * SQL models are never edited. Approved generic tests go into the model's yml; approved
      singular tests are written as new files under montreal_311/tests/.
    * Validation runs in a throwaway dbt project (stub models pointing at the real tables), so
      the real project files are untouched until you approve a test.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = ROOT / "montreal_311"
DB_PATH = Path(os.environ.get("MONTREAL_311_DB", ROOT / "data" / "montreal_311.duckdb"))
LOG_DIR = ROOT / "ai" / "logs"
PROPOSALS_DIR = LOG_DIR / "proposals"
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"

DEFAULT_LLM = "claude-sonnet-5-5"
LOW_CARDINALITY_MAX = 50  # columns with <= this many distinct values get a top-values list
TOP_N = 10
CANDIDATE_KEY_MIN_DISTINCT_RATIO = 0.9  # distinct/non-null at or above this => candidate key
NO_VALUES_PATTERN = re.compile(r"street|postal|address|phone|email|latitude|longitude|mtm_|_id$|_sk$", re.I)
PROMPT_VERSIONS = ("v1", "v2", "v3")
ALLOWED_TYPES = ["not_null", "unique", "accepted_values", "relationships", "singular_sql"]
NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
                 "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL")
FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|export|import|install|load|call|set)\b",
    re.I,
)


# --------------------------------------------------------------------------- locating files
def find_model(name: str) -> tuple[Path, Path]:
    """Return (sql_path, yml_path) for a model. The yml is the file that declares the model."""
    sql = next(iter((DBT_DIR / "models").rglob(f"{name}.sql")), None)
    if sql is None:
        sys.exit(f"Model '{name}' not found under {DBT_DIR / 'models'}")
    for yml in (DBT_DIR / "models").rglob("*.yml"):
        doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        if any(m.get("name") == name for m in doc.get("models", [])):
            return sql, yml
    sys.exit(f"No yml file declares model '{name}'")


def all_models() -> dict[str, list[str]]:
    """Every model in the project with its documented column names (for relationships)."""
    out = {}
    for yml in (DBT_DIR / "models").rglob("*.yml"):
        doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        for m in doc.get("models", []):
            out[m["name"]] = [c["name"] for c in m.get("columns", [])]
    for seed_yml in (DBT_DIR / "seeds").glob("*.yml"):
        doc = yaml.safe_load(seed_yml.read_text(encoding="utf-8")) or {}
        for s in doc.get("seeds", []):
            out[s["name"]] = [c["name"] for c in s.get("columns", [])]
    return out


def existing_tests(model: str, yml: Path) -> list[dict]:
    """Tests already on the model (column-level and model-level) plus singular tests that ref it."""
    doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    entry = next(m for m in doc["models"] if m["name"] == model)
    found = []

    def norm(t, column):
        if isinstance(t, str):
            return {"column": column, "test": t}
        (k, v), = t.items()
        return {"column": column, "test": k, "config": v}

    for t in entry.get("data_tests", []) or entry.get("tests", []) or []:
        found.append(norm(t, None))
    for col in entry.get("columns", []):
        for t in col.get("data_tests", []) or col.get("tests", []) or []:
            found.append(norm(t, col["name"]))
    for f in (DBT_DIR / "tests").rglob("*.sql"):
        if re.search(rf"ref\(\s*['\"]{model}['\"]\s*\)", f.read_text(encoding="utf-8")):
            found.append({"column": None, "test": "singular_sql", "file": f.name})
    return found


# --------------------------------------------------------------------------- profiling
def profile_model(model: str, extended: bool = False) -> dict:
    """Aggregate-only profile of every column. No raw rows leave this function.

    extended=True (prompt v3) adds (a) duplicate-value counts on candidate key columns and
    (b) complete value lists for columns with up to LOW_CARDINALITY_MAX distinct values,
    instead of only the top TOP_N. Both are still aggregates.
    """
    value_limit = LOW_CARDINALITY_MAX if extended else TOP_N
    con = duckdb.connect(str(DB_PATH), read_only=True)
    table = f"main.{model}"
    total = con.execute(f"select count(*) from {table}").fetchone()[0]
    cols = con.execute(f"describe {table}").fetchall()
    profile = {"row_count": total, "columns": {}}
    for name, dtype, *_ in cols:
        q = f'"{name}"'
        nn, distinct = con.execute(f"select count({q}), count(distinct {q}) from {table}").fetchone()
        p = {
            "type": dtype,
            "null_pct": round(100 * (total - nn) / total, 3) if total else None,
            "distinct_count": distinct,
            "all_distinct": nn > 0 and distinct == nn,
        }
        base = dtype.split("(")[0]
        if base in NUMERIC_TYPES or base in ("DATE", "TIMESTAMP"):
            lo, hi = con.execute(f"select min({q}), max({q}) from {table}").fetchone()
            p["min"], p["max"] = (str(lo) if lo is not None else None), (str(hi) if hi is not None else None)
        if base == "VARCHAR":
            lo, hi = con.execute(f"select min(length({q})), max(length({q})) from {table}").fetchone()
            p["min_length"], p["max_length"] = lo, hi
        if 0 < distinct <= LOW_CARDINALITY_MAX and not NO_VALUES_PATTERN.search(name):
            rows = con.execute(
                f"select {q}, count(*) from {table} where {q} is not null "
                f"group by 1 order by 2 desc, 1 limit {value_limit}"
            ).fetchall()
            p["top_values"] = [{"value": str(v) if not isinstance(v, bool) else v, "count": c} for v, c in rows]
            # True only when the list above is the complete set of non-null values.
            p["values_complete"] = distinct <= value_limit
        if extended and nn > 0 and (
            re.search(r"(_id|_sk|_key)$|^id$", name) or distinct / nn >= CANDIDATE_KEY_MIN_DISTINCT_RATIO
        ):
            # Candidate key column: how many values occur more than once, and in how many rows?
            dv, dr = con.execute(
                f"select count(*), coalesce(sum(n), 0) from "
                f"(select count(*) n from {table} where {q} is not null group by {q} having count(*) > 1)"
            ).fetchone()
            p["duplicate_values"] = dv  # distinct values that appear more than once
            p["rows_in_duplicate_values"] = int(dr)
        profile["columns"][name] = p
    con.close()
    return profile


# --------------------------------------------------------------------------- LLM
SYSTEM_PROMPT = """You are a careful analytics engineer proposing dbt data tests for ONE model.

You receive the model SQL, its yml (with column descriptions), aggregate-only column
profiles, the tests that already exist, and the other models you may reference.

Rules:
- Allowed test types: not_null, unique, accepted_values, relationships, singular_sql. Nothing else.
- Ground every proposal in evidence: cite the specific statistic (e.g. "null_pct=0.0",
  "all_distinct=true", "min=0") and/or the yml description or SQL that implies the rule.
- accepted_values: only for columns whose profile has values_complete=true; list EXACTLY those
  values (same spelling, accents and case). Never invent values.
- relationships: config {"to": "ref('<model>')", "field": "<column>"}, only to models and
  columns listed in available_models.
- singular_sql: config {"slug": "short_snake_case_name", "sql": "..."}. The SQL must be a
  single SELECT (or WITH ... SELECT) that returns the rows that VIOLATE the rule, reads the model via
  {{ ref('<this model>') }}, and has no semicolon. Use these for cross-column invariants.
- Do not duplicate tests that already exist. Prefer a focused list of valuable tests over an
  exhaustive one (aim for 8-20). Skip columns where a test would be trivial or meaningless.
- You may propose a rule that the statistics cannot fully confirm if the descriptions or SQL
  clearly imply it; every proposal is validated against the real data before a human sees it.
- Set "column" to null for singular_sql tests that span columns.
Return your answer ONLY by calling the submit_proposals tool."""

_BASE_PROPOSAL_TOOL = {
    "name": "submit_proposals",
    "description": "Submit the proposed dbt tests.",
    "input_schema": {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": ["string", "null"]},
                        "test_type": {"type": "string", "enum": ALLOWED_TYPES},
                        "config": {"type": "object", "description": "Test arguments; {} for not_null/unique."},
                        "rationale": {"type": "string"},
                        "evidence": {"type": "string", "description": "Which statistic/description supports this."},
                    },
                    "required": ["column", "test_type", "config", "rationale", "evidence"],
                },
            }
        },
        "required": ["proposals"],
    },
}


# v2 adds ONE generic rule (no column names): flag columns with a small non-zero count are
# known-defect indicators and deserve a warn-severity monitoring test. v3 keeps this prompt
# unchanged and only enriches the profile (see profile_model(extended=True)).
MONITORING_RULE = """- Boolean flag columns whose true-count (or false-count) is small but non-zero relative to the row
  count are known-defect indicators: the data producer is marking rows it knows are problematic.
  For each such column, propose a monitoring test with "severity": "warn" that fails when flagged
  rows exist, so the defect is tracked instead of silently ignored."""
_RETURN = "Return your answer ONLY by calling"
SYSTEM_PROMPTS = {
    "v1": SYSTEM_PROMPT,
    "v2": SYSTEM_PROMPT.replace(_RETURN, MONITORING_RULE + "\n" + _RETURN),
}
SYSTEM_PROMPTS["v3"] = SYSTEM_PROMPTS["v2"]


def proposal_tool(version: str) -> dict:
    """v2+ lets the model set an optional per-proposal severity."""
    import copy

    tool = copy.deepcopy(_BASE_PROPOSAL_TOOL)
    if version != "v1":
        tool["input_schema"]["properties"]["proposals"]["items"]["properties"]["severity"] = {
            "type": "string", "enum": ["error", "warn"],
            "description": "Optional. 'warn' for monitoring tests that are expected to fail on known defects.",
        }
    return tool


def call_llm(model: str, sql: str, yml_text: str, profile: dict, existing: list, models: dict,
             prompt_version: str = "v2") -> tuple[dict, dict]:
    import anthropic  # imported lazily so --review works without an API key

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    llm_model = os.getenv("ANTHROPIC_MODEL", DEFAULT_LLM)
    payload = {
        "model_name": model,
        "model_sql": sql,
        "model_yml": yml_text,
        "column_profile": profile,
        "existing_tests": existing,
        "available_models": models,
    }
    client = anthropic.Anthropic()
    try:
        resp = _create(client, llm_model, payload, prompt_version)
    except anthropic.APIError as e:
        sys.exit(f"Anthropic API error: {e}")
    return _parse(resp, llm_model, payload, prompt_version)


def _create(client, llm_model, payload, prompt_version):
    return client.messages.create(
        model=llm_model,
        max_tokens=8000,
        system=SYSTEM_PROMPTS[prompt_version],
        tools=[proposal_tool(prompt_version)],
        # Forced tool_choice is rejected by this model family; "auto" plus the system prompt
        # ("answer ONLY by calling submit_proposals") works, and we verify a tool_use block below.
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}],
    )


def _parse(resp, llm_model, payload, prompt_version):
    block = next((b for b in resp.content if b.type == "tool_use"), None)
    if block is None:
        sys.exit("The model did not return structured proposals.")
    meta = {
        "llm_model": llm_model,
        "prompt_version": prompt_version,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "payload_sent": payload,
    }
    return block.input, meta


# --------------------------------------------------------------------------- validation
def check_proposal_shape(p: dict, model: str, models: dict, profile: dict) -> str | None:
    """Return an error string if the proposal can't be turned into a test, else None."""
    t, col, cfg = p.get("test_type"), p.get("column"), p.get("config") or {}
    if p.get("severity") not in (None, "error", "warn"):
        return f"invalid severity {p.get('severity')!r}"
    if t not in ALLOWED_TYPES:
        return f"unsupported test type {t!r}"
    if t != "singular_sql" and col not in profile["columns"]:
        return f"unknown column {col!r}"
    if t == "accepted_values" and not (isinstance(cfg.get("values"), list) and cfg["values"]):
        return "accepted_values needs a non-empty 'values' list"
    if t == "relationships":
        m = re.fullmatch(r"ref\(\s*['\"](\w+)['\"]\s*\)", str(cfg.get("to", "")))
        if not m or m.group(1) not in models or cfg.get("field") not in models[m.group(1)]:
            return "relationships needs config.to=ref('<known model>') and a documented config.field"
    if t == "singular_sql":
        sql = str(cfg.get("sql", "")).strip()
        if not re.match(r"(?is)^(select|with)\b", sql) or ";" in sql or FORBIDDEN_SQL.search(sql):
            return "singular_sql must be a single read-only SELECT/WITH without semicolons"
        if f"ref('{model}')" not in sql and f'ref("{model}")' not in sql:
            return f"singular_sql must read from {{{{ ref('{model}') }}}}"
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(cfg.get("slug", ""))):
            return "singular_sql needs a snake_case 'slug'"
    return None


def yaml_test_item(p: dict, name: str | None = None, severity: str | None = None, indent: int = 10) -> str:
    """Render one generic test as a YAML list item (block style, dbt 1.12 'arguments' syntax)."""
    t, cfg = p["test_type"], p.get("config") or {}
    pad = " " * indent
    j = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731  (JSON flow style is valid YAML)
    lines = []
    args = {}
    if t == "accepted_values":
        args = {"values": cfg["values"]}
    elif t == "relationships":
        args = {"to": cfg["to"], "field": cfg["field"]}
    if not args and not name and not severity:
        return f"{pad}- {t}"
    lines.append(f"{pad}- {t}:")
    if name:
        lines.append(f"{pad}    name: {name}")
    if args:
        lines.append(f"{pad}    arguments:")
        for k, v in args.items():
            # json.dumps quotes strings, which also keeps Jinja like ref('x') a plain YAML string.
            lines.append(f"{pad}      {k}: {j(v)}")
    if severity:
        lines.append(f"{pad}    config:")
        lines.append(f"{pad}      severity: {severity}")
    return "\n".join(lines)


def run_dbt(args: list[str], project: Path) -> subprocess.CompletedProcess:
    dbt = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
    return subprocess.run(
        [str(dbt), *args, "--project-dir", str(project), "--profiles-dir", str(project)],
        capture_output=True, text=True, encoding="utf-8", cwd=project,
    )


def build_scratch_project(tmp: Path, model: str, items: list[tuple[str, dict]], models: dict) -> None:
    """Create a throwaway dbt project: stub models over the real tables + the candidate tests.

    Stubs are never built; `dbt test` only needs ref() to resolve to main.<name>, which already
    exists in the real DuckDB file. The real project is not touched.
    """
    (tmp / "models").mkdir(parents=True)
    (tmp / "tests").mkdir()
    (tmp / "dbt_project.yml").write_text(
        "name: ai_validation\nversion: '0.0.1'\nconfig-version: 2\nprofile: ai_validation\n"
        "model-paths: ['models']\ntest-paths: ['tests']\nmacro-paths: ['macros']\n",
        encoding="utf-8",
    )
    (tmp / "profiles.yml").write_text(
        "ai_validation:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        f"      path: {json.dumps(str(DB_PATH))}\n      schema: main\n      threads: 4\n",
        encoding="utf-8",
    )
    if (DBT_DIR / "macros").exists():
        shutil.copytree(DBT_DIR / "macros", tmp / "macros")
    for m in models:
        (tmp / "models" / f"{m}.sql").write_text("select 1 as stub\n", encoding="utf-8")

    by_col: dict[str, list[str]] = {}
    for test_name, p in items:
        if p["test_type"] == "singular_sql":
            (tmp / "tests" / f"{test_name}.sql").write_text(p["config"]["sql"].strip() + "\n", encoding="utf-8")
        else:
            by_col.setdefault(p["column"], []).append(yaml_test_item(p, name=test_name, indent=10))
    if by_col:
        lines = ["version: 2", "models:", f"  - name: {model}", "    columns:"]
        for col, tests in by_col.items():
            lines += [f"      - name: {col}", "        data_tests:", *tests]
        (tmp / "models" / "_ai_tests.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_validation(model: str, proposals: list[dict], models: dict) -> list[dict]:
    """Run each proposal as a real dbt test; return [{status, failures, message}] per proposal."""
    results: list[dict | None] = [None] * len(proposals)
    names = {}
    for i, p in enumerate(proposals):
        names[f"ai_p{i + 1:02d}"] = i

    def run_subset(indices: list[int]) -> dict[str, dict]:
        items = [(f"ai_p{i + 1:02d}", proposals[i]) for i in indices]
        with tempfile.TemporaryDirectory(prefix="ai_dbt_") as d:
            tmp = Path(d)
            build_scratch_project(tmp, model, items, models)
            proc = run_dbt(["test"], tmp)
            rr = tmp / "target" / "run_results.json"
            if not rr.exists():
                return {"__error__": {"status": "error", "failures": None,
                                      "message": (proc.stdout + proc.stderr)[-1500:]}}
            out = {}
            for r in json.loads(rr.read_text(encoding="utf-8"))["results"]:
                m = re.search(r"\.(ai_p\d{2})(?:\.|$)", r["unique_id"])
                if m:
                    out[m.group(1)] = {"status": r["status"], "failures": r.get("failures"),
                                       "message": (r.get("message") or "")[:500]}
            return out

    testable = [i for i, p in enumerate(proposals) if p.get("validation") is None]
    batch = run_subset(testable) if testable else {}
    if "__error__" in batch:  # a malformed proposal broke parsing: isolate by running one at a time
        batch = {}
        for i in testable:
            single = run_subset([i])
            batch[f"ai_p{i + 1:02d}"] = single.get(f"ai_p{i + 1:02d}", single.get("__error__"))
    for name, i in names.items():
        if i in testable:
            results[i] = batch.get(name, {"status": "error", "failures": None, "message": "no result returned"})
    return results


# --------------------------------------------------------------------------- writing approved tests
def add_to_yml(yml: Path, model: str, column: str, p: dict, severity: str | None) -> None:
    """Insert a generic test into the model's yml with a minimal text edit (keeps comments/folding)."""
    lines = yml.read_text(encoding="utf-8").split("\n")
    m_start = next(i for i, l in enumerate(lines) if re.fullmatch(rf"  - name: {re.escape(model)}\s*", l))
    m_end = next((i for i in range(m_start + 1, len(lines)) if re.match(r"  - name: ", lines[i])), len(lines))
    c_start = next((i for i in range(m_start, m_end) if re.fullmatch(rf"      - name: {re.escape(column)}\s*", lines[i])), None)
    if c_start is None:
        raise RuntimeError(f"column {column} not documented in {yml.name}; document it first")
    c_end = next((i for i in range(c_start + 1, m_end) if re.match(r"      - name: ", lines[i])), m_end)
    # Trim trailing blank lines from the column block so we insert right after its last content line.
    while c_end > c_start + 1 and not lines[c_end - 1].strip():
        c_end -= 1

    item = yaml_test_item(p, severity=severity).split("\n")
    dt = next((i for i in range(c_start, c_end) if re.match(r"        data_tests:", lines[i])), None)
    if dt is None:
        lines[c_end:c_end] = ["        data_tests:", *item]
    else:
        m = re.match(r"        data_tests:\s*\[(.*)\]\s*$", lines[dt])
        if m:  # flow list like [unique, not_null] -> expand to block style, then append
            existing = [s.strip() for s in m.group(1).split(",") if s.strip()]
            lines[dt:dt + 1] = ["        data_tests:", *[f"          - {s}" for s in existing], *item]
        else:  # block list: insert after the last line that belongs to it (indent >= 10)
            end = dt + 1
            while end < c_end and (lines[end].startswith("          ") or not lines[end].strip()):
                end += 1
            while end > dt + 1 and not lines[end - 1].strip():
                end -= 1
            lines[end:end] = item
    yml.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_singular(model: str, p: dict, severity: str | None) -> Path:
    path = DBT_DIR / "tests" / f"assert_{model}_{p['config']['slug']}.sql"
    path.parent.mkdir(exist_ok=True)
    header = "{{ config(severity='warn') }}\n" if severity == "warn" else ""
    path.write_text(
        f"-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.\n"
        f"-- Rationale: {p['rationale']}\n{header}{p['config']['sql'].strip()}\n",
        encoding="utf-8", newline="\n",
    )
    return path


# --------------------------------------------------------------------------- review
def describe_test(p: dict) -> str:
    s = describe_test_core(p)
    return s + (" [proposed severity: warn]" if p.get("severity") == "warn" else "")


def describe_test_core(p: dict) -> str:
    cfg = p.get("config") or {}
    if p["test_type"] == "accepted_values":
        return f"accepted_values {json.dumps(cfg.get('values'), ensure_ascii=False)}"
    if p["test_type"] == "relationships":
        return f"relationships to {cfg.get('to')} ({cfg.get('field')})"
    if p["test_type"] == "singular_sql":
        return f"singular_sql [{cfg.get('slug')}]\n      {cfg.get('sql', '').strip()}"
    return p["test_type"]


def format_result(v: dict) -> str:
    s = v["status"].upper()
    if v["status"] == "pass":
        return "PASS"
    if v["status"] in ("fail", "warn"):
        return f"{s} ({v['failures']:,} failing rows)"
    return f"ERROR - {v['message'][:200]}"


def print_proposals(proposals: list[dict]) -> None:
    for i, p in enumerate(proposals, 1):
        target = p["column"] or "(model-level)"
        print(f"\n[{i}/{len(proposals)}] {target}: {describe_test(p)}")
        print(f"      rationale: {p['rationale']}")
        print(f"      evidence:  {p['evidence']}")
        print(f"      result:    {format_result(p['validation'])}")


def log_decision(run: dict, p: dict, decision: str, written_to: str | None, note: str | None = None) -> None:
    record = {
        "run_id": run["run_id"], "model": run["model"], "llm_model": run["llm_model"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_version": run.get("prompt_version", "v1"),
        "proposal": {k: p[k] for k in ("column", "test_type", "config", "rationale", "evidence", "severity") if k in p},
        "validation": p["validation"], "decision": decision, "written_to": written_to,
    }
    if note:
        record["note"] = note
    DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DECISIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def review(run: dict, yml: Path) -> None:
    proposals = run["proposals"]
    model = run["model"]
    print(f"\nReviewing {len(proposals)} proposals for {model}  (run {run['run_id']})")
    quit_ = False
    for i, p in enumerate(proposals, 1):
        if quit_:
            log_decision(run, p, "undecided", None)
            continue
        target = p["column"] or "(model-level)"
        print(f"\n[{i}/{len(proposals)}] {target}: {describe_test(p)}")
        print(f"      rationale: {p['rationale']}")
        print(f"      evidence:  {p['evidence']}")
        print(f"      result:    {format_result(p['validation'])}")
        status = p["validation"]["status"]
        if status == "pass":
            options, prompt = {"y", "n", "q"}, "Add this test? [y]es / [n]o / [q]uit: "
        elif status in ("fail", "warn"):
            # Severity is enforced here, not left to the model (it was inconsistent across runs):
            # a test that already fails on current data defaults to severity warn, because adding
            # it as an error would break the build on a known defect. "e" overrides to error.
            options = {"y", "w", "e", "n", "q"}
            prompt = ("FAILS on current data, so it defaults to severity warn. "
                      "[y/w] add as warn / [e] add as error / [n]o / [q]uit: ")
        else:
            options, prompt = {"n", "q"}, "Cannot be added (errored). [n]o / [q]uit: "
        # Answer format: "<choice>" or "<choice> | free-text note" (the note is logged).
        ans, note = "", None
        while ans not in options:
            raw = input(prompt)
            ans = raw.strip().lower()[:1]
            note = raw.split("|", 1)[1].strip() if "|" in raw else None
        if ans == "q":
            quit_ = True
            log_decision(run, p, "undecided", None)
            continue
        if ans == "n":
            log_decision(run, p, "rejected", None, note)
            continue
        if status in ("fail", "warn"):
            severity = None if ans == "e" else "warn"  # None = dbt default (error)
        else:
            severity = "warn" if p.get("severity") == "warn" else None
        if p["test_type"] == "singular_sql":
            written = str(write_singular(model, p, severity).relative_to(ROOT))
        else:
            add_to_yml(yml, model, p["column"], p, severity)
            written = str(yml.relative_to(ROOT))
        label = "approved_warn" if severity else ("approved_error" if status in ("fail", "warn") else "approved")
        log_decision(run, p, label, written, note)
        print(f"      -> written to {written}")
    print("\nReview finished. Decisions appended to", DECISIONS_LOG.relative_to(ROOT))


# --------------------------------------------------------------------------- main
def propose(model: str, prompt_version: str = "v2") -> dict:
    sql_path, yml_path = find_model(model)
    models = all_models()
    print(f"Profiling {model} (aggregates only)...")
    profile = profile_model(model, extended=(prompt_version == "v3"))
    existing = existing_tests(model, yml_path)
    print(f"  {profile['row_count']:,} rows, {len(profile['columns'])} columns, {len(existing)} existing tests")

    print(f"Asking the LLM for proposals (prompt {prompt_version})...")
    result, meta = call_llm(model, sql_path.read_text(encoding="utf-8"), yml_path.read_text(encoding="utf-8"),
                            profile, existing, models, prompt_version)
    raw = result.get("proposals", [])
    print(f"  {len(raw)} proposals ({meta['input_tokens']:,} in / {meta['output_tokens']:,} out tokens)")

    kept, filtered = [], []
    seen = {(e["column"], e["test"]) for e in existing if e["test"] in ("not_null", "unique")}
    for p in raw:
        err = check_proposal_shape(p, model, models, profile)
        if err:
            filtered.append({"proposal": p, "reason": err})
        elif p["test_type"] in ("not_null", "unique") and (p["column"], p["test_type"]) in seen:
            filtered.append({"proposal": p, "reason": "already exists"})
        else:
            kept.append(p)
    if filtered:
        print(f"  filtered out {len(filtered)} unusable proposals:")
        for f in filtered:
            print(f"    - {f['proposal'].get('column')}/{f['proposal'].get('test_type')}: {f['reason']}")

    print(f"Validating {len(kept)} proposals with dbt test (scratch project)...")
    for p, v in zip(kept, run_validation(model, kept, models)):
        p["validation"] = v
    run = {
        "run_id": uuid.uuid4().hex[:8], "model": model, "created": datetime.now(timezone.utc).isoformat(),
        "llm_model": meta["llm_model"], "prompt_version": prompt_version,
        "system_prompt": SYSTEM_PROMPTS[prompt_version], "usage": {k: meta[k] for k in ("input_tokens", "output_tokens")},
        "payload_sent": meta["payload_sent"], "filtered": filtered, "proposals": kept,
    }
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    out = PROPOSALS_DIR / f"{model}_{run['run_id']}.json"
    out.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved proposals + payload sent to {out.relative_to(ROOT)}")
    return run


def main() -> None:
    ap = argparse.ArgumentParser(description="Propose, validate and review dbt tests for a model.")
    ap.add_argument("model")
    ap.add_argument("--propose-only", action="store_true", help="stop before the interactive review")
    ap.add_argument("--review", nargs="?", const="latest", metavar="FILE", help="review a saved proposals file")
    ap.add_argument("--prompt-version", choices=PROMPT_VERSIONS, default="v2",
                    help="v1 baseline, v2 +monitoring rule (default), v3 +richer profile (dropped: no gain)")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    _, yml_path = find_model(args.model)

    if args.review:
        if args.review == "latest":
            files = sorted(PROPOSALS_DIR.glob(f"{args.model}_*.json"), key=lambda f: f.stat().st_mtime)
            if not files:
                sys.exit(f"No saved proposals for {args.model}; run without --review first.")
            path = files[-1]
        else:
            path = Path(args.review)
        run = json.loads(path.read_text(encoding="utf-8"))
        review(run, yml_path)
        return

    run = propose(args.model, args.prompt_version)
    print_proposals(run["proposals"])
    if args.propose_only:
        print("\n--propose-only: stopping before review. Run again with --review to decide.")
        return
    review(run, yml_path)


if __name__ == "__main__":
    main()
