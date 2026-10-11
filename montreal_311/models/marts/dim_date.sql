-- Calendar dimension generated in SQL (no packages): one row per day from the earliest
-- to the latest date seen in either timestamp column.

with bounds as (

    select
        cast(min(least(created_at, last_status_at)) as date)    as first_day,
        cast(max(greatest(created_at, last_status_at)) as date) as last_day
    from {{ ref('stg_311__contacts') }}

),

-- range(start, stop, step) is a table function; stop is exclusive, hence + 1 day.
-- It returns timestamps, so cast back to date.
days as (

    select cast(r.range as date) as date_day
    from bounds, range(bounds.first_day, bounds.last_day + interval 1 day, interval 1 day) as r

)

select
    {{ date_key('date_day') }}                  as date_key,
    date_day,
    year(date_day)                              as year,
    quarter(date_day)                           as quarter,
    month(date_day)                             as month,
    monthname(date_day)                         as month_name,
    day(date_day)                               as day_of_month,
    isodow(date_day)                            as day_of_week,   -- ISO: 1 = Monday ... 7 = Sunday
    dayname(date_day)                           as day_name,
    isodow(date_day) in (6, 7)                  as is_weekend,
    cast(date_trunc('week', date_day) as date)  as week_start_date  -- DuckDB weeks start on Monday
from days
