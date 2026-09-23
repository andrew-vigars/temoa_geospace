"""Central CLI entry point for Geospatial-CANOE diagnostics."""

from __future__ import annotations

import sys

from geocanoe.diagnostics.cli import main


if __name__ == "__main__":
    sys.exit(main())
