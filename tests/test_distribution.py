"""Archive verification checks real bytes and rejects unsafe/generated entries."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sentinel_distribution_verifier', ROOT / 'scripts/verify_distribution.py')
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.archive = self.folder / 'sample.tar.gz'

    def write_archive(self, entries):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, value in entries:
                member = tarfile.TarInfo(name)
                if isinstance(value, dict):
                    member.type = value["type"]
                    if member.type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                        member.linkname = '../outside'
                    archive.addfile(member)
                else:
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))

    def copied_source(self):
        source = self.folder / 'copy'
        for name in verifier.required_source_files(ROOT):
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return source

    def test_regular_archive_contents_are_preserved(self):
        body = 'id,name\n00123,서울\n'.encode()
        self.write_archive([('sample', {'type': tarfile.DIRTYPE}), ('sample/examples/data.csv', body)])
        source = verifier.extract_source(self.archive, self.folder / 'extracted')
        self.assertEqual(source.name, 'sample')
        self.assertEqual((source / 'examples/data.csv').read_bytes(), body)

    def test_archive_path_escapes_are_rejected(self):
        for name in ('../escaped', '/absolute', 'sample/../escaped', 'other-root/data.csv'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.folder) as temporary:
                self.write_archive([(name, b'bad')])
                with self.assertRaisesRegex(ValueError, 'escapes'):
                    verifier.extract_source(self.archive, Path(temporary) / 'extracted')
                self.assertFalse((self.folder / 'escaped').exists())

    def test_links_and_special_files_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.folder) as temporary:
                self.write_archive([('sample/link', {'type': kind})])
                with self.assertRaisesRegex(ValueError, 'Unsupported archive entry'):
                    verifier.extract_source(self.archive, Path(temporary) / 'extracted')

    def test_generated_cache_entries_are_rejected(self):
        for name in ('sample/__pycache__', 'sample/test.pyc', 'sample/test.pyo', 'sample/test.pyd',
                     'sample/__PYCACHE__', 'sample/test.PYC', 'sample/test.PYD',
                     'sample/src/public_data_sentinel/cli.cpython-312-x86_64-linux-gnu.so'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.folder) as temporary:
                self.write_archive([(name, b'generated')])
                with self.assertRaisesRegex(ValueError, 'Generated Python file'):
                    verifier.extract_source(self.archive, Path(temporary) / 'extracted')

    def test_duplicate_archive_members_are_rejected(self):
        self.write_archive([('sample/data.csv', b'first'), ('sample/data.csv', b'second')])
        with self.assertRaisesRegex(ValueError, 'Duplicate archive entry'):
            verifier.extract_source(self.archive, self.folder / 'extracted')

    def test_empty_archive_is_rejected(self):
        self.write_archive([])
        with self.assertRaisesRegex(ValueError, 'one source archive root'):
            verifier.extract_source(self.archive, self.folder / 'extracted')

    def test_nonempty_root_name_is_required(self):
        self.archive = self.folder / '.tar.gz'
        self.write_archive([('data.csv', b'bad')])
        with self.assertRaisesRegex(ValueError, 'nonempty root name'):
            verifier.extract_source(self.archive, self.folder / 'extracted')

    def test_missing_fixture_is_rejected(self):
        source = self.copied_source()
        (source / 'examples/contract.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing examples/contract.json'):
            verifier.verify_source_files(ROOT, source)

    def test_fixture_byte_mismatch_is_rejected(self):
        source = self.copied_source()
        (source / 'examples/valid.csv').write_bytes(b'changed fixture')
        with self.assertRaisesRegex(ValueError, 'differs from checkout: examples/valid.csv'):
            verifier.verify_source_files(ROOT, source)

    def test_unchecked_python_source_is_rejected_before_execution(self):
        source = self.copied_source()
        (source / 'sitecustomize.py').write_text('raise RuntimeError("unexpected code")')
        with self.assertRaisesRegex(ValueError, 'Unexpected Python source in archive: sitecustomize.py'):
            verifier.verify_source_files(ROOT, source)

    def test_complete_source_copy_is_verified(self):
        source = self.copied_source()
        self.assertGreater(verifier.verify_source_files(ROOT, source), 20)

    def test_command_failure_is_not_accepted(self):
        with self.assertRaisesRegex(RuntimeError, r'Command failed \(7\)'):
            verifier.run([sys.executable, '-c', 'raise SystemExit(7)'], self.folder)

    def test_commands_use_explicit_source_and_clean_environment(self):
        code = ('import json,os,sys; print(json.dumps({'
                '"path":os.environ.get("PYTHONPATH"),'
                '"home":os.environ.get("PYTHONHOME"),'
                '"optimize":sys.flags.optimize}))')
        with patch.dict(os.environ, {'PYTHONPATH': 'wrong-source', 'PYTHONHOME': 'wrong-home', 'PYTHONOPTIMIZE': '2'}):
            clean = verifier.run([sys.executable, '-c', code], self.folder)
            source = verifier.run([sys.executable, '-c', code], self.folder, source=ROOT)
        self.assertEqual(json.loads(clean.stdout), {'path': None, 'home': None, 'optimize': 0})
        self.assertEqual(json.loads(source.stdout), {'path': str(ROOT / 'src'), 'home': None, 'optimize': 0})

    def test_failed_artifact_selection_removes_stale_checksums(self):
        checksums = self.folder / 'SHA256SUMS'
        checksums.write_text('stale verification result\n')
        with self.assertRaisesRegex(ValueError, 'exactly one wheel and one source archive'):
            verifier.verify(self.folder)
        self.assertFalse(checksums.exists())

    def test_failed_archive_verification_removes_stale_checksums(self):
        checksums = self.folder / 'SHA256SUMS'
        checksums.write_text('stale verification result\n')
        (self.folder / 'sample.whl').write_bytes(b'not yet checked')
        self.write_archive([('sample/../escaped', b'bad')])
        with self.assertRaisesRegex(ValueError, 'escapes'):
            verifier.verify(self.folder)
        self.assertFalse(checksums.exists())

    def test_checksums_describe_both_exact_archives(self):
        wheel = self.folder / 'sample.whl'
        wheel.write_bytes(b'wheel bytes')
        self.archive.write_bytes(b'archive bytes')
        expected = [hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name
                    for path in (wheel, self.archive)]
        self.assertEqual(verifier.write_checksums(self.folder, (wheel, self.archive)), expected)
        self.assertEqual((self.folder / 'SHA256SUMS').read_text(), '\n'.join(expected) + '\n')
        self.assertFalse(list(self.folder.glob('.SHA256SUMS-*')))

    def test_checksum_publication_failure_leaves_no_partial_result(self):
        self.archive.write_bytes(b'archive bytes')
        with patch.object(Path, 'replace', side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(OSError, 'publication failed'):
                verifier.write_checksums(self.folder, (self.archive,))
        self.assertFalse((self.folder / 'SHA256SUMS').exists())
        self.assertFalse(list(self.folder.glob('.SHA256SUMS-*')))

    def write_wheel(self, extra=()):
        wheel = self.folder / 'sample.whl'
        with ZipFile(wheel, 'w') as archive:
            for path in (ROOT / 'src/public_data_sentinel').rglob('*.py'):
                archive.write(path, path.relative_to(ROOT / 'src').as_posix())
            for name in ('METADATA', 'WHEEL', 'RECORD', 'entry_points.txt',
                         'top_level.txt', 'licenses/LICENSE'):
                archive.writestr('public_data_sentinel-0.2.1.dist-info/' + name, b'')
            for name in extra:
                archive.writestr(name, b'harmless test payload')
        return wheel

    def test_expected_wheel_members_are_accepted(self):
        verifier.verify_wheel_members(self.write_wheel(), ROOT)

    def test_unchecked_wheel_payloads_are_rejected(self):
        for name in ('sitecustomize.py', 'unchecked_startup.pth',
                     'public_data_sentinel-0.2.1.data/purelib/public_data_sentinel/cli.py',
                     'public_data_sentinel-0.2.1.data/scripts/data-sentinel',
                     'public_data_sentinel/cli.cpython-312-x86_64-linux-gnu.so',
                     '../outside.py'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Unexpected wheel members'):
                    verifier.verify_wheel_members(self.write_wheel([name]), ROOT)

    def test_duplicate_wheel_members_are_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            wheel = self.write_wheel(['public_data_sentinel/cli.py'])
        with self.assertRaisesRegex(ValueError, 'Duplicate wheel member'):
            verifier.verify_wheel_members(wheel, ROOT)

    def test_additional_wheel_metadata_directory_is_rejected(self):
        wheel = self.write_wheel(['unexpected-1.0.dist-info/METADATA'])
        with self.assertRaisesRegex(ValueError, 'exactly one wheel metadata directory'):
            verifier.verify_wheel_members(wheel, ROOT)

    def test_unchanged_original_artifacts_match_verified_copies(self):
        copied = self.folder / 'copied'
        copied.mkdir()
        for name in ('sample.whl', 'sample.tar.gz'):
            (self.folder / name).write_bytes(b'verified bytes')
            (copied / name).write_bytes(b'verified bytes')
        verifier.verify_artifacts_unchanged(self.folder, list(copied.iterdir()))

    def test_changed_original_artifacts_are_rejected(self):
        copied = self.folder / 'copied'
        copied.mkdir()
        for name in ('sample.whl', 'sample.tar.gz'):
            with self.subTest(name=name):
                (self.folder / name).write_bytes(b'changed bytes')
                (copied / name).write_bytes(b'original data')
                with self.assertRaisesRegex(ValueError, 'Distribution changed during verification'):
                    verifier.verify_artifacts_unchanged(self.folder, [copied / name])


if __name__ == '__main__':
    unittest.main()
