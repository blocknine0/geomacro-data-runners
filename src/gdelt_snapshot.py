#!/usr/bin/env python3
"""Build a redistribution-approved rolling GDELT source window.

The public runner performs only source acquisition, structural validation,
provenance recording, and checksum publication. Geomacro's private semantic
filters and production database credentials remain outside this repository.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


MASTER_FILE_URL = "https://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
ALLOWED_HOST = "data.gdeltproject.org"
USER_AGENT = "Geomacro-Public-Data-Runners/2.0"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MASTER_TAIL_BYTES = 4 * 1024 * 1024
MIN_GDELT_COLUMNS = 61
DEFAULT_EXPORT_COUNT = 12
MAX_EXPORT_COUNT = 24
RUNNER_VERSION = "gdelt-public-window-v2.0.0"


class SnapshotError(RuntimeError):
    """Fail closed when a public-source contract is violated."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_export_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    host = (parsed.hostname or "").lower()

    if host != ALLOWED_HOST:
        raise SnapshotError(f"unexpected GDELT host: {host!r}")

    if parsed.scheme not in {"http", "https"}:
        raise SnapshotError(f"unexpected GDELT scheme: {parsed.scheme!r}")

    if not parsed.path.endswith(".export.CSV.zip"):
        raise SnapshotError(f"not a GDELT Events export: {parsed.path!r}")

    return urlunparse(parsed._replace(scheme="https", query="", fragment=""))


def parse_master_tail(text: str, count: int) -> list[str]:
    if count < 1 or count > MAX_EXPORT_COUNT:
        raise SnapshotError(f"export count must be between 1 and {MAX_EXPORT_COUNT}")

    candidates: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split()
        if not fields:
            continue
        maybe_url = fields[-1]
        if maybe_url.endswith(".export.CSV.zip"):
            candidates.append(canonical_export_url(maybe_url))

    deduped = list(dict.fromkeys(candidates))
    if len(deduped) < count:
        raise SnapshotError(
            f"master-file tail exposed only {len(deduped)} Events exports; need {count}"
        )

    return deduped[-count:]


def fetch_master_tail() -> str:
    request = Request(
        MASTER_FILE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Range": f"bytes=-{MASTER_TAIL_BYTES}",
        },
    )

    with urlopen(request, timeout=120) as response:  # noqa: S310 - fixed URL
        tail = bytearray()
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > MASTER_TAIL_BYTES:
                del tail[:-MASTER_TAIL_BYTES]

    if not tail:
        raise SnapshotError("GDELT master file response was empty")

    # A byte-range response can begin in the middle of a line. Drop that partial
    # line so an incomplete URL can never enter the allowlisted parser.
    first_newline = tail.find(b"\n")
    if first_newline >= 0:
        tail = tail[first_newline + 1 :]

    return bytes(tail).decode("utf-8", errors="strict")


def download(url: str, destination: Path) -> int:
    canonical = canonical_export_url(url)
    request = Request(canonical, headers={"User-Agent": USER_AGENT})
    written = 0

    destination.parent.mkdir(parents=True, exist_ok=True)

    with urlopen(request, timeout=120) as response:  # noqa: S310 - host validated
        content_length = response.headers.get("Content-Length")
        if content_length:
            announced = int(content_length)
            if announced <= 0 or announced > MAX_DOWNLOAD_BYTES:
                raise SnapshotError(f"unexpected Content-Length: {announced}")

        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise SnapshotError("download exceeded maximum allowed size")
                handle.write(chunk)

    if written == 0:
        raise SnapshotError("downloaded artifact is empty")

    return written


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_export_zip(path: Path) -> dict[str, object]:
    if not zipfile.is_zipfile(path):
        raise SnapshotError("GDELT artifact is not a valid ZIP archive")

    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        event_members = [name for name in members if name.endswith(".export.CSV")]

        if len(event_members) != 1:
            raise SnapshotError(
                f"expected one .export.CSV member, found {len(event_members)}"
            )

        member = event_members[0]
        row_count = 0
        min_columns: int | None = None
        max_columns = 0

        with archive.open(member, "r") as raw_handle:
            text_handle = (line.decode("utf-8", errors="replace") for line in raw_handle)
            reader = csv.reader(text_handle, delimiter="\t")

            for row in reader:
                if not row:
                    continue
                column_count = len(row)
                if column_count < MIN_GDELT_COLUMNS:
                    raise SnapshotError(
                        f"row {row_count + 1} has only {column_count} columns"
                    )
                row_count += 1
                min_columns = (
                    column_count
                    if min_columns is None
                    else min(min_columns, column_count)
                )
                max_columns = max(max_columns, column_count)

        if row_count == 0:
            raise SnapshotError("GDELT export contains no event rows")

    return {
        "zip_member": member,
        "row_count": row_count,
        "min_columns": min_columns,
        "max_columns": max_columns,
    }


