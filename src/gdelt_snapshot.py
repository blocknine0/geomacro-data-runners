#!/usr/bin/env python3
"""Build a redistribution-approved rolling GDELT source artifact.

This runner deliberately does not contain Geomacro's private semantic filters or
production database credentials. It fetches the latest GDELT 2 Events export,
validates the archive contract, records provenance, and emits a checksum-bound
artifact for guarded downstream ingestion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
ALLOWED_HOST = "data.gdeltproject.org"
USER_AGENT = "Geomacro-Public-Data-Runners/1.0"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MIN_GDELT_COLUMNS = 61
RUNNER_VERSION = "gdelt-public-artifact-v1.0.0"


class SnapshotError(RuntimeError):
    """Fail closed when the public-source contract is violated."""


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

    # Prefer TLS even when the upstream index advertises an http URL.
    return urlunparse(parsed._replace(scheme="https", query="", fragment=""))


def parse_lastupdate(text: str) -> str:
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

    if len(candidates) != 1:
        raise SnapshotError(
            f"expected exactly one latest Events export, found {len(candidates)}"
        )

    return candidates[0]


def fetch_lastupdate() -> str:
    request = Request(LASTUPDATE_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed allowlisted URL
        payload = response.read(256 * 1024)
    return payload.decode("utf-8", errors="strict")


def download(url: str, destination: Path) -> int:
    canonical = canonical_export_url(url)
    request = Request(canonical, headers={"User-Agent": USER_AGENT})
    written = 0

    destination.parent.mkdir(parents=True, exist_ok=True)

    with urlopen(request, timeout=120) as response:  # noqa: S310 - host validated above
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


def build_snapshot(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    index_text = fetch_lastupdate()
    source_url = parse_lastupdate(index_text)
    source_name = Path(urlparse(source_url).path).name

    temp_path = output_dir / f".{source_name}.download"
    final_zip = output_dir / "gdelt-latest.zip"
    manifest_path = output_dir / "gdelt-latest.manifest.json"
    checksum_path = output_dir / "gdelt-latest.sha256"

    try:
        byte_count = download(source_url, temp_path)
        validation = validate_export_zip(temp_path)
        digest = sha256_file(temp_path)
        shutil.move(str(temp_path), str(final_zip))
    finally:
        temp_path.unlink(missing_ok=True)

    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "runner_version": RUNNER_VERSION,
        "source_id": "gdelt_events",
        "publisher": "GDELT Project",
        "dataset": "GDELT Events 2.x",
        "source_url": source_url,
        "source_artifact_name": source_name,
        "fetched_at": utc_now(),
        "commercial_use_allowed": True,
        "redistribution_allowed": True,
        "raw_storage_allowed": True,
        "artifact": {
            "filename": final_zip.name,
            "sha256": digest,
            "bytes": byte_count,
            **validation,
        },
        "production_boundary": {
            "writes_production_database": False,
            "contains_private_credentials": False,
            "downstream_ingestion_required": True,
        },
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_path.write_text(f"{digest}  {final_zip.name}\n", encoding="utf-8")

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="dist/gdelt",
        help="Output directory for the rolling source artifact",
    )
    args = parser.parse_args(argv)

    try:
        manifest = build_snapshot(Path(args.output))
    except (OSError, ValueError, SnapshotError, zipfile.BadZipFile) as exc:
        print(f"GDELT_PUBLIC_SNAPSHOT_FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "GDELT_PUBLIC_SNAPSHOT_OK",
        manifest["artifact"],
        manifest["source_url"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
