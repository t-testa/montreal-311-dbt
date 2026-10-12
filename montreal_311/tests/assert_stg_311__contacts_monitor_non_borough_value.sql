-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Known defect flag: placeholder borough values.
{{ config(severity='warn') }}
select * from {{ ref('stg_311__contacts') }} where is_non_borough_value
