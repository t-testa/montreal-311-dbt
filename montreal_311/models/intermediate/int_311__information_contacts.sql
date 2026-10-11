-- Information calls: every contact WITHOUT a request_id.
-- Grain: one row per contact_sk (exact duplicates keep separate rows and are flagged).
-- Status, location and borough are always empty for these rows, so they are not carried.

select
    contact_sk,
    contact_nature,
    activity_name,
    created_at,
    original_channel,
    phone_contact_count,
    email_contact_count,
    in_person_contact_count,
    mail_contact_count,
    fax_contact_count,
    council_contact_count,
    mobile_app_contact_count,
    social_media_contact_count,
    website_contact_count,
    responsible_unit,
    is_exact_duplicate
from {{ ref('stg_311__contacts') }}
where request_id is null
