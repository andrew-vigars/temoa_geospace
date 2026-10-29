-- Read-only workflow. Open the solved database or its diagnostic subset.
-- 1. Keep time and direction visible; region encodes FROM-TO for exchange techs.
SELECT scenario, period, season, tod, region,
       substr(region, 1, instr(region, '-')-1) AS from_region,
       substr(region, instr(region, '-')+1) AS to_region,
       tech, input_comm, output_comm, flow, units
FROM output_flow_out
WHERE tech IN ('H2_PIPE', 'H2_TRUCK') AND flow > 0.000001
ORDER BY scenario, period, season, tod, from_region, to_region;

-- 2. Collapse the duplicated directional capacity records for physical corridors.
-- Do not SUM reverse orientations. Keep vintages separate at this stage.
WITH endpoints AS (
  SELECT *, substr(region,1,instr(region,'-')-1) AS a,
            substr(region,instr(region,'-')+1) AS b
  FROM output_net_capacity WHERE tech='H2_PIPE'
)
SELECT scenario, period, vintage,
       CASE WHEN a<b THEN a||'-'||b ELSE b||'-'||a END AS corridor,
       MAX(capacity) AS physical_capacity, COUNT(*) AS orientation_records
FROM endpoints
GROUP BY scenario,period,vintage,corridor
HAVING MAX(capacity)>0
ORDER BY corridor;

-- 3. Inspect the actual EoS curve, not just its first breakpoint.
SELECT region,segment,capacity_lower,capacity_upper,cost_lower,cost_upper,
       (cost_upper-cost_lower)/(capacity_upper-capacity_lower) AS marginal_capex
FROM cost_invest_eos
WHERE tech_or_group='H2_PIPE' AND region='R1296-R1297'
ORDER BY segment;

-- 4. Compare the square's existing route with a larger direct connection.
-- Raw curve CAPEX only: excludes loan annualization, discounting and OPEX.
WITH q AS (
 SELECT MAX(flow) AS flow FROM output_flow_out
 WHERE tech='H2_PIPE' AND region='R1434-R1296'
), alternatives AS (
 SELECT 'existing' AS alternative, 'R1296-R1297' AS corridor,flow AS capacity FROM q
 UNION ALL SELECT 'existing','R1297-R1435',flow FROM q
 UNION ALL SELECT 'existing','R1434-R1435',flow FROM q
 UNION ALL SELECT 'existing','R1296-R1434',flow FROM q
 UNION ALL SELECT 'larger_direct','R1296-R1297',2*flow FROM q
), costed AS (
 SELECT a.alternative,
  e.cost_lower+(a.capacity-e.capacity_lower)*(e.cost_upper-e.cost_lower)
    /(e.capacity_upper-e.capacity_lower) AS capex,
  ROW_NUMBER() OVER (PARTITION BY a.alternative,a.corridor ORDER BY e.segment) AS rn
 FROM alternatives a JOIN cost_invest_eos e ON e.region=a.corridor
 WHERE e.tech_or_group='H2_PIPE'
   AND a.capacity>=e.capacity_lower-0.000001
   AND a.capacity<=e.capacity_upper+0.000001
)
SELECT alternative,SUM(capex) AS raw_curve_capex
FROM costed WHERE rn=1 GROUP BY alternative;

-- 5. Check bounds, conversions and losses before concluding a reroute is feasible.
SELECT * FROM limit_capacity WHERE tech_or_group='H2_PIPE';
SELECT * FROM limit_new_capacity WHERE tech_or_group='H2_PIPE';
SELECT * FROM existing_capacity WHERE tech='H2_PIPE';
SELECT * FROM capacity_to_activity WHERE tech='H2_PIPE';
SELECT DISTINCT input_comm,output_comm,efficiency FROM efficiency WHERE tech='H2_PIPE';
SELECT region,tech,cost,units FROM cost_variable WHERE tech IN ('H2_PIPE','H2_TRUCK');
SELECT * FROM output_objective;
-- Also inspect group/global constraints and the ORIGINAL run's solver log.
-- A subset cannot establish that no relevant constraints exist outside its scope.
