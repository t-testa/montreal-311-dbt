-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Known defect flag: borough and geo borough disagree.
{{ config(severity='warn') }}
select * from {{ ref('stg_311__contacts') }} where borough_mismatch
