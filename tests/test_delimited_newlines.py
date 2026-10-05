"""Delimited fields must retain their original CR, LF, and CRLF characters."""
import contextlib
import csv
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

from public_data_sentinel.cli import main, read_records
from public_data_sentinel.validation import validate


class DelimitedNewlineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_rows(self, suffix, rows, terminator='\r\n', bom=False):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, delimiter='\t' if suffix == '.tsv' else ',',
                            lineterminator=terminator, quoting=csv.QUOTE_ALL)
        writer.writerows(rows)
        data = stream.getvalue().encode('utf-8')
        path = self.root / ('records' + suffix)
        path.write_bytes((b'\xef\xbb\xbf' if bom else b'') + data)
        return path

    def test_quoted_field_newlines_are_not_normalized(self):
        for suffix in ('.csv', '.tsv'):
            for newline in ('\r', '\n', '\r\n'):
                with self.subTest(suffix=suffix, newline=repr(newline)):
                    value = 'before' + newline + 'after'
                    path = self.write_rows(suffix, [['id'], [value]])
                    records, headers = read_records(path)
                    self.assertEqual(headers, ['id'])
                    self.assertEqual(records, [{'id': value}])

    def test_quoted_header_newlines_are_not_normalized(self):
        for suffix in ('.csv', '.tsv'):
            with self.subTest(suffix=suffix):
                header = 'station\r\nname'
                path = self.write_rows(suffix, [[header], ['00123']])
                records, headers = read_records(path)
                self.assertEqual(headers, [header])
                self.assertEqual(records, [{header: '00123'}])
                report = validate(records, {'columns': {header: {'type': 'string'}}}, headers=headers)
                self.assertTrue(report['valid'])

    def test_newline_distinct_headers_do_not_become_duplicate_headers(self):
        headers = ['station\rname', 'station\nname', 'station\r\nname']
        for suffix in ('.csv', '.tsv'):
            with self.subTest(suffix=suffix):
                path = self.write_rows(suffix, [headers, ['1', '2', '3']])
                records, actual_headers = read_records(path)
                self.assertEqual(actual_headers, headers)
                self.assertEqual(records, [dict(zip(headers, ['1', '2', '3']))])

    def test_json_error_line_numbers_keep_existing_universal_newlines(self):
        path = self.root / 'malformed.json'
        path.write_bytes(b'{\r "key": 1,\r invalid\r}')
        with self.assertRaises(json.JSONDecodeError) as caught:
            read_records(path)
        self.assertEqual(caught.exception.lineno, 3)
        self.assertEqual(caught.exception.colno, 2)

    def test_newline_distinct_identifiers_do_not_become_duplicates(self):
        for suffix in ('.csv', '.tsv'):
            with self.subTest(suffix=suffix):
                identifiers = ['station\rname', 'station\nname', 'station\r\nname']
                path = self.write_rows(suffix, [['id'], *[[value] for value in identifiers]])
                records, headers = read_records(path)
                report = validate(records, {'columns': {'id': {'type': 'string'}}, 'unique_by': ['id']}, headers=headers)
                self.assertTrue(report['valid'], report)
                self.assertEqual(report['records_checked'], 3)

    def test_normalized_enum_decoy_is_not_accepted(self):
        for suffix in ('.csv', '.tsv'):
            with self.subTest(suffix=suffix):
                path = self.write_rows(suffix, [['id'], ['station\r\nname']])
                records, headers = read_records(path)
                report = validate(records, {'columns': {'id': {'type': 'string', 'enum': ['station\nname']}}}, headers=headers)
                self.assertFalse(report['valid'], report)
                self.assertEqual([issue['code'] for issue in report['issues']], ['enum'])

    def test_record_separators_are_supported_without_rewriting_fields(self):
        for suffix in ('.csv', '.tsv'):
            for separator in ('\r', '\n', '\r\n'):
                with self.subTest(suffix=suffix, separator=repr(separator)):
                    path = self.write_rows(suffix, [['id', 'name'], ['00123', '  가\r\n나  ']], separator, bom=True)
                    records, headers = read_records(path)
                    self.assertEqual(headers, ['id', 'name'])
                    self.assertEqual(records, [{'id': '00123', 'name': '  가\r\n나  '}])

    def test_ragged_record_after_multiline_field_reports_physical_line(self):
        for suffix, delimiter in (('.csv', ','), ('.tsv', '\t')):
            with self.subTest(suffix=suffix):
                path = self.root / ('ragged' + suffix)
                path.write_bytes(('id' + delimiter + 'name\r\n1' + delimiter + '"a\r\nb"\r\n2\r\n').encode())
                with self.assertRaisesRegex(ValueError, r'line 4: expected 2 fields, got 1'):
                    read_records(path)

    def test_ragged_multiline_record_reports_its_ending_line(self):
        for suffix, delimiter in (('.csv', ','), ('.tsv', '\t')):
            with self.subTest(suffix=suffix):
                path = self.root / ('ragged' + suffix)
                path.write_bytes(('id' + delimiter + 'name\n"a\nb"\n').encode())
                with self.assertRaisesRegex(ValueError, r'line 3: expected 2 fields, got 1'):
                    read_records(path)

    def test_seeded_quoted_fields_round_trip_exactly(self):
        randomizer = random.Random(20261004)
        alphabet = 'a0 가한é"\t,\r\n\u2028'
        rows = [['id', 'value']]
        for index in range(200):
            value = ''.join(randomizer.choice(alphabet) for _ in range(randomizer.randrange(1, 65)))
            rows.append([str(index), value])
        for suffix in ('.csv', '.tsv'):
            for separator in ('\r', '\n', '\r\n'):
                with self.subTest(suffix=suffix, separator=repr(separator)):
                    path = self.write_rows(suffix, rows, separator)
                    before = path.read_bytes()
                    records, headers = read_records(path)
                    self.assertEqual(headers, rows[0])
                    self.assertEqual(records, [dict(zip(rows[0], row)) for row in rows[1:]])
                    self.assertEqual(path.read_bytes(), before)

    def test_cli_rejects_newline_decoy_without_mutating_input_or_contract(self):
        path = self.write_rows('.csv', [['id'], ['station\r\nname']])
        contract = self.root / 'contract.json'
        contract.write_text(json.dumps({'columns': {'id': {'type': 'string', 'enum': ['station\nname']}}}), encoding='utf-8')
        before = {file: file.read_bytes() for file in (path, contract)}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main([str(path), '--contract', str(contract)])
        self.assertEqual(code, 1, stderr.getvalue())
        report = json.loads(stdout.getvalue())
        self.assertFalse(report['valid'])
        self.assertEqual(report['issues'][0]['code'], 'enum')
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual({file: file.read_bytes() for file in before}, before)


if __name__ == '__main__':
    unittest.main()
