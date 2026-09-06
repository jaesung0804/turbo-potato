"""Complete, resumable capital-area apartment collection; no partial CSV replacement."""
from __future__ import annotations
import argparse
import ast
import concurrent.futures
import csv
import gzip
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from get_molit_apt_trade_data import CAPITAL_AREA_LAWD_CODES, LawdCode, DASHBOARD_FIELDNAMES, normalize_row, month_range

URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
class TransientAPIError(RuntimeError): pass
OBSOLETE = {"28110", "28140", "28260", "41590", "41190"}
REGIONS = {r.code:r for r in CAPITAL_AREA_LAWD_CODES if r.code not in OBSOLETE}
for code, sido, name in [
    ("28125","인천광역시","제물포구"),("28155","인천광역시","영종구"),
    ("28275","인천광역시","서해구"),("28290","인천광역시","검단구"),
    ("41192","경기도","부천시 원미구"),("41194","경기도","부천시 소사구"),("41196","경기도","부천시 오정구"),
    ("41591","경기도","화성시 만세구"),("41593","경기도","화성시 효행구"),
    ("41595","경기도","화성시 병점구"),("41597","경기도","화성시 동탄구")]:
    REGIONS[code] = LawdCode(code,sido,name)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate(root):
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    if code not in {"0","00","000"}:
        raise RuntimeError("API logical error " + (code if code and code.isdigit() else "unknown"))
    total = root.findtext(".//totalCount")
    if total is None or not total.isdigit():
        raise RuntimeError("Missing or invalid API totalCount")
    return int(total)


def request(key, code, month, page, timeout=25):
    query = urllib.parse.urlencode({"serviceKey":urllib.parse.unquote(key),"LAWD_CD":code,
        "DEAL_YMD":month,"pageNo":page,"numOfRows":1000})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL+"?"+query, timeout=timeout) as response:
                root = ET.fromstring(response.read())
            validate(root)
            return root
        except urllib.error.HTTPError as error:
            if error.code in {400,401,403}:
                raise RuntimeError(f"API HTTP {error.code}; existing data preserved") from None
            kind = f"HTTP {error.code}"
        except (urllib.error.URLError,TimeoutError,ET.ParseError) as error:
            kind = type(getattr(error,'reason',error)).__name__
        if attempt < 2:
            time.sleep(1 + attempt)
    raise TransientAPIError(f"API request failed for {code}/{month}/page-{page}: {kind}") from None


def fetch_partition(key, region, month, abort):
    rows, seen, total = [], set(), None
    for page in range(1,10001):
        if abort.is_set():
            raise RuntimeError("Collection interrupted after another failed partition")
        root = request(key,region.code,month,page)
        current_total = validate(root)
        if total is None: total = current_total
        if current_total != total: raise RuntimeError(f"Changed page count: {region.code}/{month}")
        items = list(root.findall(".//item"))
        fingerprint = digest(b"".join(ET.tostring(item) for item in items))
        if items and fingerprint in seen: raise RuntimeError(f"Repeated API page: {region.code}/{month}")
        seen.add(fingerprint)
        for item in items:
            row = normalize_row(item,region)
            try:
                datetime.strptime(row["CTRT_DAY"],"%Y%m%d")
                price, area = float(row["THING_AMT"]),float(row["ARCH_AREA"])
                if not all(math.isfinite(v) and v>0 for v in [price,area]): raise ValueError
                if row["CTRT_DAY"][:6] != month: raise ValueError
            except (ValueError,KeyError):
                raise RuntimeError(f"Invalid transaction schema: {region.code}/{month}") from None
            cancellation = (item.findtext("cancelDealType") or "").strip().upper()
            if row.get("RTRCN_DAY","").strip() in {"-","--"}: row["RTRCN_DAY"] = ""
            if cancellation in {"Y","O","1","해제"} and not row.get("RTRCN_DAY"):
                row["RTRCN_DAY"] = "cancelled"
            rows.append(row)
        if len(rows) == total: return rows
        if len(rows)>total or not items: raise RuntimeError(f"Incomplete pagination: {region.code}/{month}")
        time.sleep(.1)
    raise RuntimeError("API pagination exceeded the safety bound")


