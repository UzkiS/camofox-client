"""Build a deterministic, self-contained skill archive from an explicit allowlist."""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'camofox-client'


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
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    checksum.write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n', encoding='utf-8')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    print(build(parser.parse_args().output_dir))
