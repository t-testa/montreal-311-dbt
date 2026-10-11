{# Surrogate key for a dimension member: md5 of the natural value, cast to text first.
   A NULL input yields a NULL key, which is why facts wrap this in coalesce(..., unknown_key()). #}
{% macro dim_key(expr) %}md5(cast({{ expr }} as varchar)){% endmacro %}

{# Key of the "Unknown" member that every dimension reserves for missing values. #}
{% macro unknown_key() %}md5('__unknown__'){% endmacro %}

{# Integer yyyymmdd key for dim_date, e.g. 2024-03-07 12:30 -> 20240307. #}
{% macro date_key(ts_expr) %}cast(strftime({{ ts_expr }}, '%Y%m%d') as integer){% endmacro %}
