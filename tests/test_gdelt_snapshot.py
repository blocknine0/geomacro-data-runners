import tempfile
import unittest
import zipfile
from pathlib import Path

from src.gdelt_snapshot import (
    SnapshotError,
    canonical_export_url,
    parse_lastupdate,
    validate_export_zip,
)


class GdeltSnapshotTests(unittest.TestCase):
    def test_parse_lastupdate_selects_events_export(self):
        text = "\n".join(
            [
                "123 abc http://data.gdeltproject.org/gdeltv2/20260916153000.export.CSV.zip",
                "456 def http://data.gdeltproject.org/gdeltv2/20260916153000.mentions.CSV.zip",
                "789 ghi http://data.gdeltproject.org/gdeltv2/20260916153000.gkg.csv.zip",
            ]
        )
        self.assertEqual(
            parse_lastupdate(text),
            "https://data.gdeltproject.org/gdeltv2/20260916153000.export.CSV.zip",
        )

    def test_export_url_rejects_untrusted_host(self):
        with self.assertRaises(SnapshotError):
            canonical_export_url(
                "https://example.com/20260916153000.export.CSV.zip"
            )

    def test_zip_validation_requires_gdelt_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.zip"
            row = [str(index) for index in range(61)]
            payload = "\t".join(row) + "\n" + "\t".join(row) + "\n"

            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("20260916153000.export.CSV", payload)

            result = validate_export_zip(path)
            self.assertEqual(result["row_count"], 2)
            self.assertEqual(result["min_columns"], 61)
            self.assertEqual(result["max_columns"], 61)

    def test_zip_validation_fails_short_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            payload = "\t".join(["x"] * 60) + "\n"

            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("20260916153000.export.CSV", payload)

            with self.assertRaises(SnapshotError):
                validate_export_zip(path)


if __name__ == "__main__":
    unittest.main()
