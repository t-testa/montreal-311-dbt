-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Last status cannot precede creation.
select request_id from {{ ref('fct_service_requests') }} where last_status_at < created_at
