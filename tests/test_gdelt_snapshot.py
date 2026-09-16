import tempfile
import unittest
import zipfile
from pathlib import Path

from src.gdelt_snapshot import (
    SnapshotError,
    canonical_export_url,
    parse_master_tail,
    validate_export_zip,
)


class GdeltSnapshotTests(unittest.TestCase):
    def test_parse_master_tail_selects_latest_events_window(self):
        lines = []
        for minute in (0, 15, 30, 45):
            stamp = f"2026091615{minute:02d}00"
            lines.extend(
                [
                    f"123 abc http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip",
                    f"456 def http://data.gdeltproject.org/gdeltv2/{stamp}.mentions.CSV.zip",
                    f"789 ghi http://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip",
                ]
            )

        selected = parse_master_tail("\n".join(lines), 3)
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            selected[-1],
            "https://data.gdeltproject.org/gdeltv2/20260916154500.export.CSV.zip",
        )
        self.assertTrue(all(url.endswith(".export.CSV.zip") for url in selected))

    def test_parse_master_tail_fails_when_window_is_incomplete(self):
        text = (
            "123 abc "
            "https://data.gdeltproject.org/gdeltv2/20260916154500.export.CSV.zip"
        )
        with self.assertRaises(SnapshotError):
            parse_master_tail(text, 2)

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
