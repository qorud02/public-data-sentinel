"""Explicit JSON selectors distinguish an empty key from an absent option."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExplicitRecordsKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.contract = self.root / "contract.json"
        self.contract.write_text('{"columns":{"id":{"type":"string"}}}', encoding="utf-8")

    def run_cli(self, name, content, options=()):
        source = self.root / name
        source.write_bytes(content.encode("utf-8"))
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                           PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "public_data_sentinel.cli", str(source),
             "--contract", str(self.contract), *options],
            cwd=self.root, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )

    def test_explicit_empty_key_selects_valid_and_empty_record_arrays(self):
        for records, expected in (([{"id": "001"}], 0), ([], 1)):
            with self.subTest(records=records):
                result = self.run_cli(
                    "envelope.json", json.dumps({"": records, "other": [{"id": 123}]}),
                    ("--records-key", ""),
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertEqual(report["records_checked"], len(records))
                self.assertEqual(report["valid"], expected == 0)
                if not records:
                    self.assertEqual(report["issues"][0]["code"], "empty_data")

    def test_explicit_empty_key_rejects_missing_or_nonarray_selection(self):
        for content in ('[{"id":"001"}]', '{"items":[{"id":"001"}]}', '{"":{}}'):
            with self.subTest(content=content):
                result = self.run_cli("invalid-selection.json", content, ("--records-key", ""))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.startswith("data-sentinel: "))
                self.assertNotIn("Traceback", result.stderr)

    def test_delimited_inputs_reject_empty_and_named_selectors(self):
        for suffix in ("csv", "tsv"):
            for key in ("", "items"):
                with self.subTest(suffix=suffix, key=key):
                    result = self.run_cli("data." + suffix, "id\n001\n", ("--records-key", key))
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("--records-key applies only to JSON", result.stderr)

    def test_absent_and_named_selectors_keep_existing_behavior(self):
        cases = (
            ("array.json", '[{"id":"001"}]', ()),
            ("envelope.json", '{"items":[{"id":"001"}]}', ("--records-key", "items")),
            ("data.csv", "id\n001\n", ()),
            ("data.tsv", "id\n001\n", ()),
        )
        for name, content, options in cases:
            with self.subTest(name=name, options=options):
                result = self.run_cli(name, content, options)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertTrue(report["valid"])
                self.assertEqual(report["records_checked"], 1)
                self.assertEqual(report["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
