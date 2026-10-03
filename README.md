# Public Data Sentinel

[![Tests](https://github.com/qorud02/public-data-sentinel/actions/workflows/tests.yml/badge.svg)](https://github.com/qorud02/public-data-sentinel/actions/workflows/tests.yml)

Catch malformed public-data extracts before they enter a spreadsheet, report, or monitoring workflow.

A small Python CLI validates CSV, TSV and JSON files against an explicit data contract. It reports the record and field that failed, preserves text identifiers such as `00123`, and produces JSON or Markdown suitable for a review or CI job.

**For:** analysts, small research teams, and business operators working with recurring public-data downloads.

## Install

Python 3.10 or newer. The application has no runtime dependencies.

```sh
git clone https://github.com/qorud02/public-data-sentinel.git
cd public-data-sentinel
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m public_data_sentinel.cli examples/valid.csv --contract examples/contract.json
.\.venv\Scripts\python.exe -m public_data_sentinel.cli examples/invalid.csv --contract examples/contract.json --format markdown --output report.md
```

macOS / Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m public_data_sentinel.cli examples/valid.csv --contract examples/contract.json
.venv/bin/python -m public_data_sentinel.cli examples/invalid.csv --contract examples/contract.json --format markdown --output report.md
```

These commands use the virtual environment directly; activation is optional.

The valid-data command returns exit code **0** with three records checked and no issues. The invalid-data command returns **1** and writes five issues: a negative measurement, a nonfinite number, an unsupported quality value, a duplicate station/date key, and an impossible calendar date.

See the committed [passing JSON report](examples/valid-report.json) and [failing Markdown report](examples/invalid-report.md). The examples use synthetic CSV, TSV and JSON fixtures.

Input example:

```csv
station_id,date,rainfall_mm,quality
00123,2026-09-01,12.5,measured
00123,2026-09-02,0,estimated
```

Contract:

```json
{
  "columns": {
    "station_id": {"type": "string"},
    "date": {"type": "date"},
    "rainfall_mm": {"type": "decimal", "minimum": "0"},
    "quality": {"type": "string", "enum": ["measured", "estimated"]}
  },
  "unique_by": ["station_id", "date"],
  "allow_extra_columns": false
}
```

Columns are required by default; set `"required": false` for an optional field. Supported types are `string`, `integer`, `decimal`, and `date` (`YYYY-MM-DD`). Numeric bounds are inclusive. The composite key is checked only when all its components have type-valid, nonmissing values. Unknown contract keys fail early so a misspelled rule cannot silently weaken a check.

JSON arrays work directly. For an API-style envelope such as `{"items": [...]}`, add `--records-key items`. Numeric JSON identifiers fail a string contract instead of silently losing leading zeroes. UTF-8 BOM files are supported; duplicate headers, duplicate JSON keys, ragged CSV/TSV rows, and nonstandard JSON numbers are rejected.

## Tab-delimited extracts

Use the same contract directly with a tab-delimited `.tsv` file:

```sh
data-sentinel examples/valid.tsv --contract examples/contract.json
```

The example returns `0` with three records and no issues. `.TSV` is also accepted. The reader uses a fixed tab delimiter, keeps leading zeroes and surrounding whitespace, and supports quoted tabs and newlines. CSV uses commas. `--records-key` applies to JSON inputs.

## Automation and Python use

Exit statuses: **0** = passed, **1** = data violations, **2** = unreadable input, malformed file, or invalid contract. Output cannot overwrite the input or contract, including resolved path aliases and existing hard links to either file.

```python
from public_data_sentinel import validate

report = validate(
    [{"station_id": "00123", "count": "2"}],
    {"columns": {"station_id": {"type": "string"}, "count": {"type": "integer", "minimum": 0}}},
)
assert report["valid"]
```

For test commands and source-only execution, see the [contributor guide](CONTRIBUTING.md).

GitHub Actions tests Windows and Linux with Python 3.10, 3.12, and 3.14. See the [workflow](.github/workflows/tests.yml). Source-only execution is also possible by adding `src` to `PYTHONPATH` and running `python -m public_data_sentinel.cli`.

## Scope and limitations

- This checks the contract you provide. Passing does not establish factual accuracy, provenance, publication permission, or scientific validity.
- The contract is deliberately small; it is not JSON Schema and does not support nested field paths, joins, timezone conversions, or arbitrary business formulas.
- Files are read into memory. Use a streaming validator for very large datasets.
- Decimal values are checked without binary floating-point rounding. The tool reports errors and does not repair or overwrite the source data.

## Contributing

Small synthetic fixtures and clearer validation diagnostics make useful contributions. [TSV support #2](https://github.com/qorud02/public-data-sentinel/issues/2) records the input compatibility requirements.

Start with the [contributor guide](CONTRIBUTING.md) for local setup, reproducible fixtures, and focused draft PRs. Check [open issues](https://github.com/qorud02/public-data-sentinel/issues) and [existing PRs](https://github.com/qorud02/public-data-sentinel/pulls) before starting.

한국어: [기여 안내](CONTRIBUTING.md)에서 실행·검증 방법을 확인하고, 열린 이슈와 PR에서 작업 범위를 조율해 주세요.

## Development

Executable examples and regression tests document the tool's behavior.

한국어: 공공 데이터 CSV·TSV·JSON을 분석이나 보고서에 넣기 전에 필수 값, 숫자 범위, 날짜, 중복 키를 점검하는 도구입니다. 기관 코드의 앞자리 0을 보존하고 오류 위치를 표시합니다.

MIT license.

## Container and wheel

The Linux amd64 image includes Python 3.12 and an installed Sentinel wheel. From this repository directory:

```sh
docker run --rm --platform linux/amd64 --network none --read-only --mount "type=bind,source=${PWD},target=/work,readonly" ghcr.io/qorud02/public-data-sentinel:0.2.0 /work/examples/valid.csv --contract /work/examples/contract.json
```

Use the same command in PowerShell with Docker Desktop configured for Linux containers. It exits 0 with three records checked and no issues. Replace valid.csv with invalid.csv to report five data violations and exit 1.

Check files in your current directory:

```sh
docker run --rm --platform linux/amd64 --network none --read-only --mount "type=bind,source=${PWD},target=/work,readonly" ghcr.io/qorud02/public-data-sentinel:0.2.0 /work/extract.json --contract /work/contract.json --format markdown
```

Inputs and contracts use file paths inside the container. The tool runs as UID 10001 and prints the report on stdout. Keep mounted files readable by that user; the mounted source stays read-only.

Download public_data_sentinel-0.2.0-py3-none-any.whl and SHA256SUMS from the [release](https://github.com/qorud02/public-data-sentinel/releases/tag/v0.2.0), then install:

```sh
python -m pip install --no-index --no-deps ./public_data_sentinel-0.2.0-py3-none-any.whl
data-sentinel --help
data-sentinel examples/valid.csv --contract examples/contract.json
python -c "from importlib.metadata import version; print(version('public-data-sentinel'))"
```

The wheel supports Python 3.10 or newer and has no runtime dependencies. The final command reads the installed distribution version. The [package workflow](.github/workflows/package.yml) verifies the image, wheel contents, installed command, and rendered Markdown before pushing the image.
