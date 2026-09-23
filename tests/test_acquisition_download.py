from __future__ import annotations

from pathlib import Path

from geocanoe.acquisition._download import (
    content_length,
    download_restarting,
    download_resumable,
)


class Response:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status_code: int = 200,
        content_length_value: str | None = None,
    ) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.headers = (
            {"Content-Length": content_length_value}
            if content_length_value is not None
            else {}
        )

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        assert chunk_size == 3
        yield from self.chunks


class Progress:
    def __init__(self, updates: list[int]) -> None:
        self.updates = updates

    def __enter__(self) -> Progress:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def update(self, size: int) -> None:
        self.updates.append(size)


def test_content_length_handles_partial_and_invalid_headers() -> None:
    assert content_length(Response([], content_length_value="6"), 0) == 6
    assert (
        content_length(
            Response([], status_code=206, content_length_value="3"),
            3,
        )
        == 6
    )
    assert content_length(Response([], content_length_value="invalid"), 0) is None


def test_resumable_download_appends_to_partial_file(tmp_path: Path) -> None:
    destination = tmp_path / "source.zip"
    destination.with_suffix(".zip.part").write_bytes(b"abc")
    request_headers: list[dict[str, str]] = []
    progress_options: dict[str, object] = {}
    progress_updates: list[int] = []

    def request_get(*args: object, **kwargs: object) -> Response:
        request_headers.append(dict(kwargs["headers"]))
        return Response([b"def"], status_code=206, content_length_value="3")

    def progress_factory(**kwargs: object) -> Progress:
        progress_options.update(kwargs)
        return Progress(progress_updates)

    result = download_resumable(
        "https://example.test/source.zip",
        destination,
        headers={"User-Agent": "test"},
        timeout=1,
        max_retries=1,
        retry_delay=0,
        chunk_size=3,
        request_get=request_get,
        progress_factory=progress_factory,
    )

    assert result.read_bytes() == b"abcdef"
    assert request_headers == [{"User-Agent": "test", "Range": "bytes=3-"}]
    assert progress_options["initial"] == 3
    assert progress_updates == [3]


def test_restarting_atomic_download_replaces_part_file(tmp_path: Path) -> None:
    destination = tmp_path / "source.zip"

    result = download_restarting(
        "https://example.test/source.zip",
        destination,
        headers={},
        timeout=1,
        max_retries=1,
        retry_delay=0,
        chunk_size=3,
        atomic=True,
        request_get=lambda *args, **kwargs: Response([b"abc", b"def"]),
    )

    assert result.read_bytes() == b"abcdef"
    assert not destination.with_suffix(".zip.part").exists()
