"""Generate GeoCANOE's starter configuration from the installed TEMOA package."""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

import tomlkit

from geocanoe.paths import find_project_root


TUTORIAL_RESOURCE = "tutorial_assets/config_sample.toml"
DEFAULT_CONFIG_RELATIVE_PATH = Path("config/temoav4/config_sample.toml")
DEFAULT_DATABASE_PATH = "data_files/processed/schema/CANOE_geospatial.sqlite"


def default_config_path() -> Path:
    """Return the starter-config destination in the active GeoCANOE checkout."""

    return find_project_root() / DEFAULT_CONFIG_RELATIVE_PATH


def temoa_tutorial_config_text() -> str:
    """Read the canonical tutorial configuration from the installed TEMOA wheel."""

    resource = files("temoa").joinpath(TUTORIAL_RESOURCE)
    if not resource.is_file():
        raise FileNotFoundError(
            "The installed TEMOA package does not contain "
            f"{TUTORIAL_RESOURCE!r}. Reinstall the pinned TEMOA dependency."
        )
    return resource.read_text(encoding="utf-8")


def generate_temoa_config(
    output_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Materialize TEMOA's tutorial config with GeoCANOE runtime defaults.

    TEMOA remains the owner of the complete configuration schema and comments.
    GeoCANOE changes only the settings required by its current model contract:
    EOS, the selected solver, MIP-safe output handling, and placeholder database
    paths that ``geocanoe-run`` replaces with its isolated working database.
    Solver defaults remain owned by TEMOA; this generator adds only GeoCANOE's
    explicit 1% MIP optimality gap instead of copying TEMOA's default options.
    """

    destination = (output_path or default_config_path()).expanduser().resolve()
    if destination.exists() and not force:
        raise FileExistsError(
            f"Configuration already exists: {destination}. "
            "Use --force to regenerate it from the installed TEMOA package."
        )

    document = tomlkit.parse(temoa_tutorial_config_text())
    document["extensions"] = ["eos"]
    document["input_database"] = DEFAULT_DATABASE_PATH
    document["output_database"] = DEFAULT_DATABASE_PATH
    solver = tomlkit.inline_table()
    solver["name"] = "gurobi"
    solver_options = tomlkit.inline_table()
    solver_options["MIPGap"] = 0.01
    solver["options"] = solver_options
    document["solver"] = solver
    document["save_duals"] = False
    document["check_units"] = False

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(tomlkit.dumps(document), encoding="utf-8")
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse starter-configuration generation arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate GeoCANOE's starter TEMOA v4 configuration from the "
            "tutorial template packaged with the installed TEMOA dependency."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Destination TOML path. Defaults to "
            "config/temoav4/config_sample.toml in this GeoCANOE checkout."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing generated configuration.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Generate and report the starter TEMOA configuration path."""

    args = parse_args(argv)
    generated = generate_temoa_config(args.output, force=args.force)
    print(f"Generated TEMOA v4 starter config: {generated}")


if __name__ == "__main__":
    main()
