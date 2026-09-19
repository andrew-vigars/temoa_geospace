"""Small interactive selectors shared by diagnostic entry points."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar


T = TypeVar("T")


def select_numbered(
    options: Sequence[T],
    label: str,
    *,
    display: Callable[[T], str] = str,
) -> T:
    """Prompt until the user selects one item from a numbered list."""

    if not options:
        raise ValueError(f"No {label} options are available.")

    print(f"\nAvailable {label} options:")
    for index, option in enumerate(options):
        print(f"  [{index}] {display(option)}")

    while True:
        choice = input(f"\nSelect {label} number: ").strip()
        try:
            return options[int(choice)]
        except (ValueError, IndexError):
            print("Invalid selection.")
