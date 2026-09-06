"""Versioned, binary-safe complete raw data and collection checkpoints."""
import argparse,csv,gzip,hashlib,io,json,re,tarfile,tempfile,shutil
from pathlib import Path
from estate_io import write_binary, write_json
from collect_estate_transactions import read_partition, REGIONS
from get_molit_apt_trade_data import DASHBOARD_FIELDNAMES

def sha(body):return hashlib.sha256(body).hexdigest()

def pack_v1(state,root=Path('.')):
    source=root/'data/capital_area_apt_trade_transactions.csv'
    collection=source.with_suffix('.manifest.json')
    meta=json.loads(collection.read_text())
    if not meta.get('complete') or meta.get('normalizer_version')!=2 or sha(source.read_bytes())!=meta['sha256']:
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

def restore_v1(state,root=Path('.')):
    manifest=json.loads((state/'raw_manifest.json').read_text());bodies={}
    if manifest.get('schema_version')!=1 or not manifest['collection']['complete'] or manifest['collection'].get('normalizer_version')!=2:
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
        write_binary(staged/'data/capital_area_apt_trade_transactions.csv',csv)
        (staged/'data/capital_area_apt_trade_transactions.manifest.json').write_bytes(bodies['collection.json'])
        with tarfile.open(fileobj=io.BytesIO(bodies['checkpoints.tar.gz']),mode='r:gz') as tar:
            for member in tar.getmembers():
                if not member.isfile() or not re.fullmatch(r'data/molit_cache_v3/\d{6}-\d{5}\.json\.gz',member.name):raise ValueError('Unsafe checkpoint path')
            tar.extractall(staged,filter='data')
        for path in staged.rglob('*'):
            if path.is_file():
                target=root/path.relative_to(staged);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    return manifest

def pack(state,root=Path('.')):
    """Keep independently compressed partitions; unchanged history reuses Git blobs."""
    source=root/'data/capital_area_apt_trade_transactions.csv'
    meta=json.loads(source.with_suffix('.manifest.json').read_text())
    if not meta.get('complete') or meta.get('normalizer_version')!=2 or sha(source.read_bytes())!=meta['sha256']:
        raise ValueError('Only complete, verified collections can replace raw state')
    files=[];rows=0;regions=set();old=None
    if (state/'raw_manifest.json').exists():old=json.loads((state/'raw_manifest.json').read_text())
    for path in sorted((root/'data/molit_cache_v3').glob('*.json.gz')):
        match=re.fullmatch(r'(\d{6})-(\d{5})\.json\.gz',path.name)
        if not match:continue
        month,code=match.groups()
        if not meta['start']<=month<=meta['end'] or code not in REGIONS:continue
        data=read_partition(path,month,code);rows+=data['count'];regions.add(code)
        # The fetch timestamp belongs in the small manifest, not in an otherwise
        # unchanged compressed history blob that would grow Git on every refresh.
        fetched=data.pop('fetched_at',None)
        body=gzip.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),mtime=0)
        name='partitions/'+path.name;target=state/name
        if not target.exists() or target.read_bytes()!=body:write_binary(target,body)
        files.append({'path':name,'size':len(body),'sha256':sha(body),'fetched_at':fetched})
    if rows!=meta['rows'] or len(files)!=meta['partition_count'] or len(regions)!=meta['region_count']:
        raise ValueError('The raw partitions do not cover the complete collection')
    manifest={'schema_version':2,'collection':meta,'files':files}
    write_json(state/'raw_manifest.json',manifest,indent=2)
    (state/'.gitattributes').write_text('partitions/** -text\n*.json text eol=lf\n')
    # Remove only generated payloads referenced by the previous manifest.
    keep={f['path'] for f in files}
    old_paths=[]
    if old and old.get('schema_version')==1:
        old_paths=[p['path'] for item in old['files'] for p in item['parts']]
    elif old and old.get('schema_version')==2:old_paths=[f['path'] for f in old['files']]
    for name in old_paths:
        if name not in keep and re.fullmatch(r'(objects/[a-f0-9]{16}-\d{4}\.part|partitions/\d{6}-\d{5}\.json\.gz)',name):
            (state/name).unlink(missing_ok=True)
    return manifest


def restore(state,root=Path('.')):
    manifest=json.loads((state/'raw_manifest.json').read_text())
    if manifest.get('schema_version')==1:return restore_v1(state,root)
    meta=manifest['collection']
    if manifest.get('schema_version')!=2 or not meta.get('complete') or meta.get('normalizer_version')!=2:
        raise ValueError('Unsupported or incomplete raw state')
    names=[f['path'] for f in manifest['files']]
    if len(set(names))!=len(names) or len(names)!=meta['partition_count']:
        raise ValueError('Duplicate or missing raw partitions')
    with tempfile.TemporaryDirectory() as temp:
        staged=Path(temp);cache=staged/'data/molit_cache_v3';cache.mkdir(parents=True)
        output=staged/'data/capital_area_apt_trade_transactions.csv';rows=0;regions=set()
        with output.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=DASHBOARD_FIELDNAMES);writer.writeheader()
            for item in sorted(manifest['files'],key=lambda x:x['path']):
                match=re.fullmatch(r'partitions/(\d{6})-(\d{5})\.json\.gz',item['path'])
                if not match:raise ValueError('Unsafe raw partition path')
                month,code=match.groups();path=state/item['path']
                if not path.resolve().is_relative_to(state.resolve()):raise ValueError('Raw links are not permitted')
                body=path.read_bytes()
                if len(body)!=item['size'] or sha(body)!=item['sha256']:raise ValueError('Raw partition checksum mismatch')
                data=read_partition(path,month,code)
                if not meta['start']<=month<=meta['end']:raise ValueError('Unexpected raw partition month')
                rows+=data['count'];regions.add(code);writer.writerows(data['rows'])
                data['fetched_at']=item.get('fetched_at')
                write_binary(cache/path.name,gzip.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
        if rows!=meta['rows'] or len(regions)!=meta['region_count'] or sha(output.read_bytes())!=meta['sha256']:
            raise ValueError('Reassembled raw data does not match the saved collection')
        write_json(output.with_suffix('.manifest.json'),meta,indent=2)
        for path in staged.rglob('*'):
            if path.is_file():
                target=root/path.relative_to(staged);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['pack','restore']);p.add_argument('--state-dir',required=True)
    args=p.parse_args();m=globals()[args.command](Path(args.state_dir))
    print(args.command,'verified raw state:',m['collection']['rows'],'rows through',m['collection']['end'])
