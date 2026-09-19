"""Compatibility CLI for detailed post-solve output audits."""

from __future__ import annotations

import sys

from geocanoe.diagnostics.output.audit import main


if __name__ == "__main__":
    sys.exit(main())
