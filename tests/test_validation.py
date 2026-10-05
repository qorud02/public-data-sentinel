import copy
import csv
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from html.parser import HTMLParser

from markdown_it import MarkdownIt

from public_data_sentinel.cli import main, markdown, read_records
from public_data_sentinel.validation import ContractError, check_contract, load_json, validate


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = load_json((ROOT / "examples/contract.json").read_text())


def row(**updates):
    result = {"station_id": "00123", "date": "2026-09-01", "rainfall_mm": "12.5", "quality": "measured"}
    result.update(updates)
    return result


class IssueTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.cell_tags = []
        self.current_row = []
        self.current_cell = None

    def handle_starttag(self, tag, attrs):
        if self.current_cell is not None:
            self.cell_tags.append(tag)
        if tag == "tr":
            self.current_row = []
        elif tag == "td":
            self.current_cell = []

    def handle_data(self, data):
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.current_cell is not None:
            self.current_row.append("".join(self.current_cell))
            self.current_cell = None
        elif tag == "tr" and self.current_row:
            self.rows.append(self.current_row)


def assert_literal_issue_cells(test_case, markdown_text, issues):
    rendered = MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(markdown_text)
    parser = IssueTableParser()
    parser.feed(rendered)
    expected = [
        [
            str(issue[key] if issue[key] is not None else "-")
            .replace("\n", " ").replace("\r", " ")
            for key in ("record", "field", "code", "message")
        ]
        for issue in issues
    ]
    test_case.assertEqual(parser.rows, expected)
    test_case.assertEqual(parser.cell_tags, [])


