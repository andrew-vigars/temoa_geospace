"""Shared project-path discovery utilities for Geospatial-CANOE."""

from __future__ import annotations

from pathlib import Path


def find_project_root(start_path: Path | None = None) -> Path:
    """Locate the Geospatial-CANOE repository root.

    The search begins from ``start_path`` when supplied. Otherwise, both the
    current module location and active working directory are searched. Each
    starting location and its parent directories are inspected in order, and
    the first directory containing both ``scripts/`` and ``data_files/`` is
    treated as the repository root.

    Parameters
    ----------
    start_path : Path | None, optional
        Explicit file or directory from which to begin the upward search. File
        paths are converted to their parent directory before searching. When
        omitted, the module location and current working directory are searched.

    Returns
    -------
    Path
        Absolute path to the detected Geospatial-CANOE repository root.

    Raises
    ------
    FileNotFoundError
        If no searched directory contains both the expected ``scripts/`` and
        ``data_files/`` directories.
    """

    search_starts = (
        [start_path.resolve()]
        if start_path is not None
        else [Path(__file__).resolve(), Path.cwd().resolve()]
    )

    for start in search_starts:
        candidate_start = start if start.is_dir() else start.parent

        for candidate in (candidate_start, *candidate_start.parents):
            if (
                (candidate / "scripts").is_dir()
                and (candidate / "data_files").is_dir()
            ):
                return candidate

    raise FileNotFoundError(
        "Could not locate the Geospatial-CANOE repository root. "
        "Expected to find both scripts/ and data_files/."
    )