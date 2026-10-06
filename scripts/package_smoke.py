"""Check the actual file-based Sentinel CLI, installed wheel and non-root image."""
import argparse
import csv
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
from markdown_it import MarkdownIt

class Cells(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values, self.current, self.forbidden = [], None, []
    def handle_starttag(self, tag, attrs):
        if tag in {"a", "em", "strong", "s", "code", "img", "script"}:
            self.forbidden.append(tag)
        if tag == "td":
            self.current = []
    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)
    def handle_endtag(self, tag):
        if tag == "td":
            self.values.append("".join(self.current))
            self.current = None

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--container")
mode.add_argument("--python")
parser.add_argument("--console")
args = parser.parse_args()
if args.python and not args.console:
    parser.error("--python requires --console")
root = Path.cwd().resolve()
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
clean_env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"}}
clean_env["PYTHONIOENCODING"] = "utf-8"
checks = []
with tempfile.TemporaryDirectory(prefix="sentinel-package-smoke-") as directory:
    temp = Path(directory)
    temp.chmod(0o755)
    field = " \t~~strike~~ **bold** _em_ " + chr(96) + "code" + chr(96) + " [link](https://example.test) <img> & | 한국어\nnext\t "
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([field])
    writer.writerow(["bad"])
    literal_tsv = io.StringIO(newline="")
    tsv_writer = csv.writer(literal_tsv, delimiter="\t")
    tsv_writer.writerow([field])
    tsv_writer.writerow(['bad\t"quoted"\nvalue'])
    def csv_to_tsv(name, encoding="utf-8"):
        converted = io.StringIO(newline="")
        csv.writer(converted, delimiter="\t").writerows(csv.reader(io.StringIO((root / "examples" / name).read_text(encoding="utf-8"))))
        return converted.getvalue().encode(encoding)
    unicode_field = "한글 👋"
    unicode_csv = io.StringIO(newline="")
    csv.writer(unicode_csv).writerows([[unicode_field], ["bad"]])
    payloads = {
        "malformed.json": b"[",
        "duplicate-key.json": b'[{"a":"1","a":"2"}]',
        "ragged.csv": b"a,b\n1\n",
        "duplicate-header.csv": b"a,a\n1,2\n",
        "valid.TSV": csv_to_tsv("valid.csv", "utf-8-sig"),
        "invalid.tsv": csv_to_tsv("invalid.csv"),
        "ragged.tsv": b"a\tb\n1\n",
        "duplicate-header.tsv": b"a\ta\n1\t2\n",
        "unterminated.tsv": b'a\tb\n1\t"unterminated\n',
        "literal.csv": buffer.getvalue().encode("utf-8"),
        "literal.tsv": literal_tsv.getvalue().encode("utf-8"),
        "literal.json": json.dumps([{field: "bad"}], ensure_ascii=False).encode("utf-8"),
        "unicode-👋.csv": unicode_csv.getvalue().encode("utf-8"),
        "unicode-👋-contract.json": json.dumps({"columns": {unicode_field: {"type": "integer"}}}, ensure_ascii=False).encode("utf-8"),
        "literal-contract.json": json.dumps({"columns": {field: {"type": "integer"}}}, ensure_ascii=False).encode("utf-8"),
        "outside-decimal-range.json": b'[{"amount":1e999999999999999999999999999999}]',
        "outside-decimal-range-contract.json": b'{"columns":{"amount":{"type":"decimal","minimum":1e999999999999999999999999999999}}}',
        "outside-decimal-range-envelope.json": b'{"items":[{"amount":1}],"metadata":1e999999999999999999999999999999}',
        "large-finite.json": b'[{"amount":1e400},{"amount":-1e-400}]',
        "decimal-contract.json": b'{"columns":{"amount":{"type":"decimal"}}}',
        "surrogate-field.json": b'[{"amount":1,"\\ud800":1}]',
        "selector-contract.json": b'{"columns":{"id":{"type":"string"}}}',
        "selector-array.json": b'[{"id":"001"}]',
        "selector-envelope.json": b'{"": [{"id":"001"}], "items": [{"id":"002"}]}',
        "selector-empty.json": b'{"": [], "items": [{"id":"001"}]}',
        "selector-missing.json": b'{"items": [{"id":"001"}]}',
        "selector-nonarray.json": b'{"": {}}',
        "selector.csv": b'id\n001\n',
        "selector.tsv": b'id\n001\n',
    }
    payloads["newline-unique-contract.json"] = json.dumps({
        "columns": {"id": {"type": "string"}}, "unique_by": ["id"]
    }).encode("utf-8")
    payloads["newline-enum-contract.json"] = json.dumps({
        "columns": {"id": {"type": "string", "enum": ["station\nname"]}}
    }).encode("utf-8")
    for suffix, delimiter in (("csv", ","), ("tsv", "\t")):
        content = io.StringIO(newline="")
        csv.writer(content, delimiter=delimiter).writerows([
            ["id"], ["station\rname"], ["station\nname"], ["station\r\nname"]
        ])
        payloads["newline-unique." + suffix] = content.getvalue().encode("utf-8")
        content = io.StringIO(newline="")
        csv.writer(content, delimiter=delimiter).writerows([["id"], ["station\r\nname"]])
        payloads["newline-enum." + suffix] = content.getvalue().encode("utf-8")
    for name, payload in payloads.items():
        (temp / name).write_bytes(payload)
        (temp / name).chmod(0o644)
    if args.container:
        base = ["docker", "run", "--rm", "--platform", "linux/amd64", "--read-only",
                "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
                "--mount", "type=bind,source=" + str(root) + ",target=/work,readonly",
                "--mount", "type=bind,source=" + str(temp) + ",target=/fixtures,readonly",
                "--workdir", "/work"]
        prefix = base + [args.container]
        fixture = lambda name: "/work/examples/" + name
        extra = lambda name: "/fixtures/" + name
    else:
        prefix = [str(Path(args.console).absolute())]
        fixture = lambda name: str(root / "examples" / name)
        extra = lambda name: str(temp / name)
    def run(arguments, expected, label, report=True):
        result = subprocess.run(prefix + arguments, cwd=temp, env=clean_env,
                                capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert result.returncode == expected, (label, expected, result.returncode, result.stdout, result.stderr)
        if expected == 2:
            assert result.stdout == "" and result.stderr.startswith("data-sentinel: "), (label, result.stdout, result.stderr)
            assert "Traceback" not in result.stderr, (label, result.stderr)
        checks.append(label)
        return json.loads(result.stdout) if report else result.stdout
    assert "contract" in run(["--help"], 0, "console-help", False)
    contract = fixture("contract.json")
    valid = run([fixture("valid.csv"), "--contract", contract], 0, "valid-csv")
    assert valid["valid"] and valid["records_checked"] == 3 and valid["error_count"] == 0
    invalid = run([fixture("invalid.csv"), "--contract", contract], 1, "data-violations")
    assert not invalid["valid"] and invalid["records_checked"] == 3 and invalid["error_count"] == 5
    valid_tsv = run([extra("valid.TSV"), "--contract", contract], 0, "valid-tsv-uppercase-and-bom")
    assert valid_tsv["valid"] and valid_tsv["records_checked"] == 3 and valid_tsv["error_count"] == 0
    invalid_tsv = run([extra("invalid.tsv"), "--contract", contract], 1, "tsv-data-violations")
    assert not invalid_tsv["valid"] and invalid_tsv["records_checked"] == 3 and invalid_tsv["error_count"] == 5
    for source, options, expected, label in (
        ("selector-array.json", [], 0, "json-absent-selector"),
        ("selector-envelope.json", ["--records-key", "items"], 0, "json-named-selector"),
        ("selector-envelope.json", ["--records-key", ""], 0, "json-empty-string-selector"),
        ("selector-empty.json", ["--records-key=",], 1, "json-empty-selected-array"),
    ):
        originals = {name: (temp / name).read_bytes() for name in (source, "selector-contract.json")}
        report = run([extra(source), "--contract", extra("selector-contract.json")] + options,
                     expected, label)
        assert report["valid"] == (expected == 0) and report["records_checked"] == (1 if expected == 0 else 0), report
        assert report["error_count"] == expected, report
        if expected == 1:
            assert report["issues"][0]["code"] == "empty_data", report
        assert {name: (temp / name).read_bytes() for name in originals} == originals
    for source, options in (
        ("selector-envelope.json", []),
        ("selector-array.json", ["--records-key", ""]),
        ("selector-missing.json", ["--records-key", ""]),
        ("selector-nonarray.json", ["--records-key", ""]),
        ("selector.csv", ["--records-key", ""]),
        ("selector.tsv", ["--records-key", ""]),
        ("selector.csv", ["--records-key", "items"]),
        ("selector.tsv", ["--records-key", "items"]),
    ):
        originals = {name: (temp / name).read_bytes() for name in (source, "selector-contract.json")}
        run([extra(source), "--contract", extra("selector-contract.json")] + options,
            2, source + "-selector-rejected-" + repr(options), False)
        assert {name: (temp / name).read_bytes() for name in originals} == originals
    for suffix in ("csv", "tsv"):
        source = temp / ("newline-unique." + suffix)
        before = source.read_bytes()
        report = run([extra(source.name), "--contract", extra("newline-unique-contract.json")],
                     0, suffix + "-newline-distinct-identifiers")
        assert report["valid"] and report["records_checked"] == 3, report
        assert source.read_bytes() == before
        source = temp / ("newline-enum." + suffix)
        before = source.read_bytes()
        report = run([extra(source.name), "--contract", extra("newline-enum-contract.json")],
                     1, suffix + "-newline-enum-decoy-rejected")
        assert report["error_count"] == 1 and report["issues"][0]["code"] == "enum", report
        assert source.read_bytes() == before
    for name in ("malformed.json", "duplicate-key.json", "ragged.csv", "duplicate-header.csv",
                 "ragged.tsv", "duplicate-header.tsv", "unterminated.tsv"):
        run([extra(name), "--contract", contract], 2, name, False)
    for source, rules, options, label in (
        ("outside-decimal-range.json", "decimal-contract.json", [], "json-out-of-range-input"),
        ("large-finite.json", "outside-decimal-range-contract.json", [], "json-out-of-range-contract"),
        ("outside-decimal-range-envelope.json", "decimal-contract.json", ["--records-key", "items"],
         "json-out-of-range-before-record-selection"),
    ):
        originals = {name: (temp / name).read_bytes() for name in (source, rules)}
        run([extra(source), "--contract", extra(rules)] + options, 2, label, False)
        assert {name: (temp / name).read_bytes() for name in originals} == originals
    finite = run([extra("large-finite.json"), "--contract", extra("decimal-contract.json")],
                 0, "json-large-finite-values")
    assert finite["valid"] and finite["records_checked"] == 2 and finite["error_count"] == 0, finite
    if args.python:
        for report_format in ("json", "markdown"):
            for exists in (False, True):
                output = temp / ("surrogate-" + report_format + "-" + str(exists))
                previous = b"previous report\x00\xff\n"
                if exists:
                    output.write_bytes(previous)
                run([extra("surrogate-field.json"), "--contract", extra("decimal-contract.json"),
                     "--format", report_format, "--output", str(output)], 2,
                    report_format + "-encoding-error-preserves-" + ("existing" if exists else "absent") + "-report", False)
                assert (output.read_bytes() == previous) if exists else not output.exists()
    run([extra("valid.TSV"), "--contract", contract, "--records-key", "items"], 2, "tsv-records-key-rejected", False)
    for name in ("literal.csv", "literal.tsv", "literal.json"):
        arguments = [extra(name), "--contract", extra("literal-contract.json")]
        report = run(arguments, 1, name + "-json-report")
        assert report["issues"][0]["field"] == field and report["error_count"] == 1, report
        markdown = run(arguments + ["--format", "markdown"], 1, name + "-rendered-markdown", False)
        rendered = Cells()
        rendered.feed(MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(markdown))
        assert not rendered.forbidden, rendered.forbidden
        assert rendered.values[1] == field.replace("\n", " "), rendered.values
        assert len(rendered.values) == 4, rendered.values
    unicode_arguments = [extra("unicode-👋.csv"), "--contract", extra("unicode-👋-contract.json")]
    unicode_sources = {name: (temp / name).read_bytes()
                       for name in ("unicode-👋.csv", "unicode-👋-contract.json")}
    legacy_env = dict(clean_env, PYTHONIOENCODING="cp949")
    for report_format in ("json", "markdown"):
        command = (base + ["--env", "PYTHONIOENCODING=cp949", args.container]
                   if args.container else prefix)
        result = subprocess.run(command + unicode_arguments + ["--format", report_format],
                                cwd=temp, env=legacy_env, capture_output=True, timeout=180)
        assert result.returncode == 1 and result.stderr == b"", (report_format, result.returncode, result.stderr)
        content = result.stdout.decode("utf-8")
        assert unicode_field.encode("utf-8") in result.stdout, result.stdout
        if report_format == "json":
            report = json.loads(content)
            assert not report["valid"] and report["error_count"] == 1 and report["records_checked"] == 1, report
            assert report["issues"][0]["field"] == unicode_field, report
        else:
            rendered = Cells()
            rendered.feed(MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(content))
            assert not rendered.forbidden and len(rendered.values) == 4, rendered.values
            assert rendered.values[1] == unicode_field, rendered.values
        for name, original in unicode_sources.items():
            assert (temp / name).read_bytes() == original, name
        checks.append(report_format + "-utf8-stdout-under-cp949")
    for name in ("literal.csv", "literal.tsv"):
        before = (temp / name).read_bytes()
        run([extra(name), "--contract", extra("literal-contract.json"),
             "--output", extra(name)], 2, name + "-input-overwrite-protection", False)
        assert (temp / name).read_bytes() == before
    code = "import json,os,sys,public_data_sentinel; from importlib.metadata import version; print(json.dumps({'path':public_data_sentinel.__file__,'prefix':sys.prefix,'version':version('public-data-sentinel'),'uid':getattr(os,'getuid',lambda:None)()}))"
    if args.container:
        command = base + ["--entrypoint", "python", args.container, "-I", "-c", code]
    else:
        command = [str(Path(args.python).absolute()), "-I", "-c", code]
    identity = subprocess.run(command, cwd=temp, env=clean_env, capture_output=True,
                              text=True, encoding="utf-8", timeout=30)
    assert identity.returncode == 0, identity.stderr
    imported = json.loads(identity.stdout)
    assert imported["version"] == version, imported
    if args.container:
        assert imported["uid"] == 10001 and imported["path"].startswith("/usr/local/lib/"), imported
        checks.append("non-root-and-mounted-source-independent-import")
    else:
        environment = str(Path(args.python).absolute().parents[1])
        assert os.path.commonpath([imported["path"], environment]) == environment, imported
        assert os.path.normcase(imported["prefix"]) == os.path.normcase(environment), imported
        checks.append("isolated-wheel-import-outside-source")
        result = subprocess.run([args.python, "-I", "-m", "public_data_sentinel.cli", "--help"],
                                cwd=temp, env=clean_env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and "contract" in result.stdout
        checks.append("module-help")
    checks.append("installed-package-version")
print(json.dumps({"mode": "container" if args.container else "wheel", "version": version,
                  "version_check": "installed package metadata; CLI has no --version",
                  "input_mode": "file paths; stdin is not supported",
                  "checks": checks, "passed": len(checks)}, indent=2))
