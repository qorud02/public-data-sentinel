"""Offline command line interface with machine-readable exit statuses."""

import argparse
import csv
import html
import io
import json
import pathlib
import sys

from .validation import ContractError, load_json, validate


def read_records(path, records_key=None):
    text = pathlib.Path(path).read_text(encoding="utf-8-sig")
    if pathlib.Path(path).suffix.lower() == ".csv":
        if records_key:
            raise ValueError("--records-key applies only to JSON")
        reader = csv.reader(io.StringIO(text), strict=True)
        headers = next(reader, None)
        if not headers:
            raise ValueError("CSV is empty")
        if any(not header for header in headers) or len(headers) != len(set(headers)):
            raise ValueError("CSV headers must be nonempty and unique")
        records = []
        for row_number, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"CSV line {row_number}: expected {len(headers)} fields, got {len(row)}")
            records.append(dict(zip(headers, row)))
        return records, headers
    if pathlib.Path(path).suffix.lower() != ".json":
        raise ValueError("Input filename must end in .csv or .json")
    records = load_json(text)
    if records_key:
        if not isinstance(records, dict) or records_key not in records:
            raise ValueError(f"JSON object does not contain records key {records_key!r}")
        records = records[records_key]
    if not isinstance(records, list):
        raise ValueError("JSON input must be an array of records (or use --records-key)")
    return records, None


def markdown(report):
    def escape(value):
        text = str(value if value is not None else "-")
        text = text.replace("\\", "\\\\")
        for char in ("`", "*", "_", "[", "]"):
            text = text.replace(char, f"\\{char}")
        return html.escape(text, quote=False).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")

    status = "PASS" if report["valid"] else "FAIL"
    lines = [f"# Data quality: {status}", "", f"Records checked: {report['records_checked']}", f"Issues: {report['error_count']}", ""]
    if report["issues"]:
        lines.extend(["| Record | Field | Code | Detail |", "| --- | --- | --- | --- |"])
        for item in report["issues"]:
            lines.append("| " + " | ".join(escape(item[key]) for key in ("record", "field", "code", "message")) + " |")
    return "\n".join(lines) + "\n"


def _resolve_path(path):
    try:
        return path.resolve()
    except RuntimeError as exc:
        raise ValueError(f"Cannot resolve path {path}: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a CSV or JSON extract against an explicit data contract.")
    parser.add_argument("input", help="UTF-8 CSV or JSON file")
    parser.add_argument("--contract", required=True, help="JSON contract file")
    parser.add_argument("--records-key", help="Top-level JSON key containing the record array")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Write the report to this path")
    args = parser.parse_args(argv)
    try:
        if args.output:
            output_path = pathlib.Path(args.output)
            for source_path in (pathlib.Path(args.input), pathlib.Path(args.contract)):
                if _resolve_path(output_path) == _resolve_path(source_path) or (
                    output_path.exists() and output_path.samefile(source_path)
                ):
                    raise ValueError("Report output must not overwrite the input or contract")
        contract = load_json(pathlib.Path(args.contract).read_text(encoding="utf-8-sig"))
        records, headers = read_records(args.input, args.records_key)
        report = validate(records, contract, headers=headers)
        output = markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            pathlib.Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        return 0 if report["valid"] else 1
    except (OSError, UnicodeError, ValueError, csv.Error, ContractError) as exc:
        print(f"data-sentinel: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
