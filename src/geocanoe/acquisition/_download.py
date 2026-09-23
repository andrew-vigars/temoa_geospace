"""Shared HTTP download mechanics for Bronze acquisition stages."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import requests


def content_length(response: requests.Response, offset: int) -> int | None:
    """Return the expected final byte count for a streamed response."""

    header = response.headers.get("Content-Length")
    if header is None:
        return None
    try:
        length = int(header)
    except ValueError:
        return None
    return offset + length if response.status_code == 206 else length


def download_resumable(
    url: str,
    destination: Path,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_retries: int,
    retry_delay: float,
    chunk_size: int,
    overwrite: bool = False,
    request_get: Callable[..., requests.Response] = requests.get,
    progress_factory: Callable[..., Any],
    report_completion: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Download atomically, resuming a partial transfer when supported."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    if destination.exists() and not overwrite:
        print(f"[Skip download] {destination.name} already exists.")
        return destination
    if overwrite:
        destination.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        request_headers = dict(headers)
        if offset:
            request_headers["Range"] = f"bytes={offset}-"

        try:
            print(
                f"[Download] {destination.name} "
                f"(attempt {attempt}/{max_retries}, offset {offset:,})"
            )
            response = request_get(
                url,
                headers=request_headers,
                stream=True,
                timeout=timeout,
            )
            if offset and response.status_code != 206:
                response.close()
                partial.unlink(missing_ok=True)
                offset = 0
                response = request_get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=timeout,
                )

            with response:
                response.raise_for_status()
                expected_size = content_length(response, offset)
                mode = "ab" if offset and response.status_code == 206 else "wb"
                with (
                    partial.open(mode) as file,
                    progress_factory(
                        total=expected_size,
                        initial=offset,
                        desc=destination.name,
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                        dynamic_ncols=True,
                    ) as progress,
                ):
                    for chunk in response.iter_content(chunk_size):
                        if chunk:
                            file.write(chunk)
                            progress.update(len(chunk))

            actual_size = partial.stat().st_size
            if expected_size is not None and actual_size != expected_size:
                raise OSError(
                    f"Incomplete download for {destination.name}: expected "
                    f"{expected_size:,} bytes, found {actual_size:,}."
                )

            partial.replace(destination)
            if report_completion:
                print(
                    f"[Complete download] {destination.name} "
                    f"({actual_size:,} bytes)"
                )
            return destination
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            if attempt < max_retries:
                print(f"[Retry] {destination.name}: {exc}")
                sleep(retry_delay)

    raise RuntimeError(f"Failed to download {url}") from last_error


def download_restarting(
    url: str,
    destination: Path,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_retries: int,
    retry_delay: float,
    chunk_size: int,
    overwrite: bool = False,
    atomic: bool = False,
    post_download_delay: float = 0,
    request_get: Callable[..., requests.Response] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Download from the beginning on each attempt, optionally via a part file."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        print(f"[Skip download] {destination.name} already exists.")
        return destination
    if overwrite:
        destination.unlink(missing_ok=True)

    transfer_path = (
        destination.with_suffix(destination.suffix + ".part")
        if atomic
        else destination
    )
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Download] {destination.name} (attempt {attempt}/{max_retries})")
            with request_get(
                url,
                headers=headers,
                stream=True,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                with transfer_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size):
                        if chunk:
                            file.write(chunk)

            if atomic:
                transfer_path.replace(destination)
            print(f"[Complete download] {destination.name}")
            if post_download_delay:
                sleep(post_download_delay)
            return destination
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            try:
                transfer_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                print(
                    f"[Cleanup warning] Could not remove partial file "
                    f"{transfer_path}: {cleanup_error}"
                )
            if attempt < max_retries:
                print(f"[Retry] {destination.name}: {exc}")
                sleep(retry_delay)

    raise RuntimeError(f"Failed to download {url}") from last_error


__all__ = ["content_length", "download_restarting", "download_resumable"]
