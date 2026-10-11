-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Resolution time cannot be negative.
select request_id from {{ ref('fct_service_requests') }} where resolution_days < 0
