# AI test writer: prompt and profile experiment

**Question:** can the test-writing agent (`ai/propose_tests.py`) find the data quality issues we
already know about in `stg_311__contacts`, and what does it take to get it to?

**Setup:** `--propose-only` runs on the same model, same existing tests, same LLM
(`claude-sonnet-5-5`), scored by `ai/evals/score.py` against six phase 1 issues defined in
`ai/evals/known_issues.yml`. v1 and v3 are **single runs**; v2 was run **three times**. No
proposal was reviewed or approved in any run.

**Status:** v2 is the default prompt. **v3 is dropped** (no gain over v2, more tokens).

## Results

Columns show the first run of each version; the v2 repeats are in the next section.

| | v1 (baseline, 1 run) | v2 (3 runs; run 1 shown) | v3 (1 run, dropped) |
|---|---:|---:|---:|
| Change vs. previous | none | + one generic prompt rule | + richer profile statistics |
| **Known issues caught** | **1 / 6** | **4 / 6** | **4 / 6** |
| Proposals | 16 | 20 | 20 |
| Passing on current data | 16 | 15 | 15 |
| Failing on current data | 0 | 5 | 5 |
| Proposals with `severity: warn` | n/a (field not offered) | 5 | 0 |
| Input tokens | 15,299 | 15,475 | 17,913 |
| Output tokens | 1,815 | 2,582 | 2,923 |
| Run id / file | `10cab8c7` | `4396affd` | `e82bd5ba` |

Proposals files are in `ai/logs/proposals/`; per-run scores are in `ai/evals/scores/`. Each
proposals file records its `prompt_version` (the two earlier runs were backfilled as `v1`).

### v2 across three runs

| | Run 1 (`4396affd`) | Run 2 (`3cb99d31`) | Run 3 (`db9c88ac`) |
|---|---:|---:|---:|
| Known issues caught | 4 / 6 | 4 / 6 | 4 / 6 |
| Issues caught | exact duplicates, borough mismatch, ID prefix, non-borough values | same four | same four |
| Issues missed | suffixed IDs, casing/format | same two | same two |
| Proposals | 20 | 21 | 19 |
| Failing monitors | 5 | 5 | 5 |
| Failing monitors with `severity: warn` | **5** | **0** | **0** |
| Tokens in / out | 15,475 / 2,582 | 15,475 / 1,997 | 15,475 / 2,433 |

- **Detection was stable.** All three runs caught the same four issues and missed the same two. In
  every run the model proposed one monitor per flag column (the same five flags each time,
  including `has_geocoding_error`), and each failed on the real data.
- **Severity was not stable.** Only run 1 marked its monitors `severity: warn` (5 of 5 failing
  monitors). In runs 2 and 3, **0 of 5 failing monitors had `severity: warn`**, so 10 of the
  15 failing monitors across the three runs were proposed at the default error severity.
  Approved as written, those would break the build on known defects. Those runs also used a
  different form, `accepted_values [false]` instead of a `where flag` singular test.
- **Net:** the prompt rule reliably gets the model to monitor the flag columns, but not to
  assign the severity it asked for.
- **Severity is now enforced in code, not by the model.** Because the model was inconsistent
  (5 of 15 failing monitors had `warn` across the 3 runs), `ai/propose_tests.py` now defaults
  every proposal that fails validation on current data to `severity: warn` in review, regardless
  of what the model proposed. The reviewer can override to error with `e`. The model's
  `severity` field is only honored for proposals that pass.

### Per issue (first run of each version)

| Known issue | v1 | v2 | v3 |
|---|:---:|:---:|:---:|
| `exact_duplicates`: 713 identical row pairs | missed | caught | caught |
| `borough_mismatch`: recorded vs geocoded borough disagree, 11,974 rows | missed | caught | caught |
| `unexpected_id_prefix`: 3 IDs starting `20-`/`21-` | missed | caught | caught |
| `suffixed_ids`: `-A`/`-B` suffixes, 11 base IDs shared | missed | missed | missed |
| `non_borough_values`: placeholder boroughs, 1,395 rows | missed | caught | caught |
| `casing_whitespace_format`: staging already fixes it | caught | missed | missed |

