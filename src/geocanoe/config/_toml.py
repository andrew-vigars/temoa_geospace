"""Shared TOML-scalar validation helpers used by the config loaders.

These are generic type/shape checks only (tables, strings, bools, ints,
numbers). Domain rules — which sections exist, which values are valid
choices, cross-field consistency — stay in each loader.
"""

from __future__ import annotations


def require_table(raw: dict, key: str) -> dict:
    """Return a required TOML table from a parsed configuration mapping."""

    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration section [{key}] is missing or invalid.")
    return value


def require_string(table: dict, key: str, section: str) -> str:
    """Return a required non-empty, stripped string setting."""

    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"[{section}].{key} must be a non-empty string.")
    return value.strip()


def require_bool(
    table: dict,
    key: str,
    section: str,
    default: bool | None = None,
) -> bool:
    """Return a boolean setting, required unless ``default`` is given."""

    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"[{section}].{key} must be true or false.")
    return value


def require_int(
    table: dict,
    key: str,
    section: str,
    minimum: int | None = None,
) -> int:
    """Return a required integer, rejecting bool, with an optional lower bound."""

    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"[{section}].{key} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"[{section}].{key} must be at least {minimum}.")
    return value


def require_number(table: dict, key: str, section: str) -> float:
    """Return a required numeric (int or float, not bool) setting as float."""

    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"[{section}].{key} must be a number.")
    return float(value)
