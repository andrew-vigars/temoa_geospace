from __future__ import annotations

import sys
from pathlib import Path

from geocanoe.schema import database
from geocanoe.schema.build import clear_output_tables, export_sqlite


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMOA_ROOT = PROJECT_ROOT / "temoa"


def test_geocanoe_export_is_loadable_by_temoa_without_solver(
    tmp_path: Path,
) -> None:
    # Temoa is intentionally vendored rather than installed as part of geocanoe.
    sys.path.insert(0, str(TEMOA_ROOT))
    try:
        from temoa.temoa_model.temoa_mode import TemoaMode
        from temoa.temoa_model.temoa_sequencer import TemoaSequencer
    finally:
        sys.path.remove(str(TEMOA_ROOT))

    seed_database = tmp_path / "seed.sqlite"
    empty_geocanoe_database = tmp_path / "empty-geocanoe.sqlite"
    encoded_database = tmp_path / "geocanoe.sqlite"
    source_sql = TEMOA_ROOT / "tests" / "testing_data" / "test_system.sql"
    database.convert_sql_to_sqlite(source_sql, seed_database)
    database.convert_sql_to_sqlite(
        PROJECT_ROOT / "data_files" / "canoe_dataset_schema.sql",
        empty_geocanoe_database,
    )

    upstream_tables = database.sqlite_to_dfs(seed_database)
    geocanoe_schema = database.sqlite_to_dfs(empty_geocanoe_database)
    tables = {
        name: table
        for name, table in upstream_tables.items()
        if name in geocanoe_schema
        and set(table.columns).issubset(geocanoe_schema[name].columns)
    }
    # The Gold MVP is intentionally single-period, so its canonical SQL omits
    # the legacy TimeSeason, TimeOfDay, and TimeSegmentFraction tables. Remove
    # upstream fixture rows indexed by those absent sets so Temoa exercises
    # its supported single-timeslice fallback. Multiperiod support is deferred
    # until after the MVP establishes the design baseline for Paper 1.
    for name, table in tables.items():
        if {"season", "tod"}.intersection(table.columns):
            tables[name] = table.iloc[0:0].copy()
    clear_output_tables(tables)
    export_sqlite(tables, encoded_database)

    source_config = (
        TEMOA_ROOT / "tests" / "testing_configs" / "config_test_system.toml"
    )
    effective_config = tmp_path / "temoa-build-only.toml"
    effective_config.write_text(
        source_config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    database.update_db_paths(
        effective_config,
        str(encoded_database),
        create_backup=False,
    )

    instance = TemoaSequencer(
        config_file=effective_config,
        output_path=tmp_path,
        mode_override=TemoaMode.BUILD_ONLY,
        silent=True,
    ).start()

    assert instance is not None
