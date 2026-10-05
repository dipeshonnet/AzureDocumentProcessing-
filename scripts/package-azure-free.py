"""Package only backend source for F1; deploy frontend/dist separately to SWA."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
target = root / '.tools/azure-free-mvp.zip'
target.parent.mkdir(exist_ok=True)
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    archive.write(root / 'requirements.txt', 'requirements.txt')
    for source, destination in [('backend/app', 'app'), ('config', 'config'), ('prompts', 'prompts')]:
        for path in (root / source).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                archive.write(path, Path(destination) / path.relative_to(root / source))
print(target)
