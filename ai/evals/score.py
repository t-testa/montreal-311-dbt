"""Score a saved proposals file against the known data quality issues.

Usage:
    python ai/evals/score.py <proposals.json> [--issues ai/evals/known_issues.yml] [--out FILE]

No API call. Matching rules are defined in known_issues.yml (see its header).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_ISSUES = HERE / "known_issues.yml"


def test_text(p: dict) -> str:
    """The text a clause is matched against: the SQL for singular tests, else the JSON config."""
    cfg = p.get("config") or {}
    return cfg["sql"] if p.get("test_type") == "singular_sql" and "sql" in cfg else json.dumps(cfg, ensure_ascii=False)


def clause_matches(clause: dict, p: dict) -> bool:
    text = test_text(p)
    if "test_types" in clause and p.get("test_type") not in clause["test_types"]:
        return False
    if "references" in clause:
        hit = p.get("column") in clause["references"] or any(
            re.search(rf"\b{re.escape(r)}\b", text) for r in clause["references"]
        )
        if not hit:
            return False
    if "text_regex" in clause and not re.search(clause["text_regex"], text, re.I):
        return False
    if clause.get("must_fail") and (p.get("validation") or {}).get("status") not in ("fail", "warn"):
        return False
    return True


def score(run: dict, issues_doc: dict) -> dict:
    proposals = run["proposals"]
    results = []
    for issue in issues_doc["issues"]:
        matched = [
            i + 1 for i, p in enumerate(proposals)
            if any(clause_matches(c, p) for c in issue["detect"])
        ]
        results.append({"id": issue["id"], "caught": bool(matched), "matched_proposals": matched})
    statuses = [(p.get("validation") or {}).get("status") for p in proposals]
    return {
        "proposals_file": run.get("run_id"),
        "model": run.get("model"),
        "prompt_version": run.get("prompt_version", "v1"),
        "llm_model": run.get("llm_model"),
        "tokens": run.get("usage"),
        "n_proposals": len(proposals),
        "n_pass": statuses.count("pass"),
        "n_fail": statuses.count("fail"),
        "n_warn_severity_proposed": sum(1 for p in proposals if p.get("severity") == "warn"),
        "n_error": statuses.count("error"),
        "issues": results,
        "caught": sum(r["caught"] for r in results),
        "total": len(results),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("proposals")
    ap.add_argument("--issues", default=str(DEFAULT_ISSUES))
    ap.add_argument("--out", help="also write the score as JSON")
    args = ap.parse_args()
    run = json.loads(Path(args.proposals).read_text(encoding="utf-8"))
    doc = yaml.safe_load(Path(args.issues).read_text(encoding="utf-8"))
    if run.get("model") != doc.get("model"):
        sys.exit(f"Issues file is for {doc.get('model')}, proposals are for {run.get('model')}")
    s = score(run, doc)
    print(f"{Path(args.proposals).name}  prompt={s['prompt_version']}  llm={s['llm_model']}")
    print(f"  proposals={s['n_proposals']}  pass={s['n_pass']}  fail={s['n_fail']}  error={s['n_error']}"
          f"  proposed_warn={s['n_warn_severity_proposed']}  tokens={s['tokens']}")
    for r in s["issues"]:
        mark = "CAUGHT" if r["caught"] else "missed"
        extra = f"  (proposals {r['matched_proposals']})" if r["caught"] else ""
        print(f"  [{mark:6}] {r['id']}{extra}")
    print(f"  TOTAL: {s['caught']}/{s['total']}")
    if args.out:
        Path(args.out).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
