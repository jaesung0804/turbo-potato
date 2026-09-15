const EstateReleaseStatus = (() => {
  function koreanDate(value) {
    if (!value) return '미확인';
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '미확인' : new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul'}).format(date);
  }
  function describe(manifest, now=new Date()) {
    const through = koreanDate(manifest.generated_at);
    const collected = koreanDate(manifest.collection?.fetched_at);
    const refresh = manifest.release_status?.transaction_refresh;
    const labels = {
      completed:'최근 실거래 수집 완료', skipped_missing_key:'새 실거래 수집 보류 · 인증 설정 확인 필요',
      not_requested:'이번 배포는 실거래 수집을 요청하지 않았습니다', unknown:'최근 수집 시도 상태 미기록'
    };
    const state = refresh?.status ?? 'unknown';
    const age = Math.floor((Date.parse(koreanDate(now.toISOString()))-Date.parse(through))/86400000);
    return {
      through, collected, built:koreanDate(manifest.release_status?.built_at),
      ageDays:Number.isFinite(age) && age>=0 ? age : null,
      warning:state==='skipped_missing_key', state,
      message:labels[state] ?? labels.unknown,
      text:`거래자료 ${through}까지 · 마지막 수집 ${collected} (한국시간). ${labels[state] ?? labels.unknown}.`,
    };
  }
  function render(manifest, id='release-status') {
    const node=document.getElementById(id);
    if (!node) return;
    const result=describe(manifest);
    node.textContent=result.text;
    node.classList.toggle('refresh-paused',result.warning);
  }
  return {describe, render, koreanDate};
})();
