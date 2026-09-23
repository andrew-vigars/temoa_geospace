"""Canonical Canadian province and territory identifiers."""

from __future__ import annotations


PRUID_TO_CODE: dict[str, str] = {
    "10": "NL",
    "11": "PE",
    "12": "NS",
    "13": "NB",
    "24": "QC",
    "35": "ON",
    "46": "MB",
    "47": "SK",
    "48": "AB",
    "59": "BC",
    "60": "YT",
    "61": "NT",
    "62": "NU",
}

PROVINCE_NAME_TO_CODE: dict[str, str] = {
    "Newfoundland and Labrador": "NL",
    "Prince Edward Island": "PE",
    "Nova Scotia": "NS",
    "New Brunswick": "NB",
    "Quebec": "QC",
    "Québec": "QC",
    "Ontario": "ON",
    "Manitoba": "MB",
    "Saskatchewan": "SK",
    "Alberta": "AB",
    "British Columbia": "BC",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}

PROVINCE_NAME_CASEFOLD_TO_CODE: dict[str, str] = {
    name.casefold(): code for name, code in PROVINCE_NAME_TO_CODE.items()
}

__all__ = [
    "PROVINCE_NAME_CASEFOLD_TO_CODE",
    "PROVINCE_NAME_TO_CODE",
    "PRUID_TO_CODE",
]
