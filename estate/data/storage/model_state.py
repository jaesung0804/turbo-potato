"""Checksum-verified immutable monthly artifacts, independent of a UI deployment."""
import argparse,hashlib,json,re
from pathlib import Path
def digest(body):return hashlib.sha256(body).hexdigest()
def read(state):
    m=json.loads((state/'model_manifest.json').read_text());bodies={}
    if m.get('schema_version')!=1:raise ValueError('Unsupported model state')
    for item in m['files']:
        name=item['path']
        if not re.fullmatch(r'models/estate-reference-v\d+/\d{4}-\d{2}\.joblib',name) or name in bodies:raise ValueError('Unsafe model path')
        path=state/name
        if not path.resolve().is_relative_to(state.resolve()):raise ValueError('Model links are not permitted')
        body=path.read_bytes()
        if len(body)!=item['bytes'] or digest(body)!=item['sha256']:raise ValueError('Model checksum mismatch')
        bodies[name]=body
    return m,bodies
def restore(state,root=Path('.')):
    m,bodies=read(state)
    for name,body in bodies.items():
        target=root/name
        if target.exists() and target.read_bytes()!=body:raise ValueError('Frozen model differs from saved state')
    for name,body in bodies.items():
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
    return m
def pack(state,root=Path('.')):
    old=read(state)[1] if (state/'model_manifest.json').exists() else {}
    for name,body in old.items():
        if not (root/name).exists() or (root/name).read_bytes()!=body:raise ValueError('A saved monthly model cannot be replaced')
    files=[]
    for path in sorted((root/'models').rglob('*.joblib')):
        name=path.relative_to(root).as_posix()
        if not re.fullmatch(r'models/estate-reference-v\d+/\d{4}-\d{2}\.joblib',name):continue
        body=path.read_bytes();target=state/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
        files.append({'path':name,'bytes':len(body),'sha256':digest(body)})
    if not files:raise ValueError('No current monthly model to persist')
    m={'schema_version':1,'files':files};(state/'model_manifest.json').write_text(json.dumps(m,indent=2))
    (state/'.gitattributes').write_text('models/** -text\n*.json text eol=lf\n')
    return m
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['pack','restore']);p.add_argument('--state-dir',required=True)
    a=p.parse_args();m=globals()[a.command](Path(a.state_dir));print(a.command,'verified immutable models:',len(m['files']))