class ValidationTests(unittest.TestCase):
    def test_valid_csv_and_identifiers_remain_text(self):
        records, headers = read_records(ROOT / "examples/valid.csv")
        self.assertEqual(records[0]["station_id"], "00123")
        self.assertTrue(validate(records, CONTRACT, headers=headers)["valid"])

    def test_example_has_five_precise_failures(self):
        records, headers = read_records(ROOT / "examples/invalid.csv")
        result = validate(records, CONTRACT, headers=headers)
        self.assertEqual(result["error_count"], 5)
        self.assertEqual({i["code"] for i in result["issues"]}, {"minimum", "type", "enum", "duplicate"})

    def test_input_is_not_modified(self):
        records = [row()]
        original = copy.deepcopy(records)
        validate(records, CONTRACT)
        self.assertEqual(original, records)

    def test_json_numeric_identifier_rejected(self):
        result = validate([row(station_id=123)], CONTRACT)
        self.assertEqual(result["issues"][0]["field"], "station_id")

    def test_invalid_numbers_rejected(self):
        for value in ["NaN", "Infinity", "-Infinity", True, "abc", [], {}]:
            with self.subTest(value=value):
                self.assertFalse(validate([row(rainfall_mm=value)], CONTRACT)["valid"])

    def test_limits_include_boundaries(self):
        for value in ["0", "1500"]:
            self.assertTrue(validate([row(rainfall_mm=value)], CONTRACT)["valid"])
        self.assertFalse(validate([row(rainfall_mm="1500.01")], CONTRACT)["valid"])

    def test_decimal_precision_is_retained(self):
        self.assertEqual(load_json('[0.10000000000000000001]')[0], Decimal("0.10000000000000000001"))

    def test_integer_accepts_whole_values_rejects_fraction_and_boolean(self):
        contract = {"columns": {"count": {"type": "integer"}}}
        self.assertTrue(validate([{"count": "2"}], contract)["valid"])
        for value in ["2.5", False]:
            self.assertFalse(validate([{"count": value}], contract)["valid"])

    def test_date_is_strict_and_calendar_aware(self):
        self.assertTrue(validate([row(date="2024-02-29")], CONTRACT)["valid"])
        for value in ["2026-02-29", "20260901", "2026-09-01T00:00:00Z"]:
            self.assertFalse(validate([row(date=value)], CONTRACT)["valid"])

    def test_missing_required_and_optional_fields(self):
        self.assertFalse(validate([row(station_id=" ")], CONTRACT)["valid"])
        contract = {"columns": {"note": {"type": "string", "required": False}}}
        self.assertTrue(validate([{}], contract)["valid"])

    def test_missing_csv_header_fails_even_on_empty_data(self):
        result = validate([], CONTRACT, headers=["station_id"])
        self.assertIn("missing_column", {issue["code"] for issue in result["issues"]})

    def test_extra_fields_fail_unless_enabled(self):
        self.assertFalse(validate([row(extra="x")], CONTRACT)["valid"])
        contract = dict(CONTRACT, allow_extra_columns=True)
        self.assertTrue(validate([row(extra="x")], contract)["valid"])

    def test_duplicate_composite_key_detected(self):
        result = validate([row(), row(), row(date="2026-09-02")], CONTRACT)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["issues"][0]["record"], 2)

    def test_numeric_unique_keys_compare_by_value(self):
        contract = {"columns": {"id": {"type": "integer"}}, "unique_by": ["id"]}
        self.assertEqual(validate([{"id": "1"}, {"id": "1.0"}], contract)["issues"][0]["code"], "duplicate")

    def test_invalid_key_does_not_create_duplicate(self):
        result = validate([row(station_id=None), row(station_id=None)], CONTRACT)
        self.assertNotIn("duplicate", {item["code"] for item in result["issues"]})

    def test_nonobject_record_and_empty_data(self):
        self.assertEqual(validate(["hello"], CONTRACT)["issues"][0]["code"], "record_type")
        self.assertEqual(validate([], CONTRACT)["issues"][0]["code"], "empty_data")

    def test_contract_typos_and_bad_limits_fail_early(self):
        invalid = [
            {}, {"columns": {}}, {"columns": {"id": {"type": "str"}}},
            {"columns": {"id": {"type": "string", "required": "yes"}}},
            {"columns": {"id": {"type": "string", "minimum": 0}}},
            {"columns": {"id": {"type": "decimal", "minimum": "NaN"}}},
            {"columns": {"id": {"type": "decimal", "minimum": 2, "maximum": 1}}},
            {"columns": {"id": {"type": "date", "enum": ["not-date"]}}},
            {"columns": {"id": {"type": "string"}}, "unique_by": ["other"]},
            {"columns": {"id": {"type": "string"}}, "unique_by": ["id", "id"]},
            {"columns": {"id": {"type": "string", "requried": True}}},
            {"columns": {"id": {"type": "string"}}, "allow_extra_columns": "yes"},
        ]
        for contract in invalid:
            with self.subTest(contract=contract):
                with self.assertRaises(ContractError):
                    check_contract(contract)

    def test_contract_container_type_values_fail_early(self):
        for kind in [[], {}]:
            with self.subTest(kind=kind):
                contract = {"columns": {"id": {"type": kind}}}
                with self.assertRaisesRegex(ContractError, "id: type must be string, integer, decimal or date"):
                    check_contract(contract)

    def test_json_duplicates_and_nonstandard_constants_fail(self):
        for value in ['{"id": 1, "id": 2}', '[NaN]', '[Infinity]']:
            with self.assertRaises(ValueError):
                load_json(value)

    def test_markdown_escapes_field_and_detail(self):
        issues = [{"record": 1, "field": "<b>|\nx", "code": "type", "message": "hello\rworld"}]
        text = markdown({"valid": False, "records_checked": 1, "error_count": 1, "issues": issues})
        self.assertIn("&lt;b&gt;&#124; x", text)
        self.assertNotIn("<b>", text)
        assert_literal_issue_cells(self, text, issues)

    def test_markdown_escapes_formatting_characters_in_field_and_detail(self):
        cases = [
            ("~~struck~~", r"\~\~struck\~\~"),
            ("~single~", r"\~single\~"),
            ("[station](https://example.invalid)", r"\[station\](https://example.invalid)"),
            ("**station_id**", r"\*\*station\_id\*\*"),
            ("`station_id`", r"\`station\_id\`"),
            ("_station_id_", r"\_station\_id\_"),
            (r"station\_id", r"station\\\_id"),
            ("station|name", "station&#124;name"),
            ("<b>station</b>", "&lt;b&gt;station&lt;/b&gt;"),
            ("정류장_id", r"정류장\_id"),
            ("station&id", "station&amp;id"),
            ("station\nname", "station name"),
            ("station\rname", "station name"),
            ("station\r\nname", "station  name"),
        ]
        issues = [
            {"record": idx + 1, "field": raw, "code": "type", "message": f"invalid {raw}"}
            for idx, (raw, _) in enumerate(cases)
        ]
        report = {
            "valid": False,
            "records_checked": len(cases),
            "error_count": len(cases),
            "issues": issues,
        }
        text = markdown(report)
        for raw, escaped in cases:
            with self.subTest(case=raw):
                self.assertIn(f"| {escaped} |", text)
                self.assertIn(f"| invalid {escaped} |", text)
        assert_literal_issue_cells(self, text, issues)

    def test_markdown_preserves_boundary_whitespace_in_fields_and_details(self):
        cases = [
            (" name ", "&#32;name&#32;"),
            ("  ", "&#32;&#32;"),
            ("\tname\t", "&#9;name&#9;"),
            (" \t ", "&#32;&#9;&#32;"),
            ("name\n", "name&#32;"),
            ("\nname", "&#32;name"),
            ("\u00a0name\u00a0", "&#160;name&#160;"),
            ("\u2003name\u2003", "&#8195;name&#8195;"),
            ("name\tpart", "name\tpart"),
            ("ordinary  words", "ordinary  words"),
            (" \nname\r\n ", "&#32;&#32;name&#32;&#32;&#32;"),
        ]
        issues = [
            {"record": idx + 1, "field": raw, "code": "type", "message": raw}
            for idx, (raw, _) in enumerate(cases)
        ]
        report = {"valid": False, "records_checked": len(cases), "error_count": len(cases), "issues": issues}
        text = markdown(report)
        for raw, escaped in cases:
            with self.subTest(case=raw):
                self.assertIn(f"| {escaped} | type | {escaped} |", text)
        assert_literal_issue_cells(self, text, issues)


