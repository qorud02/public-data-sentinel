import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from public_data_sentinel.cli import main


ROOT = pathlib.Path(__file__).resolve().parents[1]


class Utf8StdoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = pathlib.Path(temp.name)
        self.source = self.folder / "extract-👋.csv"
        self.contract = self.folder / "contract-👋.json"
        self.source.write_text('"한글 👋"\nnot-an-integer\n', encoding="utf-8")
        self.contract.write_text(json.dumps({"columns": {"한글 👋": {"type": "integer"}}},
                                           ensure_ascii=False), encoding="utf-8")
        self.before = {path: path.read_bytes() for path in (self.source, self.contract)}

    def arguments(self, report_format):
        return [str(self.source), "--contract", str(self.contract), "--format", report_format]

    def real_cli(self, report_format, output=None):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp949"
        env["PYTHONPATH"] = str(ROOT / "src")
        args = [sys.executable, "-m", "public_data_sentinel.cli", *self.arguments(report_format)]
        if output is not None:
            args.extend(["--output", str(output)])
        return subprocess.run(args, cwd=self.folder, env=env, capture_output=True, timeout=30)

    def assert_sources_unchanged(self):
        for path, contents in self.before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_json_pipe_preserves_unicode_and_data_violation_exit_under_cp949(self):
        run = self.real_cli("json")
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(run.stderr, b"")
        report = json.loads(run.stdout.decode("utf-8"))
        self.assertFalse(report["valid"])
        self.assertEqual(report["issues"][0]["field"], "한글 👋")
        self.assertEqual(report["issues"][0]["code"], "type")
        self.assert_sources_unchanged()

    def test_markdown_pipe_preserves_unicode_and_data_violation_exit_under_cp949(self):
        run = self.real_cli("markdown")
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(run.stderr, b"")
        content = run.stdout.decode("utf-8")
        self.assertIn("# Data quality: FAIL\n", content)
        self.assertIn("| 한글 👋 | type |", content)
        self.assert_sources_unchanged()

    def test_output_file_keeps_utf8_and_matches_stdout_text_under_cp949(self):
        for report_format in ("json", "markdown"):
            with self.subTest(report_format=report_format):
                target = self.folder / (report_format + "-👋.txt")
                stdout_run = self.real_cli(report_format)
                file_run = self.real_cli(report_format, target)
                self.assertEqual((file_run.returncode, file_run.stdout, file_run.stderr), (1, b"", b""))
                content = target.read_text(encoding="utf-8")
                self.assertIn("한글 👋", content)
                self.assertEqual(stdout_run.returncode, 1, stdout_run.stderr)
                self.assertEqual(stdout_run.stdout.decode("utf-8"), content)
                self.assert_sources_unchanged()

    def test_StringIO_capture_preserves_unicode_and_exit_status(self):
        for report_format in ("json", "markdown"):
            with self.subTest(report_format=report_format):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = main(self.arguments(report_format))
                self.assertEqual(status, 1)
                self.assertEqual(stderr.getvalue(), "")
                self.assertIn("한글 👋", stdout.getvalue())
                self.assert_sources_unchanged()

    def test_stdout_flush_error_has_output_error_exit_status(self):
        class BrokenBuffer(io.BytesIO):
            def flush(self):
                raise BrokenPipeError("synthetic output flush failure")

        class CapturedStdout(io.StringIO):
            def __init__(self):
                super().__init__()
                self.buffer = BrokenBuffer()

        stdout, stderr = CapturedStdout(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(self.arguments("json"))
        self.assertEqual(status, 2)
        self.assertIn("data-sentinel: synthetic output flush failure", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assert_sources_unchanged()


if __name__ == "__main__":
    unittest.main()
