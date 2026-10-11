-- Fact table. Grain: one row per contact_sk (information calls with no request_id).

select
    contact_sk,
    {{ date_key('created_at') }}                                  as created_date_key,
    coalesce({{ dim_key('activity_name') }}, {{ unknown_key() }}) as activity_key,
    contact_nature,
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
from {{ ref('int_311__information_contacts') }}
