# Contributing / 기여 안내

Contributions that make public-data checks easier to reproduce are welcome.
Start with [open issues](https://github.com/qorud02/public-data-sentinel/issues)
and [open pull requests](https://github.com/qorud02/public-data-sentinel/pulls)
so work does not overlap. For a feature, describe the input, intended behavior,
and proposed scope in its issue before starting. A comment coordinates work;
it does not reserve exclusive ownership.

한국어: 열린 이슈와 PR을 먼저 확인하고, 기능 변경은 입력 예시와 작업 범위를
이슈에서 공유해 주세요. 작업 의사 표시는 중복을 줄이기 위한 조율입니다.

## Work locally / 로컬 실행

Fork the repository on GitHub, clone your fork, and create a branch.
Replace YOUR_USERNAME below with your GitHub username. Run the remaining
commands from the repository root.

~~~sh
git clone https://github.com/YOUR_USERNAME/public-data-sentinel.git
cd public-data-sentinel
git switch -c my-change
python -m venv .venv
~~~

The project requires Python 3.10 or newer and has no runtime dependencies.
Editable installation uses the build requirements in pyproject.toml.
You do not need to activate the virtual environment.

Windows PowerShell:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m public_data_sentinel.cli examples/valid.csv --contract examples/contract.json
~~~

macOS / Linux:

~~~sh
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m public_data_sentinel.cli examples/valid.csv --contract examples/contract.json
~~~

For source-only CLI work with an existing Python installation, no installation
is needed. To run the tests, install the test-only renderer first:

~~~sh
python -m pip install markdown-it-py==4.2.0
~~~

The package uses a src/ layout, so set PYTHONPATH:

~~~powershell
# Windows PowerShell, from the repository root
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
python -m public_data_sentinel.cli examples/valid.csv --contract examples/contract.json
~~~

~~~sh
# macOS / Linux, from the repository root
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m public_data_sentinel.cli examples/valid.csv --contract examples/contract.json
~~~

한국어: Python 3.10 이상을 사용합니다. 가상환경을 활성화하지 않아도 위 명령을
실행할 수 있습니다. CLI 실행에는 외부 패키지가 필요하지 않습니다. 테스트에는
markdown-it-py 4.2.0을 설치하고, src를 PYTHONPATH에 넣어 주세요.

## Show the behavior / 변경 검증

Use small synthetic fixtures that anyone can run offline. Preserve text
identifiers such as 00123; do not substitute large downloads for a minimal
reproducer. Existing examples live in examples/, and tests use the standard
library's unittest in tests/test_validation.py.

For a runtime bug, show a regression failing on the unchanged base, then passing
with the fix. For a new feature, state what the base currently does and test the
proposed behavior; missing support is not automatically an existing bug.
Check existing behavior as well as the new case. Run:

~~~sh
python -m unittest discover -s tests -v
~~~

Use the virtual-environment Python or PYTHONPATH setup above. CLI exit codes
are 0 for passing data, 1 for data violations, and 2 for malformed or
unreadable input or an invalid contract. The passing CSV example returns 0
with three records and no issues. The invalid example returns 1 with five
issues. Keep source files unchanged during validation.

The tool supports CSV, TSV and JSON. Input-format ideas in issues remain
proposals until implemented and reviewed. Discuss compatibility, error
locations, and existing parser behavior when proposing a format change.

For documentation-only changes, check the changed links and example commands.
Do not add tests that only repeat wording or rerun the full suite solely for
prose edits.

한국어: 작은 합성 데이터로 재현하고, 버그 수정은 변경 전 실패와 변경 후 통과를
기록해 주세요. 새 기능은 기존 동작과 제안한 동작을 구분합니다. 문서만 바꾸는
경우에는 관련 링크와 명령을 확인하면 됩니다.

## Send a focused draft PR / 작은 Draft PR

Link the issue, explain the concrete behavior change, and list the commands
and results you actually ran. Keep unrelated refactoring separate. Open a
small draft PR while the implementation or design needs discussion; describe
remaining work so reviewers can assess it. Update relevant examples and
documentation when behavior changes.

The [test workflow](.github/workflows/tests.yml) runs on Windows and Linux with
Python 3.10, 3.12, and 3.14. Local results cover only the environment you ran;
check the hosted CI result before describing the full matrix as passing.

한국어: 관련 이슈, 실제 실행한 검증 명령·결과, 남은 작업을 Draft PR에 적어 주세요.
작업과 무관한 리팩터링을 섞지 말고, 동작이 바뀌면 예제와 문서도 갱신합니다.

## Distribution changes

Keep the example data, contributor instructions and verification scripts in the
source archive. Use Python 3.12 or later with the `test` extra and `build` installed,
then run `python -m build --outdir dist` and
`python scripts/verify_distribution.py dist`. This runs the extracted-source tests
and installed-wheel checks rather than relying only on the checkout. Keep fixture
contents synthetic, and add intended new source-archive inputs to `MANIFEST.in`
and the verifier's required-file list. Generated bytecode does not belong in
distributions.
