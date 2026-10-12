-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: request_id and last_status are null exactly for Information contacts.
select * from {{ ref('stg_311__contacts') }} where (contact_nature = 'Information') <> (request_id is null) or (contact_nature = 'Information') <> (last_status is null)
