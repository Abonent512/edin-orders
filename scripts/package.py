"""Prepare a source/release ZIP without credentials, databases or build keys."""
import argparse
import hashlib
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ALLOWED={'backend','frontend','docs','deploy','scripts','mobile','releases'}
SKIP_DIRS={'.venv','node_modules','__pycache__','.pytest_cache','.git','build'}
ROOT_FILES={'Dockerfile','.dockerignore','.gitignore','.env.example','compose.yaml','render.yaml','README.md','THIRD_PARTY.md'}


def package(destination):
    destination=Path(destination).resolve()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob('*')):
            if not path.is_file():continue
            relative=path.relative_to(ROOT)
            if relative.parts[0] not in ALLOWED and str(relative) not in ROOT_FILES:continue
            if any(p in SKIP_DIRS for p in relative.parts[:-1]):continue
            if path.name=='.env' or path.suffix in {'.keystore','.jks','.pyc','.tsbuildinfo','.log'}:continue
            archive.write(path,Path('edin-case9')/relative)
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix+'.sha256').write_text(digest+'  '+destination.name+'\n')
    print(f'{destination.name}: {destination.stat().st_size:,} bytes; SHA256 {digest}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args();package(args.destination)
