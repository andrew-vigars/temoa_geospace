"""Central command dispatcher for Geospatial-CANOE diagnostics."""

from __future__ import annotations

import sys
from collections.abc import Callable

from geocanoe.diagnostics.input import gate as input_gate
from geocanoe.diagnostics.output import gate as output_gate
from geocanoe.diagnostics.selection import select_numbered


def print_help() -> None:
    """Print central diagnostic commands and their purposes."""

    print(
        """usage: check.py [inputs | outputs] [selection]

commands:
  inputs   Choose a silver configuration and validate all associated inputs
  outputs  Choose a solved run and validate its model outputs

Run without a command to choose interactively. CSV evidence is always written."""
    )


def main(argv: list[str] | None = None) -> int:
    """Dispatch a central diagnostic subcommand."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"-h", "--help"}:
        print_help()
        return 0

    handlers: dict[str, Callable[[list[str]], int]] = {
        "inputs": input_gate.main,
        "outputs": output_gate.main,
    }
    command = (
        arguments.pop(0)
        if arguments
        else select_numbered(
            list(handlers),
            "diagnostic workflow",
            display=lambda value: {
                "inputs": "Inputs - silver configuration and encoded schema",
                "outputs": "Outputs - solved SQLite run",
            }[value],
        )
    )
    handler = handlers.get(command)
    if handler is None:
        print(f"Unknown diagnostic command: {command}\n")
        print_help()
        return 2
    return handler(arguments)
