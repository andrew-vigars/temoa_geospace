"""Portable diagnostic extracts of solved databases, including model inputs."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3

from geocanoe.analysis.selections import region_matches, validate_selection


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def export_subset_database(database: Path, selection: dict, target: Path, transport: str) -> Path:
    """Preserve schema/global inputs and scope regional inputs and all outputs.

    This is a diagnostic extract, not an independently re-solvable model. Boundary
    endpoint region definitions are retained, but their complete external network
    and balances are not. Original units and numerical values are copied verbatim.
    """
    selection = validate_selection(selection, database)
    selected = set(selection["regions"])
    region_matches("", selected, transport)  # Validate the transport mode.
    if target.exists() or target.with_suffix(".sql").exists():
        raise FileExistsError("Subset database or SQL file already exists.")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        objects = source.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        ).fetchall()
        tables = [(name, sql) for kind, name, sql in objects if kind == "table"]
        if any(sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE") for _, sql in tables):
            raise ValueError("Virtual tables are not supported in diagnostic extracts.")
        if any(name.lower() in {"subset_selection", "subset_manifest"} for name, _ in tables):
            raise ValueError("Source already contains diagnostic subset metadata.")
        scope = set(selected)
        counts = {}
        with closing(sqlite3.connect(target)) as destination:
            destination.execute("PRAGMA foreign_keys=OFF")
            for name, sql in tables:
                destination.execute(sql)
            source.create_function("in_selection", 1, lambda value: int(
                region_matches(value, selected, transport) or str(value).lower() in {"global", "all", "*"}
            ))

            def copy_table(name: str, where: str = "", params: tuple = ()) -> None:
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({quote(name)})")]
                cursor = source.execute(f"SELECT * FROM {quote(name)}" + where, params)
                region_index = columns.index("region") if "region" in columns else None
                count = 0
                while rows := cursor.fetchmany(2000):
                    destination.executemany(
                        f"INSERT INTO {quote(name)} VALUES ({','.join('?' for _ in columns)})", rows
                    )
                    count += len(rows)
                    if region_index is not None:
                        for row in rows:
                            region = str(row[region_index])
                            scope.add(region)
                            if len(region.split("-")) == 2:
                                scope.update(region.split("-"))
                counts[name] = count

            region_table = None
            for name, _ in tables:
                if name.lower() == "region":
                    region_table = name
                    continue
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({quote(name)})")]
                where = " WHERE in_selection(region)" if "region" in columns else ""
                copy_table(name, where)
            if region_table:
                source.create_function("retained_region", 1, lambda value: int(str(value) in scope))
                copy_table(region_table, " WHERE retained_region(region)")
            for kind, _, sql in objects:
                if kind != "table":
                    destination.execute(sql)
            destination.execute("CREATE TABLE subset_selection (region TEXT PRIMARY KEY)")
            destination.executemany("INSERT INTO subset_selection VALUES (?)", [(r,) for r in sorted(selected)])
            destination.execute("CREATE TABLE subset_manifest (key TEXT PRIMARY KEY, value TEXT)")
            destination.executemany("INSERT INTO subset_manifest VALUES (?,?)", [
                ("source_database", str(database.resolve())),
                ("selection", json.dumps(selection)),
                ("transport", transport),
                ("row_counts", json.dumps(counts)),
                ("purpose", "Diagnostic extract; regional inputs and all outputs scoped; global inputs retained. Not a standalone solve."),
                ("units", "Original source metadata copied verbatim; workbook may separately infer missing flow labels."),
            ])
            destination.commit()
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Subset database integrity check failed.")
            with target.with_suffix(".sql").open("x", encoding="utf-8") as stream:
                for line in destination.iterdump():
                    stream.write(line + "\n")
    return target
