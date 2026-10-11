-- Fact table. Grain: one row per request_id.

select
    request_id,

    -- Foreign keys. coalesce(<key>, <unknown>) routes missing values to the Unknown member.
    {{ date_key('created_at') }}                                        as created_date_key,
    {{ date_key('last_status_at') }}                                    as last_status_date_key,
    coalesce({{ dim_key('activity_name') }}, {{ unknown_key() }})       as activity_key,
    -- Role-playing dimension: two keys into dim_borough.
    coalesce({{ dim_key('borough') }}, {{ unknown_key() }})             as responsible_borough_key,
    coalesce({{ dim_key('borough_geo') }}, {{ unknown_key() }})         as location_borough_key,

    request_base_id,
    request_id_suffix,
    is_unexpected_id_prefix,
    contact_nature,
    created_at,
    last_status,
    last_status_at,
    is_closed,
    closed_outcome,
    resolution_days,
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
    location_type,
    street,
    postal_code,
    latitude,
    longitude,
    has_geocoding_error,
    borough_mismatch
from {{ ref('int_311__service_requests') }}
