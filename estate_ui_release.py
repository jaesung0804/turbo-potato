"""Copy and version the small public UI without importing collection or ML code."""
import hashlib
import shutil
from pathlib import Path

UI_FILES = ['index.html', 'model.html', 'potential.html', 'research.html', 'research.css',
            'styles.css', 'app.js', 'potential.js', 'data-store.js', 'result-pages.js',
            'model.js', '404.html', 'vendor/leaflet.css', 'vendor/leaflet.js', 'vendor/LICENSE.txt']


def copy_ui(output, source=Path('web')):
    output, source = Path(output), Path(source)
    for name in UI_FILES:
        (output / name).parent.mkdir(parents=True, exist_ok=True)
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