def build_snapshot(output_dir: Path, export_count: int = DEFAULT_EXPORT_COUNT) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    index_text = fetch_master_tail()
    source_urls = parse_master_tail(index_text, export_count)

    window_path = output_dir / "gdelt-window.zip"
    manifest_path = output_dir / "gdelt-window.manifest.json"
    checksum_path = output_dir / "gdelt-window.sha256"

    sources: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="geomacro-gdelt-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)

        for position, source_url in enumerate(source_urls, start=1):
            source_name = Path(urlparse(source_url).path).name
            local_path = temp_dir / source_name
            byte_count = download(source_url, local_path)
            validation = validate_export_zip(local_path)
            digest = sha256_file(local_path)

            sources.append(
                {
                    "position": position,
                    "source_url": source_url,
                    "source_artifact_name": source_name,
                    "sha256": digest,
                    "bytes": byte_count,
                    **validation,
                }
            )

        source_manifest = {
            "schema_version": "1.0.0",
            "runner_version": RUNNER_VERSION,
            "source_id": "gdelt_events",
            "publisher": "GDELT Project",
            "dataset": "GDELT Events 2.x",
            "fetched_at": utc_now(),
            "commercial_use_allowed": True,
            "redistribution_allowed": True,
            "raw_storage_allowed": True,
            "window": {
                "export_count": len(sources),
                "first_source_url": sources[0]["source_url"],
                "last_source_url": sources[-1]["source_url"],
                "total_rows": sum(int(item["row_count"]) for item in sources),
                "sources": sources,
            },
            "production_boundary": {
                "writes_production_database": False,
                "contains_private_credentials": False,
                "downstream_ingestion_required": True,
            },
        }

        with zipfile.ZipFile(window_path, "w", compression=zipfile.ZIP_STORED) as window:
            for item in sources:
                source_name = str(item["source_artifact_name"])
                window.write(temp_dir / source_name, arcname=f"exports/{source_name}")
            window.writestr(
                "source-manifest.json",
                json.dumps(source_manifest, indent=2, sort_keys=True) + "\n",
            )

    window_digest = sha256_file(window_path)
    manifest = {
        **source_manifest,
        "artifact": {
            "filename": window_path.name,
            "sha256": window_digest,
            "bytes": window_path.stat().st_size,
            "member_count": len(sources) + 1,
        },
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_path.write_text(f"{window_digest}  {window_path.name}\n", encoding="utf-8")

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="dist/gdelt",
        help="Output directory for the rolling source window",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_EXPORT_COUNT,
        help="Number of recent 15-minute Events exports to include",
    )
    args = parser.parse_args(argv)

    try:
        manifest = build_snapshot(Path(args.output), export_count=args.count)
    except (OSError, ValueError, SnapshotError, zipfile.BadZipFile) as exc:
        print(f"GDELT_PUBLIC_SNAPSHOT_FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "GDELT_PUBLIC_WINDOW_OK",
        manifest["artifact"],
        manifest["window"]["export_count"],
        manifest["window"]["total_rows"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
