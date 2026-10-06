"""Verify source fixtures, extracted tests, and an isolated installed wheel."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
from zipfile import ZipFile


def run(command, cwd, *, source=None):
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                       PYTHONNOUSERSITE="1")
    if source is not None:
        environment["PYTHONPATH"] = str(source / "src")
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True,
                            text=True, encoding="utf-8", timeout=180)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}"
        )
    return result


def extract_source(archive_path, destination):
    """Extract regular files under one archive root, rejecting links and caches."""
    destination.mkdir()
    root_name = archive_path.name.removesuffix(".tar.gz")
    if root_name in {"", ".", ".."}:
        raise ValueError("Source archive needs a nonempty root name")
    root = destination / root_name
    seen = set()
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            target = (destination / member.name).resolve()
            if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(root.resolve()):
                raise ValueError(f"Archive entry escapes its source root: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported archive entry: {member.name}")
            if "__pycache__" in {part.casefold() for part in relative.parts} or relative.suffix.casefold() in {".pyc", ".pyo", ".pyd", ".so"}:
                raise ValueError(f"Generated Python file in source archive: {member.name}")
            if target in seen:
                raise ValueError(f"Duplicate archive entry: {member.name}")
            seen.add(target)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
    if not root.is_dir():
        raise ValueError("Expected one source archive root")
    return root


def required_source_files(checkout):
    required = {
        "CONTRIBUTING.md", "LICENSE", "MANIFEST.in", "README.md", "pyproject.toml",
        "ci/github-actions.yml", "examples/contract.json", "examples/valid.csv",
        "examples/invalid.csv", "examples/valid.tsv", "examples/valid-report.json",
        "examples/invalid-report.md", "scripts/verify_distribution.py",
        "scripts/verify_wheel.py", "scripts/package_smoke.py",
    }
    for folder in ("src/public_data_sentinel", "tests"):
        required.update(path.relative_to(checkout).as_posix()
                        for path in (checkout / folder).rglob("*.py"))
    return required


def verify_source_files(checkout, source):
    required = required_source_files(checkout)
    for path in source.rglob("*.py"):
        name = path.relative_to(source).as_posix()
        if name not in required:
            raise ValueError(f"Unexpected Python source in archive: {name}")
    for name in sorted(required):
        archived = source / name
        if not archived.is_file():
            raise ValueError(f"Source archive is missing {name}")
        if archived.read_bytes() != (checkout / name).read_bytes():
            raise ValueError(f"Source archive differs from checkout: {name}")
    return len(required)


def verify_wheel_members(wheel, checkout):
    """Reject extra installable payloads before any installed Python executes."""
    with ZipFile(wheel) as archive:
        names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Duplicate wheel member")
    metadata_roots = {name.split("/")[0] for name in names
                      if name.split("/")[0].endswith(".dist-info")}
    if len(metadata_roots) != 1:
        raise ValueError("Expected exactly one wheel metadata directory")
    metadata_root = metadata_roots.pop()
    allowed = {path.relative_to(checkout / "src").as_posix()
               for path in (checkout / "src/public_data_sentinel").rglob("*.py")}
    allowed.update(metadata_root + "/" + name for name in (
        "METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt",
        "LICENSE", "licenses/LICENSE",
    ))
    unexpected = set(names) - allowed
    if unexpected:
        raise ValueError("Unexpected wheel members: " + ", ".join(sorted(unexpected)))


def write_checksums(dist, archives):
    checksums = [hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
                 for path in archives]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dist,
                                         prefix=".SHA256SUMS-", delete=False) as output:
            temporary = Path(output.name)
            output.write("\n".join(checksums) + "\n")
        temporary.replace(dist / "SHA256SUMS")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return checksums


def verify_artifacts_unchanged(dist, archives):
    for archive in archives:
        if hashlib.sha256((dist / archive.name).read_bytes()).digest() != hashlib.sha256(archive.read_bytes()).digest():
            raise ValueError("Distribution changed during verification: " + archive.name)


def verify(dist):
    # A failed recheck must not leave a previous success marker behind.
    (dist / "SHA256SUMS").unlink(missing_ok=True)
    checkout = Path(__file__).resolve().parents[1]
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one source archive")
    wheel, sdist = wheels[0].resolve(), sdists[0].resolve()
    with tempfile.TemporaryDirectory(prefix="sentinel-distribution-") as temporary:
        temp = Path(temporary)
        # Verify immutable private copies, including the wheel that is installed.
        # The wheel verifier's intermediate checksum stays private here too.
        artifacts = temp / "artifacts"
        artifacts.mkdir()
        shutil.copyfile(wheel, artifacts / wheel.name)
        shutil.copyfile(sdist, artifacts / sdist.name)
        wheel, sdist = artifacts / wheel.name, artifacts / sdist.name
        source = extract_source(sdist, temp / "source")
        count = verify_source_files(checkout, source)
        verify_wheel_members(wheel, checkout)
        wheel_result = run([sys.executable, "scripts/verify_wheel.py", str(artifacts)], checkout)
        print(wheel_result.stdout.strip())
        import_result = run(
            [sys.executable, "-c", "import public_data_sentinel; print(public_data_sentinel.__file__)"],
            source, source=source,
        )
        if not Path(import_result.stdout.strip()).resolve().is_relative_to((source / "src").resolve()):
            raise ValueError("Source test import escaped the extracted archive")
        tests = run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                    source, source=source)
        print(tests.stderr.strip())
        result_count = re.search(r"Ran (\d+) tests? in", tests.stderr)
        if result_count is None or int(result_count.group(1)) == 0:
            raise ValueError("No extracted-source tests were recorded")
        environment = temp / "installed"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        command = scripts / ("data-sentinel.exe" if os.name == "nt" else "data-sentinel")
        outside = temp / "outside"
        outside.mkdir()
        run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], outside)
        smoke = run([sys.executable, "scripts/package_smoke.py", "--python", str(python),
                     "--console", str(command)], source)
        smoke_result = json.loads(smoke.stdout)
        if smoke_result.get("mode") != "wheel" or not smoke_result.get("passed"):
            raise ValueError("Installed-wheel smoke checks were not recorded")
        verify_artifacts_unchanged(dist, (wheel, sdist))
        checksums = write_checksums(dist, (wheel, sdist))
        print(json.dumps({
            "verified_source_files": count,
            "extracted_source_tests": int(result_count.group(1)),
            "source_import_from_archive": True,
            "installed_wheel": smoke_result,
            "artifacts": checksums,
        }, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/verify_distribution.py DIST_DIRECTORY")
    verify(Path(sys.argv[1]).resolve())
