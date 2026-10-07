"""Copy and version the small public UI without importing collection or ML code."""
import hashlib
import shutil
import zipfile
from pathlib import Path

UI_FILES = ['index.html', 'model.html', 'quantile-validation.html', 'potential.html', 'research.html', 'research.css',
            'research.js', 'release-status.js',
            'styles.css', 'filter-select.js', 'app.js', 'potential.js', 'data-store.js', 'result-pages.js',
            'xlsx-export.js', 'estate-export.js', 'downloads/estate-excel-helper.zip',
            'model.js', '404.html', 'vendor/leaflet.css', 'vendor/leaflet.js', 'vendor/LICENSE.txt']


def copy_ui(output, source=Path('web')):
    output, source = Path(output), Path(source)
    for name in UI_FILES:
        (output / name).parent.mkdir(parents=True, exist_ok=True)
        if name == 'downloads/estate-excel-helper.zip':
            with zipfile.ZipFile(output/name, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(Path('excel_helper').iterdir()):
                    if path.suffix in {'.py', '.txt', '.md', '.cmd', '.bas'}:
                        info = zipfile.ZipInfo(path.name, date_time=(2026, 10, 8, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        body = path.read_bytes()
                        if path.suffix in {'.cmd', '.bas'}:
                            body = body.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
                        archive.writestr(info, body)
        else:
            shutil.copy2(source / name, output / name)
    for page in ['index.html', 'model.html', 'potential.html', 'research.html']:
        html = (output / page).read_text(encoding='utf-8')
        for name in UI_FILES:
            if name.endswith(('.css', '.js')):
                version = hashlib.sha256((output / name).read_bytes()).hexdigest()[:16]
                html = html.replace('"' + name + '"', '"' + name + '?v=' + version + '"')
        (output / page).write_text(html, encoding='utf-8', newline='\n')
    (output / '.nojekyll').touch()
    return {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in UI_FILES}
