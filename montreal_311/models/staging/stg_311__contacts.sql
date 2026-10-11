{{ config(materialized='table') }}  {# table, not view: md5 and window functions over 3.2M rows would be recomputed by every downstream query #}

-- Staging model: 1:1 with raw.requetes311 (no rows removed).
-- Renames French columns to English, casts types, normalizes text, and adds
-- row-level flags describing known data quality issues (see docs/profiling.md).

{% set source_columns = adapter.get_columns_in_relation(source('raw', 'requetes311')) %}

with source as (

    select * from {{ source('raw', 'requetes311') }}

),

-- Step 1: fingerprint every row using the RAW (untouched) values.
-- md5() over all columns gives identical rows an identical hash.
-- coalesce(..., '<null>') matters: concat_ws silently skips NULLs, which would let
-- (a, NULL, b) and (a, b, NULL) collide. The '|' delimiter prevents ('ab','c') = ('a','bc').
fingerprinted as (

    select
        *,
        md5(concat_ws('|',
            {%- for col in source_columns %}
            coalesce("{{ col.name }}", '<null>'){{ "," if not loop.last }}
            {%- endfor %}
        )) as row_hash
    from source

),

-- Step 2: window functions over each group of identical rows.
--   count(*) over (partition by row_hash)  -> size of the group (1 = not duplicated)
--   row_number() over (partition by row_hash) -> 1, 2, 3... inside the group
-- Window functions keep every row (unlike GROUP BY, which would collapse them).
-- Rows in a group are identical, so the order within it is irrelevant: the set of
-- (row_hash, row_number) pairs is the same on every run, which keeps the key deterministic.
numbered as (

    select
        *,
        count(*)     over (partition by row_hash) as duplicate_group_size,
        row_number() over (partition by row_hash order by row_hash) as duplicate_row_number
    from fingerprinted

),

cleaned as (

    select
        -- Surrogate key: unique even for exact duplicates thanks to the row number.
        md5(row_hash || '|' || duplicate_row_number::varchar) as contact_sk,

        -- nullif(trim(x), ''): trim whitespace, and turn blank strings into real NULLs
        -- so "empty" is represented one way only.
        nullif(trim("ID_UNIQUE"), '')                  as request_id,
        nullif(trim("NATURE"), '')                     as contact_nature,

        -- Explicit casing fixes only; French text is never blanket-lower()ed because
        -- that would break proper nouns ("Île", "Saint-Laurent"). Add new variants here.
        case nullif(trim("ACTI_NOM"), '')
            when 'Soutien aux Élus' then 'Soutien aux élus'
            when 'soutien aux élus' then 'Soutien aux élus'
            else nullif(trim("ACTI_NOM"), '')
        end                                            as activity_name,

        nullif(trim("TYPE_LIEU_INTERV"), '')           as location_type,
        nullif(trim("RUE"), '')                        as street,
        nullif(trim("RUE_INTERSECTION1"), '')          as cross_street_1,
        nullif(trim("RUE_INTERSECTION2"), '')          as cross_street_2,

        -- Source stores '0'/'1' as text; compare to '1' to get a true boolean.
        ("LOC_ERREUR_GDT" = '1')                       as has_geocoding_error,

        -- The two borough columns use different separators ("A - B" vs "A-B").
        -- Standardize both to "A-B" so they can be compared and joined.
        replace(nullif(trim("ARRONDISSEMENT"), ''), ' - ', '-')     as borough,
        replace(nullif(trim("ARRONDISSEMENT_GEO"), ''), ' - ', '-') as borough_geo,

        -- Postal codes: strip spaces, then uppercase ('H1Y2m5' -> 'H1Y2M5').
        upper(replace(nullif(trim("LIN_CODE_POSTAL"), ''), ' ', '')) as postal_code,

        -- Source timestamps are ISO text with no timezone: America/Montreal local time.
        -- TIMESTAMP (not TIMESTAMPTZ) keeps them as-is. A plain cast fails loudly on bad
        -- values, which is what we want in staging.
        cast("DDS_DATE_CREATION" as timestamp)         as created_at,
        cast("DATE_DERNIER_STATUT" as timestamp)       as last_status_at,

        nullif(trim("PROVENANCE_ORIGINALE"), '')       as original_channel,

        -- PROVENANCE_* look like 0/1 flags but hold counts (max 60): how many times the
        -- same request was reported through each channel. Hence the *_contact_count names.
        cast("PROVENANCE_TELEPHONE" as integer)        as phone_contact_count,
        cast("PROVENANCE_COURRIEL" as integer)         as email_contact_count,
        cast("PROVENANCE_PERSONNE" as integer)         as in_person_contact_count,
        cast("PROVENANCE_COURRIER" as integer)         as mail_contact_count,
        cast("PROVENANCE_TELECOPIEUR" as integer)      as fax_contact_count,
        cast("PROVENANCE_INSTANCE" as integer)         as council_contact_count,
        cast("PROVENANCE_MOBILE" as integer)           as mobile_app_contact_count,
        cast("PROVENANCE_MEDIASOCIAUX" as integer)     as social_media_contact_count,
        cast("PROVENANCE_SITEINTERNET" as integer)     as website_contact_count,

        nullif(trim("UNITE_RESP_PARENT"), '')          as responsible_unit,

        cast("LOC_LONG" as double)                     as longitude,
        cast("LOC_LAT" as double)                      as latitude,
        cast("LOC_X" as double)                        as mtm_x,
        cast("LOC_Y" as double)                        as mtm_y,

        nullif(trim("DERNIER_STATUT"), '')             as last_status,

        duplicate_group_size,
        row_hash

    from numbered

),

final as (

    select
        *,

        -- Request ID anatomy: '22-105694-A' = year prefix, number, optional letter suffix.
        -- regexp_replace removes a trailing '-<letter>'; the '$' anchors to end of string,
        -- so '22-1' (a short ID, not a suffix) is left intact.
        regexp_replace(request_id, '-[A-Z]$', '')      as request_base_id,

        -- regexp_extract(..., group 1) returns just the captured letter, or '' if no match.
        nullif(regexp_extract(request_id, '-([A-Z])$', 1), '') as request_id_suffix,

        -- Data starts in 2022, so '20-'/'21-' prefixes are suspicious. coalesce(..., false):
        -- a NULL request_id makes LIKE return NULL, but a flag should be strictly true/false.
        coalesce(request_id like '20-%' or request_id like '21-%', false) as is_unexpected_id_prefix,

        -- Borough columns disagree. Both must be present: comparing with NULL yields NULL,
        -- and a missing value is not a "mismatch".
        coalesce(borough is not null and borough_geo is not null and borough <> borough_geo, false)
                                                       as borough_mismatch,

        -- Placeholder values in the borough column that are not real boroughs.
        coalesce(borough in ('Ville de Montréal', 'Extérieur de Montréal'), false)
                                                       as is_non_borough_value,

        duplicate_group_size > 1                       as is_exact_duplicate

    from cleaned

)

select
    contact_sk,
    request_id,
    request_base_id,
    request_id_suffix,
    is_unexpected_id_prefix,
    contact_nature,
    activity_name,
    created_at,
    last_status_at,
    last_status,
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
    cross_street_1,
    cross_street_2,
    postal_code,
    borough,
    borough_geo,
    borough_mismatch,
    is_non_borough_value,
    longitude,
    latitude,
    mtm_x,
    mtm_y,
    has_geocoding_error,
    is_exact_duplicate
from final
