-- One row per normalized borough value found in EITHER borough column, plus an Unknown member.
-- Role-playing dimension: the facts join to it twice (responsible and location borough).

with all_values as (

    -- The recorded borough may hold placeholders, so carry the staging flag along.
    select borough as borough_name, is_non_borough_value
    from {{ ref('stg_311__contacts') }}
    where borough is not null

    union all

    -- The geocoded borough never holds placeholders.
    select borough_geo, false
    from {{ ref('stg_311__contacts') }}
    where borough_geo is not null

),

-- A value can appear in both columns; collapse to one row. bool_or keeps the flag true if
-- any occurrence was a placeholder.
distinct_values as (

    select
        borough_name,
        bool_or(is_non_borough_value) as is_non_borough_value
    from all_values
    group by borough_name

)

select
    {{ dim_key('borough_name') }} as borough_key,
    borough_name,
    is_non_borough_value,
    false                         as is_unknown_member
from distinct_values

union all

-- Unknown member: facts with a missing borough point here instead of holding a NULL key,
-- so inner joins to the dimension never drop rows.
select
    {{ unknown_key() }},
    'Unknown',
    false,
    true
