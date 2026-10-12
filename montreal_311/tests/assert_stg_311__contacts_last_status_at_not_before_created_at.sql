-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Last status change cannot precede creation.
select * from {{ ref('stg_311__contacts') }} where last_status_at < created_at
