"""Report encoding failures must leave existing files and absent targets intact."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from public_data_sentinel.cli import main


class ReportEncodingPreservationTests(unittest.TestCase):
    def test_lone_surrogate_in_contract_preserves_existing_report(self):
        for report_format in ('json', 'markdown'):
            with self.subTest(report_format=report_format), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, contract, output = [root / name for name in ('data.json', 'contract.json', 'report.txt')]
                source.write_text('[{"id":"ok"}]', encoding='utf-8')
                contract.write_text('{"columns":{"id":{"type":"string"},"\\ud800":{"type":"string"}}}', encoding='utf-8')
                output.write_bytes(b'previous report\x00\xff\n')
                before = {path: path.read_bytes() for path in (source, contract, output)}
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main([str(source), '--contract', str(contract), '--format', report_format,
                                 '--output', str(output)])
                self.assertEqual(code, 2, stderr.getvalue())
                self.assertEqual(stdout.getvalue(), '')
                self.assertIn('surrogates not allowed', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_lone_surrogate_failure_preserves_existing_or_absent_report(self):
        for report_format in ('json', 'markdown'):
            for output_exists in (False, True):
                for surrogate in ('\\ud800', '\\udfff'):
                    with self.subTest(report_format=report_format, output_exists=output_exists,
                                      surrogate=surrogate), tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        source, contract, output = [root / name for name in ('data.json', 'contract.json', 'report.txt')]
                        source.write_text('[{"id":"ok","' + surrogate + '":1}]', encoding='utf-8')
                        contract.write_text('{"columns":{"id":{"type":"string"}}}', encoding='utf-8')
                        if output_exists:
                            output.write_bytes(b'previous successful report\n')
                        source_before = {path: path.read_bytes() for path in (source, contract)}
                        report_before = output.read_bytes() if output_exists else None
                        stdout, stderr = io.StringIO(), io.StringIO()
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = main([str(source), '--contract', str(contract), '--format', report_format,
                                         '--output', str(output)])
                        self.assertEqual(code, 2, stderr.getvalue())
                        self.assertEqual(stdout.getvalue(), '')
                        self.assertIn('surrogates not allowed', stderr.getvalue())
                        self.assertNotIn('Traceback', stderr.getvalue())
                        self.assertEqual({path: path.read_bytes() for path in source_before}, source_before)
                        if output_exists:
                            self.assertEqual(output.read_bytes(), report_before)
                        else:
                            self.assertFalse(output.exists())

    def test_valid_surrogate_pair_still_produces_a_report(self):
        for report_format in ('json', 'markdown'):
            with self.subTest(report_format=report_format), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, contract, output = [root / name for name in ('data.json', 'contract.json', 'report.txt')]
                source.write_text('[{"id":"ok","\\ud83d\\udc4b":1}]', encoding='utf-8')
                contract.write_text('{"columns":{"id":{"type":"string"}}}', encoding='utf-8')
                output.write_bytes(b'previous successful report\n')
                before = {path: path.read_bytes() for path in (source, contract)}
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main([str(source), '--contract', str(contract), '--format', report_format,
                                 '--output', str(output)])
                self.assertEqual(code, 1, stderr.getvalue())
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(stderr.getvalue(), '')
                text = output.read_text(encoding='utf-8')
                self.assertIn('\U0001f44b', text)
                if report_format == 'json':
                    report = json.loads(text)
                    self.assertFalse(report['valid'])
                    self.assertEqual(report['issues'][0]['field'], '\U0001f44b')
                self.assertEqual({path: path.read_bytes() for path in before}, before)


if __name__ == '__main__':
    unittest.main()
