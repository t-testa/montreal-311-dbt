-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Known defect flag: geocoding errors.
{{ config(severity='warn') }}
select * from {{ ref('stg_311__contacts') }} where has_geocoding_error