## What changed between versions

- **v1, baseline.** The original system prompt. The profile gives, per column, type, null %,
  distinct count, min/max and string lengths, plus the top 10 values for columns with up to 50
  distinct values (`values_complete` only when there are 10 or fewer).
- **v2, one prompt rule.** Added a single generic rule to the system prompt, with no column
  names: boolean flag columns whose true count is small but non-zero are known-defect
  indicators and deserve a monitoring test with `severity: warn`. The proposal schema gained an
  optional `severity` field. The profile is unchanged.
- **v3, richer profile.** The v2 prompt is unchanged. The profile adds (a) duplicate-value
  counts (`duplicate_values`, `rows_in_duplicate_values`) on candidate key columns, meaning names
  ending in `_id`/`_sk`/`_key` or columns that are at least 90% distinct, and (b) complete value
  lists for every column with up to 50 distinct values.

## Findings

**v1: the model saw the defect counts and proposed nothing.** The v1 payload already contained
the counts of the flagged rows: `is_exact_duplicate` true on 1,426 rows, `borough_mismatch` on
11,974, `is_unexpected_id_prefix` on 3, `is_non_borough_value` on 1,395. The model proposed
zero tests on any of those columns. All 16 of its proposals passed, so it caught nothing
(its one match, a postal-code format test, is a guard against a problem staging already
fixes). The information was there; the agent treated the flags as documentation. It
gravitated to tests that confirm what the data already looks like (not-null, accepted values
copied from the observed data, non-negative counts), which can't fail.

**v2: one generic sentence changed the behavior.** With the monitoring rule, the model
proposed a warn-severity test on every flag column (five proposals, all `severity: warn`),
four of which map to known issues, raising the score from 1/6 to 4/6. Each fails on the current
data with the right row count (3, 11,974, 1,395 and 1,426). The fifth is
`has_geocoding_error`, 64,599 rows, a real issue that was not in the six.

**v3: richer statistics did not improve the score, and cost more.** It caught the same four
issues and missed the same two, with 17% more input tokens than v1 (+16% over v2) and
13% more output than v2. Two observations:
- The new `duplicate_values: 11` statistic on `request_base_id` was in the payload, but the
  model still did not propose a uniqueness or shared-base-id monitor, so `suffixed_ids` stayed
  missed in all three runs. It again proposed `accepted_values ["A", "B"]` on the suffix,
  which passes and detects nothing.
- v3 proposed the same five flag tests but set no severity on any of them. This first looked
  like a v3 effect, but the v2 repeats show the same omission in two of three v2 runs, so it is
  run-to-run variation, not something the richer profile caused.

v3 is dropped: it matches v2 on detection, costs about 16% more input tokens, and did not fix
`suffixed_ids`, the issue its new duplicate statistics were meant to help with.

**Where the score is soft.** The `casing_whitespace_format` match in v1 comes from a
postal-code regex that passes. It counts only because the issue is already remediated in
staging and the rule for it has `must_fail: false`. v2 and v3 did not propose it. The
cleaner comparison is the five issues still present in staging: v1 caught none of them, and v2
and v3 each caught four, with tests that fail on the real defect (`must_fail: true`).

## Limitations

- **Small samples.** v2 has three runs; v1 and v3 have one each. Three runs show detection was
  stable and severity was not, but are too few for any rate estimate. v1's single run and v3's
  single run could differ from their own repeat runs.
- **v3 changes two things at once** (duplicate statistics and wider value lists), so the
  experiment cannot say which one, if either, mattered. It was dropped without isolating them.
- **Scoring rules are mine.** The detection clauses in `known_issues.yml` are a judgment call
  about what counts as "catching" an issue. Extra proposals (like the geocoding monitor) are not
  rewarded or penalized.
- **Caught is not the same as approvable.** A test that catches an issue but defaults to error
  severity would break the build on known data; the score doesn't distinguish.
- **Cost is reported in tokens only.** The per-token price is not recorded here.

## Reproduce

```powershell
python ai/propose_tests.py stg_311__contacts --propose-only --prompt-version v1   # or v2 (default), v3
python ai/evals/score.py ai/logs/proposals/stg_311__contacts_<run_id>.json
```