class CLITests(unittest.TestCase):
    def test_tsv_bom_crlf_strings_and_suffixes(self):
        with tempfile.TemporaryDirectory() as temp:
            for suffix in (".tsv", ".TSV"):
                with self.subTest(suffix=suffix):
                    file = pathlib.Path(temp) / ("data" + suffix)
                    file.write_bytes("\ufeffid\tname\tcount\r\n00123\t 부산 \t2\r\n\r\n".encode("utf-8"))
                    records, headers = read_records(file)
                    self.assertEqual(headers, ["id", "name", "count"])
                    self.assertEqual(records, [{"id": "00123", "name": " 부산 ", "count": "2"}])

    def test_tsv_quoted_tabs_newlines_and_quotes(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.tsv"
            # Keep the intended LF fixture on Windows as well as POSIX.
            file.write_bytes('id\tnote\n00123\t"탭\t줄\n인용 ""문자"""\n'.encode("utf-8"))
            records, headers = read_records(file)
            self.assertEqual(headers, ["id", "note"])
            self.assertEqual(records, [{"id": "00123", "note": '탭\t줄\n인용 "문자"'}])

    def test_tsv_rejects_empty_and_duplicate_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.tsv"
            for content in ("", "\n", "id\tid\na\tb\n", "id\t\na\tb\n"):
                with self.subTest(content=content):
                    file.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "TSV"):
                        read_records(file)

    def test_tsv_rejects_short_long_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.tsv"
            for row_text in ("00123\n", "00123\tx\ty\n"):
                with self.subTest(row_text=row_text):
                    file.write_text("id\tname\n" + row_text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "TSV line 2"):
                        read_records(file)
            file.write_text('id\tname\n00123\t"unclosed\n', encoding="utf-8")
            with self.assertRaises(csv.Error):
                read_records(file)

    def test_tsv_records_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.tsv"
            file.write_text("id\n00123\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "--records-key applies only to JSON"):
                read_records(file, "items")

    def test_equivalent_csv_tsv_validation_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.tsv"
            for fixture in ("valid.csv", "invalid.csv"):
                with self.subTest(fixture=fixture):
                    original, csv_headers = read_records(ROOT / "examples" / fixture)
                    with file.open("w", newline="", encoding="utf-8") as handle:
                        writer = csv.writer(handle, delimiter="\t")
                        writer.writerow(csv_headers)
                        writer.writerows([record[column] for column in csv_headers] for record in original)
                    records, tsv_headers = read_records(file)
                    self.assertEqual(records, original)
                    self.assertEqual(validate(records, CONTRACT, headers=tsv_headers),
                                     validate(original, CONTRACT, headers=csv_headers))

    def test_tsv_cli_exit_codes_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            file, contract, output = root / "data.tsv", root / "contract.json", root / "report.json"
            contract.write_text(json.dumps({"columns": {"id": {"type": "string"}, "count": {"type": "integer"}}}), encoding="utf-8")
            contract_bytes = contract.read_bytes()
            for content, expected in (("id\tcount\n00123\t2\n", 0),
                                      ("id\tcount\n00123\tinvalid\n", 1),
                                      ("id\tcount\n00123\n", 2)):
                with self.subTest(expected=expected):
                    file.write_text(content, encoding="utf-8")
                    before = file.read_bytes()
                    self.assertEqual(main([str(file), "--contract", str(contract), "--output", str(output)]), expected)
                    self.assertEqual(file.read_bytes(), before)
                    self.assertEqual(contract.read_bytes(), contract_bytes)
                    if expected < 2:
                        report = json.loads(output.read_text(encoding="utf-8"))
                        self.assertEqual(set(report), {"valid", "records_checked", "error_count", "issues"})
                        self.assertEqual(report["valid"], expected == 0)
            file.write_text("id\tcount\n00123\t2\n", encoding="utf-8")
            self.assertEqual(main([str(file), "--contract", str(contract), "--records-key", "items"]), 2)

    def test_tsv_output_source_aliases_and_hardlinks_are_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            file, contract = root / "data.tsv", root / "contract.json"
            file.write_text("id\n00123\n", encoding="utf-8")
            contract.write_text(json.dumps({"columns": {"id": {"type": "string"}}}), encoding="utf-8")
            before = {source: source.read_bytes() for source in (file, contract)}
            for source in (file, contract):
                for output in (source, root / ".." / root.name / source.name):
                    with self.subTest(source=source.name, output=output):
                        self.assertEqual(main([str(file), "--contract", str(contract), "--output", str(output)]), 2)
                        self.assertEqual(source.read_bytes(), before[source])
                hardlink = root / (source.name + ".link")
                os.link(source, hardlink)
                self.assertEqual(main([str(file), "--contract", str(contract), "--output", str(hardlink)]), 2)
                self.assertEqual(hardlink.read_bytes(), before[source])
            self.assertEqual(file.read_bytes(), before[file])
            self.assertEqual(contract.read_bytes(), before[contract])

    def test_bom_csv_and_ragged_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.csv"
            file.write_text("\ufeffid,count\n001,2\n", encoding="utf-8")
            records, headers = read_records(file)
            self.assertEqual(records[0]["id"], "001")
            self.assertEqual(headers, ["id", "count"])
            file.write_text("id,count\n001\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                read_records(file)

    def test_duplicate_or_empty_csv_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.csv"
            for content in ["id,id\na,b\n", "id,\na,b\n"]:
                file.write_text(content)
                with self.assertRaises(ValueError):
                    read_records(file)

    def test_json_envelope_requires_explicit_key(self):
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / "data.json"
            file.write_text(json.dumps({"items": [row()]}))
            self.assertEqual(read_records(file, "items")[0][0]["station_id"], "00123")
            with self.assertRaises(ValueError):
                read_records(file)
            with self.assertRaises(ValueError):
                read_records(file, "missing")

    def test_cli_exit_codes_and_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "report.md"
            self.assertEqual(main([str(ROOT / "examples/valid.csv"), "--contract", str(ROOT / "examples/contract.json"), "--format", "markdown", "--output", str(output)]), 0)
            self.assertIn("Data quality: PASS", output.read_text())
            self.assertEqual(main([str(ROOT / "examples/invalid.csv"), "--contract", str(ROOT / "examples/contract.json"), "--output", str(output)]), 1)
            self.assertEqual(json.loads(output.read_text())["error_count"], 5)
            self.assertEqual(main([str(ROOT / "missing.csv"), "--contract", str(ROOT / "examples/contract.json")]), 2)

    def test_output_cannot_overwrite_source(self):
        source = ROOT / "examples/valid.csv"
        before = source.read_bytes()
        self.assertEqual(main([str(source), "--contract", str(ROOT / "examples/contract.json"), "--output", str(source)]), 2)
        self.assertEqual(before, source.read_bytes())

    def test_real_module_invocation(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [sys.executable, "-m", "public_data_sentinel.cli", str(ROOT / "examples/valid.csv"), "--contract", str(ROOT / "examples/contract.json")]
        process = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["records_checked"], 3)

    def test_cli_contract_container_type_values_exit_two(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        with tempfile.TemporaryDirectory() as temp:
            contract_file = pathlib.Path(temp) / "contract.json"
            for kind in [[], {}]:
                with self.subTest(kind=kind):
                    contract_file.write_text(json.dumps({"columns": {"id": {"type": kind}}}))
                    command = [sys.executable, "-m", "public_data_sentinel.cli", str(ROOT / "examples/valid.csv"), "--contract", str(contract_file)]
                    process = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8")
                    self.assertEqual(process.returncode, 2, process.stderr)
                    self.assertEqual(process.stdout, "")
                    self.assertEqual(process.stderr, "data-sentinel: id: type must be string, integer, decimal or date\n")

    def test_markdown_report_preserves_contract_columns_in_csv_and_json(self):
        cases = [
            ("~~struck~~", r"\~\~struck\~\~"),
            ("~single~", r"\~single\~"),
            ("[station](https://example.invalid)", r"\[station\](https://example.invalid)"),
            ("**station_id**", r"\*\*station\_id\*\*"),
            ("`station_id`", r"\`station\_id\`"),
            ("_station_id_", r"\_station\_id\_"),
            (r"station\_id", r"station\\\_id"),
            ("station|name", "station&#124;name"),
            ("<b>station</b>", "&lt;b&gt;station&lt;/b&gt;"),
            ("정류장_id", r"정류장\_id"),
            ("station&id", "station&amp;id"),
            ("station\nname", "station name"),
            ("~~station|<b>\nname~~", r"\~\~station&#124;&lt;b&gt; name\~\~"),
            (" name ", "&#32;name&#32;"),
            ("  ", "&#32;&#32;"),
            ("\tname\t", "&#9;name&#9;"),
            (" \t ", "&#32;&#9;&#32;"),
            ("name\n", "name&#32;"),
            ("\nname", "&#32;name"),
            ("\u00a0name\u00a0", "&#160;name&#160;"),
            ("\u2003name\u2003", "&#8195;name&#8195;"),
            ("name\tpart", "name\tpart"),
            ("ordinary  words", "ordinary  words"),
            (" \nname\n ", "&#32;&#32;name&#32;&#32;"),
        ]
        names = [raw for raw, _ in cases]
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            contract = root / "contract.json"
            contract.write_text(json.dumps({
                "columns": {name: {"type": "integer"} for name in names}
            }), encoding="utf-8")

            # JSON input
            records_json = root / "records.json"
            records_json.write_text(json.dumps([dict.fromkeys(names, "invalid")]), encoding="utf-8")
            source_bytes = records_json.read_bytes()
            contract_bytes = contract.read_bytes()
            report_json_md = root / "report_json.md"
            self.assertEqual(main([str(records_json), "--contract", str(contract), "--format", "markdown", "--output", str(report_json_md)]), 1)
            json_md = report_json_md.read_text(encoding="utf-8")
            for _, escaped in cases:
                self.assertIn(escaped, json_md)
            self.assertEqual(records_json.read_bytes(), source_bytes)
            self.assertEqual(contract.read_bytes(), contract_bytes)

            json_report = root / "report.json"
            self.assertEqual(main([str(records_json), "--contract", str(contract), "--output", str(json_report)]), 1)
            report = json.loads(json_report.read_text(encoding="utf-8"))
            self.assertEqual(set(report), {"valid", "records_checked", "error_count", "issues"})
            self.assertEqual([issue["field"] for issue in report["issues"]], names)
            self.assertEqual(records_json.read_bytes(), source_bytes)
            self.assertEqual(contract.read_bytes(), contract_bytes)
            assert_literal_issue_cells(self, json_md, report["issues"])

            # CSV input
            records_csv = root / "records.csv"
            with open(records_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(names)
                writer.writerow(["invalid"] * len(names))
            source_bytes = records_csv.read_bytes()
            report_csv_md = root / "report_csv.md"
            self.assertEqual(main([str(records_csv), "--contract", str(contract), "--format", "markdown", "--output", str(report_csv_md)]), 1)
            csv_md = report_csv_md.read_text(encoding="utf-8")
            for _, escaped in cases:
                self.assertIn(escaped, csv_md)
            self.assertEqual(records_csv.read_bytes(), source_bytes)
            self.assertEqual(contract.read_bytes(), contract_bytes)
            assert_literal_issue_cells(self, csv_md, report["issues"])


if __name__ == "__main__":
    unittest.main()
