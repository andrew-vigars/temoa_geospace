# Select output-map regions

Generate an output map as usual (`geocanoe-map`, or the map generated after a
model run). Newly generated maps include a **Region selection** panel. To enable
saving directly into the associated run folder, open the map with the local helper:

```powershell
python -m geocanoe.analysis.selections --db "output_files/RUN/model.sqlite"
```

The helper opens the run's interactive map in your browser. Keep its terminal
running while selecting and saving. The server automatically stops about 15
seconds after the last map tab closes. Refreshing or keeping another map tab
open keeps it running. If a browser crashes without sending a close event,
missing heartbeats stop the server after roughly three minutes. Active Excel
exports finish before shutdown. Add `--keep-alive` to disable automatic shutdown.
Restart an already running helper once to enable this behavior. If several maps exist in that run directory,
add `--map "output_files/RUN/MAP_interactive_folium.html"`. Existing maps need
regenerating once to acquire the selection panel.

Enable **Select regions**, then click model polygons to toggle them. The orange
highlight and selected-ID list show the exact selection. **Find** adds an exact
region ID and zooms to it. **Rectangle** selects by dragging, and **Polygon**
selects by clicking corners followed by **Finish**. Both area tools test model
polygon centres; use individual clicks to refine boundary cells. Choose **Remove**
under Area action to subtract a drawn area. **Cancel** restores normal navigation.
Disabling selection restores output marker popups and ordinary map navigation.
The panel starts minimized. Use **Open** to expand it, or **Minimize** in the panel header to collapse it into a small tab showing the
selection count. **Open** restores the panel and its settings. While minimized,
region picking is paused so the legend and map popups remain accessible.

Name the selection and click **Save**. Files are stored as:

```text
output_files/RUN/
  model.sqlite
  MAP_interactive_folium.html
  region_selections/
    study_area.json
    study_area/
      excel_outputs/output_tables.xlsx
      sql_diagnostics/costs.csv
```

Names accept letters, numbers, underscores and hyphens. Saving an existing name
is rejected; use a new name for a revised selection. Reload saved selections
through the dropdown, or load a JSON file. Selections record the database's
absolute path, exact model region IDs and available schema fingerprint. Moving
a run requires regenerating its selections because the database identity changes.

Opening the HTML file directly also supports selection, but **Save** downloads
JSON using the browser's normal download location. Import that file into its run:

```powershell
python -m geocanoe.analysis.selections --db "output_files/RUN/model.sqlite" --import-selection "Downloads/study_area.json"
```

## Excel exports

The map's **Export selected regions to Excel** section runs the export directly.
The source solved database is shown and comes from the map's associated run.
Select output tables (all are selected initially), choose the connection scope,
enter a workbook name ending in `.xlsx`, and click **Export workbook + SQL**. No separate
JSON save or command is required. The current selection is used, including any
changes since loading a saved selection. If the selection name is blank, the
workbook's filename stem becomes the selection name. The status shows the full
saved workbook path. Existing workbook names are rejected to prevent overwrites.
Each UI export also writes matching `.sqlite` and `.sql` companions next to the
workbook. The SQLite file is ready for queries, and the SQL text dump can restore
the same extract. They preserve original table definitions, views, indexes,
global input tables, regional input rows and all regional solved output tables.
Workbook table choices affect workbook sheets only. The selected transport rule
also filters the database's regional rows; global/all/wildcard rows and endpoint
region definitions are retained. `subset_selection` records the exact chosen
node IDs; `subset_manifest` records the source, scope, row counts and limitations.
Units and values in SQL/SQLite are copied verbatim from the solved database;
the workbook may separately infer missing flow-unit labels with provenance.
These are diagnostic extracts, not independently re-solvable models: external
network balances and constraints can be lost when a region is cut out. Original
database files are unchanged, and existing companion filenames are not replaced.
Keep the local map helper running; export is unavailable in a directly opened
HTML file. Restart an already running helper after upgrading this feature.

The equivalent command-line workflow remains available:

```powershell
geocanoe-export --db "output_files/RUN/model.sqlite" --selection "output_files/RUN/region_selections/study_area.json" --transport touching
```

Without `--out`, scoped exports go into
`region_selections/study_area/excel_outputs/`. The workbook includes the selected
IDs, database and transport rule in a `RegionSelection` sheet. Tables without a
`region` column are skipped because they cannot be scoped to the selection.
Derived summaries use the filtered rows.

Node rows match exact IDs. Transport regions use CANOE's `FROM-TO` encoding:

| `--transport` | Included transport regions |
| --- | --- |
| `touching` (default) | At least one endpoint selected |
| `within` | Both endpoints selected |
| `boundary` | Exactly one endpoint selected |

All three modes also include selected node rows. Boundary mode identifies
crossing corridors; distinguish imports and exports using directional flow data.

## SQL diagnostics

Write a diagnostic query in `costs.sql`, for example:

```sql
SELECT o.*
FROM OutputCost AS o
JOIN selected_regions AS s ON CAST(o.region AS TEXT) = s.region;
```

Then run:

```powershell
python -m geocanoe.analysis.selections --db "output_files/RUN/model.sqlite" --selection "output_files/RUN/region_selections/study_area.json" --sql-file costs.sql
```

The database is opened read-only. `selected_regions` is a temporary table in
that query connection; the solved database is unchanged. Query results go to
`region_selections/study_area/sql_diagnostics/costs.csv`. Existing CSV names are
not overwritten. SQL joins match exact node IDs; include corridor matching
explicitly when writing transport diagnostics. This is a scoped SQL runner;
the existing whole-run diagnostic gates retain their whole-run checks.
