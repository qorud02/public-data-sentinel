"""Check src-layout wheel bytes, optional dependencies, CLI metadata and license."""
import configparser
from email.parser import Parser
import hashlib
from pathlib import Path
import sys
import tomllib
from zipfile import ZipFile

def check(condition, message):
    if not condition:
        raise SystemExit(message)

folder = Path(sys.argv[1])
wheels = list(folder.glob("public_data_sentinel-*.whl"))
check(len(wheels) == 1, "Expected exactly one public-data-sentinel wheel")
wheel = wheels[0]
project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
with ZipFile(wheel) as archive:
    names = archive.namelist()
    sources = {path.relative_to("src").as_posix(): path for path in Path("src/public_data_sentinel").rglob("*.py")}
    packaged = {name for name in names if name.startswith("public_data_sentinel/")}
    check(packaged == set(sources), "Wheel package file set differs from source")
    for name, source in sources.items():
        check(archive.read(name) == source.read_bytes(), "Wheel source differs: " + name)
    headers = Parser().parsestr(archive.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode("utf-8"))
    check(headers["Name"] == project["name"] and headers["Version"] == project["version"], "Wheel name or version differs")
    check(headers["Requires-Python"] == project["requires-python"], "Python requirement differs")
    expected = set(project.get("dependencies", []))
    for extra, dependencies in project.get("optional-dependencies", {}).items():
        expected.update(dependency + '; extra == "' + extra + '"' for dependency in dependencies)
    normalize = lambda value: value.replace(" ", "")
    check({normalize(value) for value in headers.get_all("Requires-Dist", [])} == {normalize(value) for value in expected},
          "Wheel dependency metadata differs")
    check(not project.get("dependencies"), "Runtime dependencies were added")
    entries = configparser.ConfigParser()
    entries.read_string(archive.read(next(name for name in names if name.endswith(".dist-info/entry_points.txt"))).decode("utf-8"))
    check(entries["console_scripts"]["data-sentinel"] == project["scripts"]["data-sentinel"], "Console entry point differs")
    license_name = next(name for name in names if name.endswith("/LICENSE"))
    check(archive.read(license_name) == Path("LICENSE").read_bytes(), "Wheel license differs")
digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
(folder / "SHA256SUMS").write_text(digest + "  " + wheel.name + "\n", encoding="utf-8")
print("Verified wheel " + wheel.name + " SHA256 " + digest)
