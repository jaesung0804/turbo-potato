/* Frozen, local review data. Does not access price models or personal storage. */
const WalkReview = (() => {
  const DATA_SHA256 = '7298b80d288f7340fb73e14fae2b42c5a14050bb751dcc1c2a96c2e0159235e9';
  const purposes = {station: '역', school: '초등학교', bus_stop: '버스 정류장'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const number = (value, digits = 1) => Number.isFinite(value) ? value.toLocaleString('ko-KR', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
  const nonnegative = value => Number.isFinite(value) && value >= 0;
  const close = (a, b) => Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 1e-7;
  function counts(rows) {
    return {
      complexes: new Set(rows.map(r => r.complex_id)).size, routes: rows.length,
      estimated: rows.filter(r => r.status === 'assumed_connector_total').length,
      disconnected: rows.filter(r => r.status === 'disconnected').length,
      terrain_reference: rows.filter(r => r.terrain.status === 'reference').length,
      terrain_excluded: rows.filter(r => r.terrain.status === 'excluded').length
    };
  }
  function validate(data) {
    if (data.schema_version !== 'mullae-walk-review-v1' || data.review_only !== true || data.operating_model_changed !== false || !Array.isArray(data.rows)) throw Error('검토 자료의 범위가 올바르지 않습니다.');
    const ids = new Set(), complexes = new Map();
    for (const r of data.rows) {
      if (!r || ![r.id, r.complex_id, r.name, r.address, r.destination_id, r.destination].every(x => typeof x === 'string' && x.length) || !Object.hasOwn(purposes, r.purpose) || ids.has(r.id)) throw Error('경로 식별 정보가 올바르지 않습니다.');
      ids.add(r.id);
      const group = complexes.get(r.complex_id) ?? [];
      group.push(r.purpose); complexes.set(r.complex_id, group);
      const d = r.distance, t = r.time, h = r.terrain;
      if (!d || !t || !h || !nonnegative(d.origin_m) || !nonnegative(d.destination_m) || h.is_measured_walk !== false || h.used_in_duration !== false) throw Error('거리 또는 지형의 가정이 올바르지 않습니다.');
      if (r.status === 'disconnected') {
        if (![d.network_m, d.total_m, t.base_min, t.fast_min, t.slow_min, h.forward_m, h.reverse_m, h.height_range_m].every(v => v === null) || !r.missing_reason || h.status !== 'missing') throw Error('미연결 값은 비어 있어야 합니다.');
      } else if (r.status === 'assumed_connector_total') {
        if (![d.network_m, d.total_m, t.base_min, t.fast_min, t.slow_min, h.forward_m, h.reverse_m, h.height_range_m].every(nonnegative) || d.total_m <= 0 || !close(d.total_m, d.origin_m + d.network_m + d.destination_m) || !close(t.base_min, d.total_m / 80) || !close(t.fast_min, d.total_m / 90) || !close(t.slow_min, d.total_m / 60)) throw Error('거리와 시간의 범위가 일치하지 않습니다.');
        if (!['reference', 'excluded'].includes(h.status) || (h.status === 'excluded' && !h.exclusion_reason)) throw Error('지형 검토 상태가 올바르지 않습니다.');
        for (const range of [h.forward_range_m, h.reverse_range_m]) if (!Array.isArray(range) || range.length !== 2 || !range.every(nonnegative) || range[0] > range[1]) throw Error('지형 설정 범위가 올바르지 않습니다.');
      } else throw Error('알 수 없는 연결 상태입니다.');
    }
    if ([...complexes.values()].some(v => v.length !== 3 || new Set(v).size !== 3)) throw Error('단지별 세 경로 구성이 다릅니다.');
    const actual = counts(data.rows), expected = {complexes: 14, routes: 42, estimated: 31, disconnected: 11, terrain_reference: 27, terrain_excluded: 4};
    for (const key of Object.keys(expected)) if (actual[key] !== expected[key] || data.counts?.[key] !== expected[key]) throw Error('검토 자료의 건수가 일치하지 않습니다.');
    if (!Array.isArray(data.sources) || data.sources.length !== 4 || !data.sources.every(s => typeof s.label === 'string' && typeof s.date === 'string' && /^https:\/\//.test(s.url))) throw Error('출처가 올바르지 않습니다.');
    return data;
  }
  async function load() {
    const response = await fetch('walk-review.json', {cache: 'no-cache'});
    if (!response.ok) throw Error('보행 검토 자료를 불러오지 못했습니다.');
    const raw = await response.arrayBuffer();
    if (raw.byteLength > 262144) throw Error('검토 자료의 크기가 올바르지 않습니다.');
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', raw))].map(x => x.toString(16).padStart(2, '0')).join('');
    if (hash !== DATA_SHA256) throw Error('보행 검토 자료의 무결성을 확인하지 못했습니다.');
    return validate(JSON.parse(new TextDecoder().decode(raw)));
  }
  function sortValue(r, key) {
    if (key === 'distance') return Number.isFinite(r.distance.total_m) ? r.distance.total_m : null;
    if (key === 'time') return Number.isFinite(r.time.base_min) ? r.time.base_min : null;
    if (['forward', 'reverse'].includes(key)) return r.terrain.status === 'reference' && Number.isFinite(r.terrain[key + '_m']) ? r.terrain[key + '_m'] : null;
    return r.name;
  }
  function compare(a, b, key, direction) {
    const x = sortValue(a, key), y = sortValue(b, key);
    // Missing and excluded values stay last in both directions, never Number(null).
    if (x === null || y === null) {
      if (x !== y) return x === null ? 1 : -1;
    } else {
      const delta = key === 'name' ? x.localeCompare(y, 'ko') : x - y;
      if (delta) return direction === 'desc' ? -delta : delta;
    }
    return a.name.localeCompare(b.name, 'ko') || a.destination.localeCompare(b.destination, 'ko') || a.id.localeCompare(b.id);
  }
  function selected(data, options) {
    const query = (options.query ?? '').trim().toLocaleLowerCase('ko');
    const rows = data.rows.filter(r => (options.purpose === 'all' || r.purpose === options.purpose) &&
      (options.destination === 'all' || r.destination_id === options.destination) &&
      (!query || (r.name + ' ' + r.address).toLocaleLowerCase('ko').includes(query)));
    // Terrain sorting only compares the same exact destination.
    const key = ['forward', 'reverse'].includes(options.sort) && options.destination === 'all' ? 'name' : options.sort;
    return rows.slice().sort((a, b) => compare(a, b, key, options.direction));
  }
  function detail(r) {
    const d = r.distance, t = r.time, h = r.terrain;
    const distances = '<p><b>거리의 구성</b><br>출발 직선 연결 ' + number(d.origin_m) + 'm + 지도 보행 구간 ' + (d.network_m === null ? '미연결' : number(d.network_m) + 'm') + ' + 도착 직선 연결 ' + number(d.destination_m) + 'm</p>';
    const timing = t.base_min === null ? '<p>연결 근거가 부족해 거리 합계와 시간을 표시하지 않습니다.</p>' :
      '<p>분당 80m 기준, 속도를 바꾸면 약 ' + number(t.fast_min) + '–' + number(t.slow_min) + '분입니다. 신호·계단·경사·버스 대기 시간은 포함하지 않습니다.</p>';
    const terrain = h.status === 'missing' ? '<p>경로가 없어 지형 추정도 비어 있습니다.</p>' :
      '<p>' + (h.status === 'excluded' ? '<b>비교 제외 · 검토용 계산값</b><br>' : '<b>지형 기반 추정</b><br>') +
      '갈 때 상승 ' + number(h.forward_m) + 'm, 같은 길을 반대로 이동하면 ' + number(h.reverse_m) + 'm. 높이 범위 ' + number(h.height_range_m) + 'm.</p>' +
      '<p>보간 방법·표본 간격·위치를 바꾼 설정 범위: 갈 때 ' + number(h.forward_range_m[0]) + '–' + number(h.forward_range_m[1]) + 'm / 반대 방향 ' + number(h.reverse_range_m[0]) + '–' + number(h.reverse_range_m[1]) + 'm. 실제 오차 범위나 신뢰구간은 아닙니다.</p>';
    const facility = r.purpose === 'school' ? '<p>학교 중심점 기준입니다. 교문 동선과 배정학교는 확인되지 않았습니다.</p>' :
      r.purpose === 'bus_stop' ? '<p>지도상 승강장 대표점입니다. 운영기관의 방향·노선과 정확한 승차 위치는 미확인입니다.</p>' :
      '<p>지도상 역 출구 후보입니다. 현재 출입 가능 여부와 단지의 실제 출입구 동선은 미확인입니다.</p>';
    const reported = r.bus_reported ? '<p>지도 전달 정보: ' + esc(r.bus_reported.name) + ' · ' + esc(r.bus_reported.direction) + ' · ' + esc(r.bus_reported.routes.join(', ')) + '. 운영기관 확인과 구분합니다.</p>' : '';
    return '<div class="walk-detail-content">' + distances + timing + terrain + facility + reported + '<p class="walk-small">단지·지도 2026.10.17–20 관측 · 학교 2026.10.01 기준 · 지형 파일 2025.03.20 수정(측량일 미확인). 수집 범위 안의 경로이며 전역 최단·최근접 보행 목적지는 미확인입니다.</p></div>';
  }
  function rowHtml(r) {
    const missing = r.status === 'disconnected', h = r.terrain;
    const walk = missing ? '<strong class="walk-unknown">계산 못함</strong><small>연결 근거 부족</small>' :
      '<strong>약 ' + number(r.distance.total_m, 0) + 'm</strong><small>약 ' + number(r.time.base_min) + '분</small>';
    const hill = h.status === 'reference' ? '<strong>갈 때 약 ' + number(h.forward_m) + 'm</strong><small>반대 방향 약 ' + number(h.reverse_m) + 'm</small>' :
      '<strong class="walk-unknown">' + (h.status === 'excluded' ? '비교 제외' : '자료 없음') + '</strong><small>' + (h.status === 'excluded' ? '계산값은 근거에서 확인' : '경로 미연결') + '</small>';
    const badge = missing ? '미연결' : h.status === 'excluded' ? '지형 비교 제외' : '지형 참고';
    const note = missing ? r.missing_reason : h.status === 'excluded' ? h.exclusion_reason : '대표점 기준 · 실제 보행면과 다를 수 있음';
    return '<tr data-route-id="' + esc(r.id) + '" data-status="' + esc(r.status) + '" data-terrain-status="' + esc(h.status) + '">' +
      '<th scope="row" class="walk-name"><strong>' + esc(r.name) + '</strong><small>' + esc(r.address) + '</small></th>' +
      '<td class="walk-destination" data-label="목적지"><span class="walk-purpose">' + purposes[r.purpose] + '</span><strong>' + esc(r.destination) + '</strong></td>' +
      '<td class="walk-distance" data-label="예상 거리·시간">' + walk + '</td>' +
      '<td class="walk-height" data-label="오르는 높이 · 지형 추정">' + hill + '</td>' +
      '<td class="walk-quality" data-label="확인 상태"><span class="walk-badge ' + (missing ? 'is-missing' : h.status === 'excluded' ? 'is-excluded' : 'is-reference') + '">' + badge + '</span><small>' + esc(note) + '</small></td>' +
      '<td class="walk-evidence" data-label="근거"><button type="button" data-walk-detail="walk-evidence-' + esc(r.id) + '" aria-controls="walk-evidence-' + esc(r.id) + '" aria-expanded="false">가정·근거<span class="walk-sr-only"> · ' + esc(r.name + ' ' + r.destination) + '</span></button></td></tr>' +
      '<tr class="walk-evidence-row" id="walk-evidence-' + esc(r.id) + '" hidden><td colspan="6">' + detail(r) + '</td></tr>';
  }
  function destinationOptions(data, purpose) {
    const rows = data.rows.filter(r => purpose === 'all' || r.purpose === purpose);
    const unique = [...new Map(rows.map(r => [r.destination_id, r])).values()].sort((a, b) => a.destination.localeCompare(b.destination, 'ko') || a.destination_id.localeCompare(b.destination_id));
    return unique.map(r => {
      const same = unique.filter(x => x.purpose === r.purpose && x.destination === r.destination);
      const suffix = same.length > 1 ? ' · 표시 지점 ' + (same.findIndex(x => x.destination_id === r.destination_id) + 1) : '';
      return {value: r.destination_id, label: purposes[r.purpose] + ' · ' + r.destination + suffix};
    });
  }
  async function init() {
    const status = document.getElementById('walk-load-status'), body = document.getElementById('walk-body');
    try {
      const data = await load();
      const get = id => document.getElementById('walk-' + id);
      function destinations() {
        const current = get('destination').value;
        get('destination').innerHTML = '<option value="all">모든 목적지</option>' + destinationOptions(data, get('purpose').value).map(o => '<option value="' + esc(o.value) + '">' + esc(o.label) + '</option>').join('');
        if ([...get('destination').options].some(o => o.value === current)) get('destination').value = current;
      }
      function render() {
        const terrainSortAllowed = get('destination').value !== 'all';
        for (const option of get('sort').options) if (['forward', 'reverse'].includes(option.value)) option.disabled = !terrainSortAllowed;
        if (!terrainSortAllowed && ['forward', 'reverse'].includes(get('sort').value)) get('sort').value = 'name';
        const options = {purpose: get('purpose').value, destination: get('destination').value, query: get('query').value, sort: get('sort').value, direction: get('direction').value};
        const rows = selected(data, options), c = counts(rows);
        get('rows').innerHTML = rows.map(rowHtml).join('');
        get('empty').hidden = rows.length !== 0; get('table').hidden = rows.length === 0;
        get('selected').textContent = '현재 ' + c.complexes + '개 단지 · ' + c.routes + '개 경로 / 도보 추정 ' + c.estimated + ' · 미연결 ' + c.disconnected + ' · 지형 참고 ' + c.terrain_reference + ' · 지형 비교 제외 ' + c.terrain_excluded;
        get('sort-note').textContent = terrainSortAllowed ? '같은 목적지끼리 비교합니다. 작은 높이 차이로 우열을 단정하지 마세요. 숫자 정렬의 빈 값과 높이 정렬의 비교 제외는 뒤에 표시합니다.' : '지형 정렬은 같은 목적지를 고른 뒤 사용할 수 있습니다. 숫자 정렬에서 빈 값은 뒤에 표시합니다.';
      }
      destinations();
      get('purpose').addEventListener('change', () => {destinations(); render();});
      for (const name of ['destination', 'sort', 'direction']) get(name).addEventListener('change', render);
      get('query').addEventListener('input', render);
      get('rows').addEventListener('click', event => {
        const button = event.target.closest('[data-walk-detail]');
        if (!button) return;
        const detailRow = document.getElementById(button.dataset.walkDetail);
        detailRow.hidden = !detailRow.hidden; button.setAttribute('aria-expanded', String(!detailRow.hidden));
      });
      get('reset').addEventListener('click', () => {get('purpose').value = 'all'; get('destination').value = 'all'; get('sort').value = 'name'; get('direction').value = 'asc'; get('query').value = ''; destinations(); render();});
      get('sources').innerHTML = data.sources.map(s => '<li><a href="' + esc(s.url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.label) + '</a> · ' + esc(s.date) + (s.attribution ? '<small>' + esc(s.attribution) + '</small>' : '') + '</li>').join('');
      render(); body.hidden = false;
      status.textContent = '저장된 14개 단지·42개 경로를 불러왔습니다. 가격 모델·가격 순위에는 반영하지 않습니다.';
      if (location.hash === '#walk-review') document.getElementById('walk-review').scrollIntoView();
    } catch (error) {
      body.hidden = true; status.classList.add('error'); status.setAttribute('role', 'alert');
      status.textContent = error.message + ' 값을 임의로 채우지 않았습니다.';
    }
  }
  return {validate, counts, load, sortValue, compare, selected, rowHtml, destinationOptions, init};
})();
if (typeof document !== 'undefined' && document.getElementById('walk-review')) WalkReview.init();
if (typeof module !== 'undefined') module.exports = WalkReview;
