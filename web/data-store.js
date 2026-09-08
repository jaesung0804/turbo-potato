const DashboardData = (() => {
  const metricNames=["price_billion","area_pyeong","price_per_pyeong"], stats=["avg","median","min","max"];
  const metrics=values=>Object.fromEntries(metricNames.map((k,i)=>[k,Object.fromEntries(stats.map((s,j)=>[s,values[i*4+j]]))]));
  async function compressed(asset) {
    if (!asset || !/^data\/bundle\/[a-zA-Z0-9_.-]+\.json\.gz$/.test(asset.url)) throw Error("자료 경로 오류");
    if (typeof DecompressionStream === "undefined") throw Error("최신 브라우저로 열어주세요.");
    const r=await fetch(asset.url,{cache:"force-cache"});if(!r.ok)throw Error(`자료 응답 ${r.status}`);
    const body=await r.arrayBuffer();
    if(body.byteLength!==asset.bytes)throw Error('데이터 무결성 확인 실패 (자료 크기)');
    if(globalThis.crypto?.subtle){
      const h=[...new Uint8Array(await crypto.subtle.digest("SHA-256",body))].map(x=>x.toString(16).padStart(2,"0")).join("");
      if(h!==asset.sha256)throw Error("데이터 무결성 확인 실패");
    }
    return new Response(new Blob([body]).stream().pipeThrough(new DecompressionStream("gzip"))).json();
  }
  async function open() {
    const r=await fetch("data/dashboard_manifest.json",{cache:"no-cache"});if(!r.ok)throw Error(`자료 응답 ${r.status}`);
    const manifest=await r.json();if(manifest.schema_version!==1)throw Error("지원하지 않는 자료 버전");
    const [catalog,packed]=await Promise.all([compressed(manifest.catalog),compressed(manifest.recommendations)]);
    const locations=new Map(catalog.regions.map(r=>[r.code,r]));
    const recommendations=packed.encoding==='catalog-v1'?{...packed.metadata,recommendations:packed.rows.map(([idx,code,...values])=>{
      const b=catalog.addresses[idx],r=locations.get(code);if(!b||!r||values.length!==packed.fields.length)throw Error('모델 색인 오류');
      return {...b,...r,...Object.fromEntries(packed.fields.map((k,i)=>[k,values[i]])),year:packed.metadata.target_year,building_key:b.key,region_code:code};
    })}:packed;
    let history={},historyPromise=null,transactionPromise=null;
    const regions=catalog.regions.map(r=>({...r,loadedBucket:null})), byCode=new Map(regions.map(r=>[r.code,r]));
    const historyCache=new Map();let request=0;
    return {manifest,recommendations,generation:0,summary:{...manifest,regions},
      ensureTransactionValuations(){
        if(!transactionPromise)transactionPromise=compressed(manifest.transaction_valuation).then(data=>{
          if(data.schema_version!==1 || !data.by_key)throw Error('계약 평가 자료 형식 오류');
          for(const rec of recommendations.recommendations){const value=data.by_key[`${rec.region_code}|${rec.building_key}`];if(value)rec.transaction_valuation=value;}
          return true;
        }).catch(error=>{transactionPromise=null;throw error;});
        return transactionPromise;
      },
      ensureHistory(){
        if(!historyPromise)historyPromise=compressed(manifest.history).then(value=>{history=value;historyCache.clear();return true;}).catch(e=>{historyPromise=null;throw e;});
        return historyPromise;
      },
      async loadPeriod(year) {
        const ticket=++request, data=await compressed(manifest.periods[year]);if(ticket!==request)return false;
        const next=new Map();let count=0,trades=0,represented=0;
        for(const [code,n,values,rows,recent] of data){
          if(!byCode.has(code)||next.has(code))throw Error("지역 색인 오류");
          const addresses=rows.map(([idx,c,v,floor,floorCount])=>{
            if(!catalog.addresses[idx])throw Error("단지 색인 오류");represented+=c;
            return Object.assign(Object.create(catalog.addresses[idx]),{count:c,metrics:metrics(v),observed_floor:floor,floor_sample_count:floorCount});
          });
          next.set(code,{count:n,metrics:metrics(values),addresses,recent});count+=rows.length;trades+=n;
        }
        const c=manifest.coverage[year];
        if(count!==c.available_types||trades!==c.source_trades||represented!==c.represented_trades)throw Error("전체 결과 건수 불일치");
        for(const r of regions)r.loadedBucket=next.get(r.code)??null;
        this.generation++;
        return true;
      },
      historyBucket(code,year){
        const key=`${code}|${year}`;
        if(!historyCache.has(key))historyCache.set(key,{addresses:(history[year]?.[code]??[]).map(([idx,count,pa,pm,ppa,ppm,area])=>
          Object.assign(Object.create(catalog.addresses[idx]),{count,metrics:{price_billion:{avg:pa,median:pm},price_per_pyeong:{avg:ppa,median:ppm},area_pyeong:{avg:area}}}))});
        return historyCache.get(key);
      }
    };
  }
  return {open,compressed,metrics};
})();
