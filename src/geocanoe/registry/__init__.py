"""Registry helper for bronze data inputs.

Loads the repository-level ``registry/bronze_registry.yaml`` file and exposes
a small ``Registry`` helper for discovering and resolving registered bronze
datasets.

Usage
-----
from geocanoe.registry import Registry, load_registry

meta = load_registry()
registry = Registry()

print(registry.list_ids())
print(registry.resolve_path("commodities"))
"""

from __future__ import annotations

from pathlib import Path


try:
    import yaml
except ImportError as exc:
    raise ImportError(
        "PyYAML is required to use the registry. "
        "Install it with: pip install pyyaml"
    ) from exc


# =============================================================================
# Project discovery
# =============================================================================

def find_project_root(start_path: Path | None = None) -> Path:
    """Locate the Geospatial-CANOE repository root.

    The search begins from ``start_path`` when supplied. Otherwise, it begins
    from this module's location. Parent directories are inspected until a
    directory containing both ``registry/`` and ``data_files/`` is found.

    Parameters
    ----------
    start_path : Path | None, optional
        File or directory from which to begin searching.

    Returns
    -------
    Path
        Resolved repository root.

    Raises
    ------
    FileNotFoundError
        If the repository root cannot be located.
    """

    start = (
        start_path.resolve()
        if start_path is not None
        else Path(__file__).resolve()
    )

    candidate_start = start if start.is_dir() else start.parent

    for candidate in (candidate_start, *candidate_start.parents):
        if (
            (candidate / "registry").is_dir()
            and (candidate / "data_files").is_dir()
        ):
            return candidate

    raise FileNotFoundError(
        "Could not locate the Geospatial-CANOE repository root. "
        "Expected to find both registry/ and data_files/."
    )


PROJECT_ROOT = find_project_root()

REGISTRY_FILE = (
    PROJECT_ROOT
    / "registry"
    / "bronze_registry.yaml"
)


# =============================================================================
# Registry loading
# =============================================================================

def load_registry(
    path: Path | str | None = None,
) -> dict:
    """Load and validate the bronze dataset registry.

    Parameters
    ----------
    path : Path | str | None, optional
        Explicit registry path. When omitted, the canonical repository-level
        bronze registry is used.

    Returns
    -------
    dict
        Registry metadata containing the resolved registry path and dataset
        entries.

    Raises
    ------
    FileNotFoundError
        If the registry file does not exist.
    ValueError
        If the registry structure is invalid.
    """

    registry_path = (
        Path(path).expanduser().resolve()
        if path is not None
        else REGISTRY_FILE
    )

    if not registry_path.exists():
        raise FileNotFoundError(
            f"Registry file not found: {registry_path}"
        )

    with registry_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise ValueError(
            "Bronze registry must contain a top-level mapping."
        )

    datasets = data.get("datasets")

    if not isinstance(datasets, list):
        raise ValueError(
            "Bronze registry requires a top-level 'datasets' list."
        )

    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            raise ValueError(
                f"Dataset entry {index} must be a mapping."
            )

        dataset_id = dataset.get("id")
        dataset_path = dataset.get("path")

        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise ValueError(
                f"Dataset entry {index} requires a non-empty 'id'."
            )

        if not isinstance(dataset_path, str) or not dataset_path.strip():
            raise ValueError(
                f"Dataset {dataset_id!r} requires a non-empty 'path'."
            )

    dataset_ids = [
        dataset["id"]
        for dataset in datasets
    ]

    if len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError(
            "Bronze registry contains duplicate dataset IDs."
        )

    return {
        "path": str(registry_path),
        "datasets": datasets,
    }


# =============================================================================
# Registry interface
# =============================================================================

class Registry:
    """Programmatic interface to registered bronze datasets."""

    def __init__(
        self,
        path: Path | str | None = None,
        repo_root: Path | str | None = None,
    ) -> None:
        """Load the registry and initialize dataset lookup.

        Parameters
        ----------
        path : Path | str | None, optional
            Explicit bronze registry path.
        repo_root : Path | str | None, optional
            Explicit repository root used to resolve registered relative paths.
            When omitted, the detected project root is used.
        """

        meta = load_registry(path)

        self._meta = meta
        self._datasets = {
            dataset["id"]: dataset
            for dataset in meta["datasets"]
        }

        self.repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root is not None
            else PROJECT_ROOT
        )

    def list_ids(self) -> list[str]:
        """Return registered dataset IDs."""

        return list(self._datasets.keys())

    def get(
        self,
        dataset_id: str,
    ) -> dict | None:
        """Return registry metadata for one dataset."""

        return self._datasets.get(dataset_id)

    def resolve_path(
        self,
        dataset_id: str,
    ) -> Path | None:
        """Resolve the registered path for one dataset.

        Relative paths are interpreted from the repository root.

        Parameters
        ----------
        dataset_id : str
            Registered dataset identifier.

        Returns
        -------
        Path | None
            Resolved dataset path, or ``None`` when the ID is not registered.
        """

        entry = self.get(dataset_id)

        if entry is None:
            return None

        return (
            self.repo_root
            / entry["path"]
        ).resolve()


__all__ = [
    "Registry",
    "load_registry",
]
