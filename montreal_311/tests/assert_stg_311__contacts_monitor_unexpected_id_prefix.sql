-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Known defect flag: request IDs with 20-/21- prefix.
{{ config(severity='warn') }}
select * from {{ ref('stg_311__contacts') }} where is_unexpected_id_prefix
