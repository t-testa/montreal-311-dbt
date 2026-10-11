-- One row per activity (source ACTI_NOM), plus an Unknown member.
-- Nature and location type are NOT attributes here: the same activity occurs under several
-- natures (665 of 995), so they live on the facts.

select
    {{ dim_key('activity_name') }} as activity_key,
    activity_name,
    false                          as is_unknown_member
from (
    select distinct activity_name
    from {{ ref('stg_311__contacts') }}
    where activity_name is not null
)

union all

-- Reserved for future loads where activity_name could be null.
select
    {{ unknown_key() }},
    'Unknown',
    true
