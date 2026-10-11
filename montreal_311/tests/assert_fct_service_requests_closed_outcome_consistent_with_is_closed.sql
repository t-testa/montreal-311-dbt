-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: closed_outcome and resolution_days are populated only for closed requests.
select request_id from {{ ref('fct_service_requests') }} where (is_closed and (closed_outcome is null or resolution_days is null)) or (not is_closed and (closed_outcome is not null or resolution_days is not null))
