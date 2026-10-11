-- Service requests, complaints and comments: every contact that has a request_id.
-- Grain: one row per request_id. Adds open/closed logic from the status_mapping seed.

with requests as (

    select * from {{ ref('stg_311__contacts') }}
    where request_id is not null

),

status_map as (

    select * from {{ ref('status_mapping') }}

)

select
    r.request_id,
    r.request_base_id,
    r.request_id_suffix,
    r.is_unexpected_id_prefix,
    r.contact_nature,
    r.activity_name,
    r.created_at,
    r.last_status,
    r.last_status_at,

    -- LEFT JOIN, not INNER: an unmapped status must not silently drop the request.
    -- It shows up as NULL here, and the relationships test on the staging model fails first.
    m.is_closed,
    m.closed_outcome,

    -- The source has no "closed at" timestamp, so last_status_at stands in for it.
    -- date_diff in seconds / 86400.0 gives fractional days (an integer day diff would
    -- round a 23-hour request to 0). The CASE has no ELSE, so open requests get NULL.
    case
        when m.is_closed
            then date_diff('second', r.created_at, r.last_status_at) / 86400.0
    end                                                as resolution_days,

    r.original_channel,
    r.phone_contact_count,
    r.email_contact_count,
    r.in_person_contact_count,
    r.mail_contact_count,
    r.fax_contact_count,
    r.council_contact_count,
    r.mobile_app_contact_count,
    r.social_media_contact_count,
    r.website_contact_count,
    r.responsible_unit,
    r.location_type,
    r.street,
    r.cross_street_1,
    r.cross_street_2,
    r.postal_code,
    r.borough,
    r.borough_geo,
    r.borough_mismatch,
    r.longitude,
    r.latitude,
    r.has_geocoding_error

from requests as r
left join status_map as m
    on r.last_status = m.status
