import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from support import ROOT, SKILL, clean_env

spec = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts' / 'build_release.py')
build_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_release)


class ReleaseTests(unittest.TestCase):
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
