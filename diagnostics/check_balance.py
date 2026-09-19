"""Compatibility CLI for post-solve balance and accounting diagnostics."""

from __future__ import annotations

import sys

from geocanoe.diagnostics.output.gate import main


if __name__ == "__main__":
    sys.exit(main())
