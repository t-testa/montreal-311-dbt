-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: Contact counts are non-negative.
select request_id from {{ ref('fct_service_requests') }} where phone_contact_count < 0 or email_contact_count < 0 or in_person_contact_count < 0 or mail_contact_count < 0 or fax_contact_count < 0 or council_contact_count < 0 or mobile_app_contact_count < 0 or social_media_contact_count < 0 or website_contact_count < 0