def rows_bytes(rows):
    return json.dumps(rows,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()


def read_partition(path,month,code):
    data=json.loads(gzip.decompress(path.read_bytes()))
    if not data.get("complete") or data["month"]!=month or data["code"]!=code:
        raise ValueError("Invalid partition identity")
    if data["count"]!=len(data["rows"]) or digest(rows_bytes(data["rows"]))!=data["rows_sha256"]:
        raise ValueError("Invalid partition count or checksum")
    return data


def collect(key,start,end,cache,output,refresh_months=3,workers=2,shard_index=0,shard_count=1):
    months=month_range(start,end)
    if start>end or end>datetime.now(timezone.utc).strftime("%Y%m") or refresh_months<0:
        raise ValueError("Invalid contract-month range")
    cache.mkdir(parents=True,exist_ok=True)
    partitions=[]; missing=[]
    if shard_index<0 or shard_index>=shard_count:raise ValueError('Invalid shard index')
    regions=sorted(REGIONS.items())[shard_index::shard_count]
    for month in months:
        for code,region in regions:
            path=cache/f"{month}-{code}.json.gz"
            partitions.append((path,month,code))
            refresh=refresh_months>0 and month in months[-refresh_months:]
            if path.exists() and not refresh:
                try:read_partition(path,month,code);continue
                except (OSError,ValueError,KeyError):pass
            missing.append((path,month,region))
    if missing and not key:raise RuntimeError("MOLIT_API_KEY is required for uncached or refreshed partitions")
    if missing and shard_count==1:
        # Confirm that pre-reorganization history is reachable under the new
        # registry instead of silently dropping the former city/district.
        for old_codes,new_codes in [(["41190"],["41192","41194","41196"]),
            (["41590"],["41591","41593","41595","41597"]),
            (["28110","28140"],["28125","28155"]),(["28260"],["28275","28290"])]:
            totals={}
            for code in old_codes+new_codes:
                totals[code]=validate(request(key,code,start,1))
            print("Historical registry audit",start,json.dumps(totals),flush=True)
            if sum(totals[c] for c in new_codes)<sum(totals[c] for c in old_codes):
                raise RuntimeError("Current district registry does not cover former-code history; existing data preserved")
    abort=threading.Event()
    def one(item):
        path,month,region=item
        rows=fetch_partition(key,region,month,abort)
        data={"complete":True,"month":month,"code":region.code,"count":len(rows),
              "fetched_at":utc_now(),"rows_sha256":digest(rows_bytes(rows)),"rows":rows}
        temp=path.with_suffix(".tmp")
        temp.write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False,separators=(",",":")).encode(),mtime=0))
        temp.replace(path)
        return len(rows)
    print(f"Requested {len(partitions)} partitions; fetch {len(missing)}, reuse {len(partitions)-len(missing)}",flush=True)
    pending=missing
    for pass_number in range(3):
        failed=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(one,item):item for item in pending}
            for count,future in enumerate(concurrent.futures.as_completed(futures),1):
                try:future.result()
                except TransientAPIError as error:
                    failed.append(futures[future]);print(str(error),flush=True)
                except Exception:
                    abort.set()
                    for other in futures:other.cancel()
                    raise
                if count%100==0 or count==len(pending):print(f"Pass {pass_number+1}: checked {count}/{len(pending)}; transient failures {len(failed)}",flush=True)
        if not failed:break
        pending=failed
    if failed:raise RuntimeError(f'{len(failed)} incomplete partitions; completed checkpoints and previous CSV were preserved')
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix(".csv.tmp"); total=0
    with temp.open("w",newline="",encoding="utf-8-sig") as stream:
        writer=csv.DictWriter(stream,fieldnames=DASHBOARD_FIELDNAMES);writer.writeheader()
        for path,month,code in partitions:
            data=read_partition(path,month,code)
            writer.writerows(data["rows"]);total+=data["count"]
    if total==0:raise RuntimeError("Empty whole-market collection cannot replace existing data")
    manifest={"schema_version":1,"complete":shard_count==1,"shard_complete":True,"start":start,"end":end,"rows":total,
        "shard_index":shard_index,"shard_count":shard_count,
        "partition_count":len(partitions),"region_count":len(regions),"registry_month":"2026-09",
        "sha256":digest(temp.read_bytes()),"fetched_at":utc_now(),"source":URL,
        "refresh_months":refresh_months,"note":"All API pages retained, including cancellations for downstream filtering."}
    temp.replace(output)
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest),flush=True)
    return manifest


def existing_key():
    key=os.getenv("MOLIT_API_KEY","").strip()
    if key:return key
    tree=ast.parse(Path("get_apt_basis_detail_data.py").read_text(encoding="utf-8-sig"))
    values=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)
            and re.fullmatch(r"[A-Za-z0-9+/=%]{70,160}",n.value)]
    if len(values)!=1:raise RuntimeError("No existing authorized public-data configuration")
    return values[0]


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start",default="202101");p.add_argument("--end",default=datetime.now(timezone.utc).strftime("%Y%m"))
    p.add_argument("--refresh-months",type=int,default=3);p.add_argument("--workers",type=int,choices=[1,2,3,4],default=2)
    p.add_argument('--shard-index',type=int,default=0);p.add_argument('--shard-count',type=int,choices=[1,2,4],default=1)
    p.add_argument("--cache",default="data/molit_cache_v3");p.add_argument("--output",default="data/capital_area_apt_trade_transactions.csv")
    p.add_argument("--use-existing-config",action="store_true",help="One-time migration of the configuration already provided in this repository")
    args=p.parse_args()
    key=existing_key() if args.use_existing_config else os.getenv("MOLIT_API_KEY","")
    if key and os.getenv("GITHUB_ACTIONS"):print("::add-mask::"+key,flush=True)
    collect(key,args.start,args.end,Path(args.cache),Path(args.output),args.refresh_months,args.workers,args.shard_index,args.shard_count)
