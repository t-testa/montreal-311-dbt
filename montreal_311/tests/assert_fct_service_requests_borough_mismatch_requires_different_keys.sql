-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: A mismatch implies the two borough keys differ.
select request_id from {{ ref('fct_service_requests') }} where borough_mismatch and responsible_borough_key = location_borough_key
