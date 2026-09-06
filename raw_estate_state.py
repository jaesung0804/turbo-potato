"""Versioned, binary-safe complete raw data and collection checkpoints."""
import argparse,gzip,hashlib,io,json,re,tarfile,tempfile,shutil
from pathlib import Path

def sha(body):return hashlib.sha256(body).hexdigest()

def pack(state,root=Path('.')):
    source=root/'data/capital_area_apt_trade_transactions.csv'
    collection=source.with_suffix('.manifest.json')
    meta=json.loads(collection.read_text())
    if not meta.get('complete') or sha(source.read_bytes())!=meta['sha256']:
        raise ValueError('Only checksum-verified complete collections can replace raw state')
    buf=io.BytesIO()
    with gzip.GzipFile(fileobj=buf,mode='wb',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w') as tar:
            for path in sorted((root/'data/molit_cache_v3').glob('*.json.gz')):
                body=path.read_bytes()
                info=tarfile.TarInfo('data/molit_cache_v3/'+path.name)
                info.size=len(body);info.mode=0o644;info.mtime=0
                tar.addfile(info,io.BytesIO(body))
    bodies={'transactions.csv.gz':gzip.compress(source.read_bytes(),mtime=0),
            'collection.json':collection.read_bytes(),'checkpoints.tar.gz':buf.getvalue()}
    objects=state/'objects';objects.mkdir(parents=True,exist_ok=True)
    manifest={'schema_version':1,'collection':meta,'files':[]};keep=set()
    for name,body in bodies.items():
        item={'name':name,'size':len(body),'sha256':sha(body),'parts':[]}
        for index,offset in enumerate(range(0,len(body),40*1024*1024)):
            chunk=body[offset:offset+40*1024*1024];filename=f'{sha(body)[:16]}-{index:04d}.part'
            (objects/filename).write_bytes(chunk);keep.add(filename)
            item['parts'].append({'path':'objects/'+filename,'size':len(chunk),'sha256':sha(chunk)})
        manifest['files'].append(item)
    (state/'raw_manifest.json').write_text(json.dumps(manifest,indent=2))
    (state/'.gitattributes').write_text('objects/** -text\n*.json text eol=lf\n')
    for path in objects.glob('*.part'):
        if path.name not in keep:path.unlink()
    return manifest

def restore(state,root=Path('.')):
    manifest=json.loads((state/'raw_manifest.json').read_text());bodies={}
    if manifest.get('schema_version')!=1 or not manifest['collection']['complete']:
        raise ValueError('Unsupported or incomplete raw state')
    for item in manifest['files']:
        if item['name'] not in {'transactions.csv.gz','collection.json','checkpoints.tar.gz'}:raise ValueError('Unexpected state path')
        chunks=[]
        for part in item['parts']:
            if not re.fullmatch(r'objects/[a-f0-9]{16}-\d{4}\.part',part['path']):raise ValueError('Unexpected part path')
            body=(state/part['path']).read_bytes()
            if len(body)!=part['size'] or sha(body)!=part['sha256']:raise ValueError('State part checksum mismatch')
            chunks.append(body)
        body=b''.join(chunks)
        if len(body)!=item['size'] or sha(body)!=item['sha256']:raise ValueError('State payload checksum mismatch')
        bodies[item['name']]=body
    with tempfile.TemporaryDirectory() as temp:
        staged=Path(temp);(staged/'data').mkdir()
        csv=gzip.decompress(bodies['transactions.csv.gz'])
        if sha(csv)!=manifest['collection']['sha256']:raise ValueError('Raw CSV checksum mismatch')
        (staged/'data/capital_area_apt_trade_transactions.csv').write_bytes(csv)
        (staged/'data/capital_area_apt_trade_transactions.manifest.json').write_bytes(bodies['collection.json'])
        with tarfile.open(fileobj=io.BytesIO(bodies['checkpoints.tar.gz']),mode='r:gz') as tar:
            for member in tar.getmembers():
                if not member.isfile() or not re.fullmatch(r'data/molit_cache_v3/\d{6}-\d{5}\.json\.gz',member.name):raise ValueError('Unsafe checkpoint path')
            tar.extractall(staged,filter='data')
        for path in staged.rglob('*'):
            if path.is_file():
                target=root/path.relative_to(staged);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['pack','restore']);p.add_argument('--state-dir',required=True)
    args=p.parse_args();m=globals()[args.command](Path(args.state_dir))
    print(args.command,'verified raw state:',m['collection']['rows'],'rows through',m['collection']['end'])
