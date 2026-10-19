from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from support import ROOT, SKILL, clean_env

spec = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts' / 'build_release.py')
build_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_release)


class ReleaseTests(unittest.TestCase):
    def test_zip_write_failure_leaves_no_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError('compression failed')):
                with self.assertRaises(OSError):
                    build_release.build(output)
            self.assertEqual(list(output.iterdir()), [])
            self.assertTrue(build_release.build(output).is_file())

    def test_artifact_write_failure_leaves_no_artifacts(self):
        original_open = Path.open
        for suffix in ('.zip', '.sha256'):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)

                @contextmanager
                def failing_open(path, *args, **kwargs):
                    with original_open(path, *args, **kwargs) as stream:
                        if path.parent == output and path.suffix == suffix:
                            def fail_write(data):
                                stream.write(data[:8])
                                raise OSError('disk full')
                            yield Mock(wraps=stream, write=Mock(side_effect=fail_write))
                        else:
                            yield stream

                with patch.object(Path, 'open', failing_open):
                    with self.assertRaises(OSError):
                        build_release.build(output)
                self.assertEqual(list(output.iterdir()), [])
                self.assertTrue(build_release.build(output).is_file())

    def test_source_date_epoch_overrides_git_in_utc(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'SOURCE_DATE_EPOCH': '1704164646'}), \
                patch.object(build_release.subprocess, 'check_output') as git:
            archive = build_release.build(directory)
            with zipfile.ZipFile(archive) as output:
                self.assertEqual({info.date_time for info in output.infolist()}, {(2024, 1, 2, 3, 4, 6)})
            git.assert_not_called()

    def test_git_commit_date_is_used_without_source_date_epoch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch.object(build_release.subprocess, 'check_output', return_value='1704164646\n') as git:
            archive = build_release.build(directory)
            with zipfile.ZipFile(archive) as output:
                self.assertEqual({info.date_time for info in output.infolist()}, {(2024, 1, 2, 3, 4, 6)})
            git.assert_called_once_with(['git', 'log', '-1', '--format=%ct'], cwd=ROOT,
                                        stderr=subprocess.DEVNULL, text=True)

    def test_missing_git_uses_fixed_date(self):
        for error in (FileNotFoundError('git'), subprocess.CalledProcessError(128, 'git')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(build_release.subprocess, 'check_output', side_effect=error):
                first = build_release.build(Path(directory) / 'first')
                second = build_release.build(Path(directory) / 'second')
                self.assertEqual(first.read_bytes(), second.read_bytes())
                with zipfile.ZipFile(first) as output:
                    self.assertEqual({info.date_time for info in output.infolist()}, {(2000, 1, 1, 0, 0, 0)})

    def test_unusable_epoch_falls_back_to_git_then_fixed_date(self):
        for epoch in ('', 'invalid', '0', '999999999999999999999'):
            with self.subTest(epoch=epoch), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {'SOURCE_DATE_EPOCH': epoch}), \
                    patch.object(build_release.subprocess, 'check_output', return_value='1704164646'):
                first = build_release.build(Path(directory) / 'git')
                with zipfile.ZipFile(first) as output:
                    self.assertEqual({info.date_time for info in output.infolist()}, {(2024, 1, 2, 3, 4, 6)})
                with patch.object(build_release.subprocess, 'check_output', return_value=epoch):
                    second = build_release.build(Path(directory) / 'fixed')
                with zipfile.ZipFile(second) as output:
                    self.assertEqual({info.date_time for info in output.infolist()}, {(2000, 1, 1, 0, 0, 0)})

    def test_archive_is_deterministic_complete_and_runnable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_release.build(root / 'first')
            second = build_release.build(root / 'second')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            checksum = first.with_suffix('.zip.sha256').read_text(encoding='utf-8').split()[0]
            self.assertEqual(checksum, hashlib.sha256(first.read_bytes()).hexdigest())
            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
                expected = {'camofox-client/SKILL.md', 'camofox-client/LICENSE', 'camofox-client/version.txt'}
                expected.update('camofox-client/' + file.relative_to(SKILL).as_posix()
                                for folder, pattern in (('scripts', '*.py'), ('references', '*.md'))
                                for file in (SKILL / folder).glob(pattern))
                self.assertEqual(names, expected)
                self.assertFalse(any('__pycache__' in name or '.env' in name for name in names))
                self.assertEqual(archive.read('camofox-client/version.txt'), (ROOT / 'version.txt').read_bytes())
                archive.extractall(root / '安装 with spaces')
            script = root / '安装 with spaces' / 'camofox-client/scripts/main.py'
            result = subprocess.run([sys.executable, str(script), '--help'], cwd=root, env=clean_env(),
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with self.assertRaises(FileExistsError):
                build_release.build(root / 'first')
