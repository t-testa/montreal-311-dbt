-- AI-proposed (ai/propose_tests.py), human-approved. Returns rows that violate the rule.
-- Rationale: WGS84 coordinates should fall in the Montreal area.
select * from {{ ref('stg_311__contacts') }} where latitude not between 45.0 and 46.0 or longitude not between -74.5 and -73.0
