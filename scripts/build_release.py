"""Build a deterministic, self-contained skill archive from an explicit allowlist."""
import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'camofox-client'
DEFAULT_DATE_TIME = (2000, 1, 1, 0, 0, 0)


def _date_time_from_epoch(epoch):
    try:
        date_time = time.gmtime(int(epoch))[:6]
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return date_time if 1980 <= date_time[0] <= 2107 else None


def _archive_date_time():
    date_time = _date_time_from_epoch(os.environ.get('SOURCE_DATE_EPOCH'))
    if date_time is not None:
        return date_time
    try:
        epoch = subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=ROOT,
                                        stderr=subprocess.DEVNULL, text=True)
    except (OSError, subprocess.CalledProcessError):
        return DEFAULT_DATE_TIME
    return _date_time_from_epoch(epoch) or DEFAULT_DATE_TIME


def build(output_dir):
    version = (ROOT / 'version.txt').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('version.txt must contain a semantic version')
    if (ROOT / 'LICENSE').read_bytes() != (SKILL / 'LICENSE').read_bytes():
        raise ValueError('The bundled LICENSE must match the root LICENSE')
    files = [SKILL / 'SKILL.md', SKILL / 'LICENSE', *sorted((SKILL / 'scripts').glob('*.py')),
             *sorted((SKILL / 'references').glob('*.md'))]
    if not (SKILL / 'scripts' / 'main.py').is_file():
        raise ValueError('The skill entry point is missing')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f'camofox-client-v{version}.zip'
    checksum = archive.with_suffix('.zip.sha256')
    if archive.exists() or checksum.exists():
        raise FileExistsError('Release artifacts already exist; use a new output directory')
    entries = {f'camofox-client/{file.relative_to(SKILL).as_posix()}': file.read_bytes() for file in files}
    entries['camofox-client/version.txt'] = (version + '\n').encode('utf-8')
    date_time = _archive_date_time()
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as output:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=date_time)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                output.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        archive_bytes = buffer.getvalue()
    created = []
    try:
        with archive.open('xb') as output:
            created.append(archive)
            output.write(archive_bytes)
        with checksum.open('x', encoding='utf-8') as output:
            created.append(checksum)
            output.write(f'{hashlib.sha256(archive_bytes).hexdigest()}  {archive.name}\n')
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    print(build(parser.parse_args().output_dir))
