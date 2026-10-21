/* Frozen, local review data. Does not access price models or personal storage. */
const WalkReview = (() => {
  const DATA_SHA256 = '15ad72cbf8a5f95a01388c07d2292e2fa9958083cdb8c56c98351a09992e54bd';
  const purposes = {station: '역', school: '초등학교', bus_stop: '버스 정류장'};
  const reviewCodes = ['large_detour', 'building_crossing', 'road_crossing', 'connector_dominant', 'road_attachment', 'mapped_obstacle'];
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
      terrain_excluded: rows.filter(r => r.terrain.status === 'excluded').length,
      route_review: rows.filter(r => r.route_review_status === 'needs_review').length,
      review_estimated: rows.filter(r => r.route_review_status === 'needs_review' && r.status !== 'disconnected').length,
      review_disconnected: rows.filter(r => r.route_review_status === 'needs_review' && r.status === 'disconnected').length,
      comparison_estimated: rows.filter(r => r.route_review_status !== 'needs_review' && r.status !== 'disconnected').length
    };
  }
  function validate(data) {
    if (data.schema_version !== 'mullae-walk-review-v3' || data.review_only !== true || data.operating_model_changed !== false || !Array.isArray(data.rows)) throw Error('검토 자료의 범위가 올바르지 않습니다.');
    if (data.quality_screening?.not_error_verdict !== true || data.quality_screening?.remaining_estimates_are_measured !== false || data.quality_screening?.distance_or_speed_adjusted !== false) throw Error('품질 검토의 가정이 올바르지 않습니다.');
    const ids = new Set(), complexes = new Map();
    for (const r of data.rows) {
      if (!r || ![r.id, r.complex_id, r.name, r.address, r.destination_id, r.destination].every(x => typeof x === 'string' && x.length) || !Object.hasOwn(purposes, r.purpose) || ids.has(r.id)) throw Error('경로 식별 정보가 올바르지 않습니다.');
      ids.add(r.id);
      if (r.purpose === 'station' ? typeof r.station_name !== 'string' || !r.station_name.endsWith('역') : r.station_name !== null) throw Error('역명 비교 정보가 올바르지 않습니다.');
      if (!['needs_review', 'no_specific_flag'].includes(r.route_review_status) || (r.route_review_status === 'needs_review' && !r.route_review_reason)) throw Error('경로 재검토 정보가 올바르지 않습니다.');
      if (!Array.isArray(r.route_review_reasons) || !Array.isArray(r.route_review_notes) || !r.route_review_notes.every(n => typeof n === 'string') ||
          r.route_review_reasons.some(x => !reviewCodes.includes(x.code) || typeof x.label !== 'string' || !x.label || typeof x.detail !== 'string' || !x.detail) ||
          new Set(r.route_review_reasons.map(x => x.code)).size !== r.route_review_reasons.length ||
          (r.route_review_reasons.length > 0) !== (r.route_review_status === 'needs_review') ||
          r.route_review_reason !== (r.route_review_reasons.map(x => x.label).join(' · ') || null)) throw Error('확인 필요 사유가 올바르지 않습니다.');
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
    const actual = counts(data.rows), expected = {complexes: 14, routes: 42, estimated: 31, disconnected: 11, terrain_reference: 27, terrain_excluded: 4, route_review: 30, review_estimated: 25, review_disconnected: 5, comparison_estimated: 6};
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
    if (key !== 'name' && r.route_review_status === 'needs_review') return null;
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
      (options.destination === 'all' || destinationKey(r) === options.destination) &&
      (!query || (r.name + ' ' + r.address).toLocaleLowerCase('ko').includes(query)));
    // Terrain sorting only compares one destination group (station names merge exits).
    const key = ['forward', 'reverse'].includes(options.sort) && options.destination === 'all' ? 'name' : options.sort;
    return rows.slice().sort((a, b) => comparisonGroup(a) - comparisonGroup(b) || compare(a, b, key, options.direction));
  }
  function comparisonGroup(r) {
    return r.status === 'disconnected' ? 2 : r.route_review_status === 'needs_review' ? 1 : 0;
  }
  function comparisonHtml(rows) {
    const labels = ['추정 비교 · 실측 검증 전', '확인 필요 · 거리 비교에서 제외', '미연결 · 합계와 시간 없음'];
    return labels.map((label, index) => {
      const group = rows.filter(r => comparisonGroup(r) === index);
      return group.length ? '<tr class="walk-group-heading" data-comparison-group="' + index + '"><th colspan="6" scope="rowgroup">' + label + ' <small>' + group.length + '개</small></th></tr>' + group.map(rowHtml).join('') : '';
    }).join('');
  }
  function qualityReasons(r, detailed = false) {
    if (!r.route_review_reasons.length) return '';
    return '<ul class="walk-quality-reasons">' + r.route_review_reasons.map(x => '<li>' + esc(detailed ? x.detail : x.label) + '</li>').join('') + '</ul>';
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
    const review = r.route_review_status === 'needs_review' ? '<div class="walk-review-warning"><b>확인 필요 · 비교 대표값으로 사용하지 않음</b>' + qualityReasons(r, true) + '<p>기하상 위험과 연결 의심을 표시한 것으로 실제 통행 불가나 거리 오류를 확정한 것은 아닙니다. 아래 값은 진단용으로 보존합니다.</p></div>' : '';
    const notes = r.route_review_notes.map(n => '<p class="walk-small">' + esc(n) + '</p>').join('');
    const saved = '<p><b>저장된 경로 목적지</b><br>' + esc(r.destination) + (d.total_m === null ? '' : '<br>기존 거리 합계 ' + number(d.total_m) + 'm · 시간 ' + number(t.base_min) + '분') + '</p>';
    return '<div class="walk-detail-content">' + review + saved + distances + notes + timing + terrain + facility + reported + '<p class="walk-small">단지·지도 2026.10.17–20 관측 · 학교 2026.10.01 기준 · 지형 파일 2025.03.20 수정(측량일 미확인). 수집 범위 안의 경로이며 전역 최단·최근접 보행 목적지는 미확인입니다.</p></div>';
  }
  function rowHtml(r) {
    const missing = r.status === 'disconnected', h = r.terrain, review = r.route_review_status === 'needs_review';
    const walk = missing ? '<strong class="walk-unknown">계산 못함</strong><small>연결 근거 부족</small>' : review ? '<strong class="walk-unknown">거리 비교 보류</strong><small>기존 추정은 근거에서 확인</small>' :
      '<strong>약 ' + number(r.distance.total_m, 0) + 'm</strong><small>약 ' + number(r.time.base_min) + '분</small>';
    const hill = missing ? '<strong class="walk-unknown">자료 없음</strong><small>경로 미연결</small>' : review ? '<strong class="walk-unknown">경로와 함께 재검토</strong><small>' + (h.status === 'excluded' ? '원자료 지형 비교 제외' : '원자료 지형 참고') + '</small>' : h.status === 'reference' ? '<strong>갈 때 약 ' + number(h.forward_m) + 'm</strong><small>반대 방향 약 ' + number(h.reverse_m) + 'm</small>' :
      '<strong class="walk-unknown">' + (h.status === 'excluded' ? '비교 제외' : '자료 없음') + '</strong><small>' + (h.status === 'excluded' ? '계산값은 근거에서 확인' : '경로 미연결') + '</small>';
    const badge = missing ? '미연결' : review ? '연결 확인 필요' : h.status === 'excluded' ? '지형 비교 제외' : '지형 참고';
    const note = review ? '실제 통행 불가 판정이 아닙니다.' : missing ? r.missing_reason : h.status === 'excluded' ? h.exclusion_reason : '대표점 기준 · 실제 보행면과 다를 수 있음';
    return '<tr data-route-id="' + esc(r.id) + '" data-status="' + esc(r.status) + '" data-review-status="' + esc(r.route_review_status) + '" data-terrain-status="' + esc(h.status) + '">' +
      '<th scope="row" class="walk-name"><strong>' + esc(r.name) + '</strong><small>' + esc(r.address) + '</small></th>' +
      '<td class="walk-destination" data-label="목적지"><span class="walk-purpose">' + purposes[r.purpose] + '</span><strong>' + esc(destinationLabel(r)) + '</strong></td>' +
      '<td class="walk-distance" data-label="예상 거리·시간">' + walk + '</td>' +
      '<td class="walk-height" data-label="오르는 높이 · 지형 추정">' + hill + '</td>' +
      '<td class="walk-quality" data-label="확인 상태"><span class="walk-badge ' + (review ? 'is-excluded' : missing ? 'is-missing' : h.status === 'excluded' ? 'is-excluded' : 'is-reference') + '">' + badge + '</span>' + qualityReasons(r) + '<small>' + esc(note) + '</small></td>' +
      '<td class="walk-evidence" data-label="근거"><button type="button" data-walk-detail="walk-evidence-' + esc(r.id) + '" aria-controls="walk-evidence-' + esc(r.id) + '" aria-expanded="false">가정·근거<span class="walk-sr-only"> · ' + esc(r.name + ' ' + r.destination) + '</span></button></td></tr>' +
      '<tr class="walk-evidence-row" id="walk-evidence-' + esc(r.id) + '" hidden><td colspan="6">' + detail(r) + '</td></tr>';
  }
  function destinationKey(r) {
    return r.purpose === 'station' ? 'station:' + r.station_name : r.destination_id;
  }
  function destinationLabel(r) {
    return r.purpose === 'station' ? r.station_name : r.destination;
  }
  function destinationOptions(data, purpose) {
    const rows = data.rows.filter(r => r.purpose === purpose);
    const unique = [...new Map(rows.map(r => [destinationKey(r), r])).values()]
      .sort((a, b) => destinationLabel(a).localeCompare(destinationLabel(b), 'ko') || destinationKey(a).localeCompare(destinationKey(b)));
    return unique.map(r => {
      const same = unique.filter(x => destinationLabel(x) === destinationLabel(r));
      const suffix = same.length > 1 ? ' · 지도 지점 ' + (same.findIndex(x => destinationKey(x) === destinationKey(r)) + 1) : '';
      return {value: destinationKey(r), label: destinationLabel(r) + suffix};
    });
  }
  function complexGroups(data, options = {}) {
    const query = (options.query ?? '').trim().toLocaleLowerCase('ko'), groups = new Map();
    for (const row of data.rows) {
      if (query && !(row.name + ' ' + row.address).toLocaleLowerCase('ko').includes(query)) continue;
      if (!groups.has(row.complex_id)) groups.set(row.complex_id, {id: row.complex_id, name: row.name, address: row.address, routes: {}});
      groups.get(row.complex_id).routes[row.purpose] = row;
    }
    return [...groups.values()].sort((a, b) => {
      if (Object.hasOwn(purposes, options.sort)) {
        const delta = compare(a.routes[options.sort], b.routes[options.sort], 'distance', options.direction);
        if (delta) return delta;
      }
      const delta = a.name.localeCompare(b.name, 'ko');
      return options.sort === 'name' && options.direction === 'desc' ? -delta : delta;
    });
  }
  function summaryRoute(r) {
    const review = r.route_review_status === 'needs_review', missing = r.status === 'disconnected', h = r.terrain;
    const metric = missing ? '계산 못함' : review ? '거리 비교 보류' : '약 ' + number(r.distance.total_m, 0) + 'm · ' + number(r.time.base_min) + '분';
    const terrain = missing ? '지형 자료 없음 · 연결 근거 부족' : review ? '기존 추정은 근거에서 확인' + (h.status === 'excluded' ? ' · 원자료 지형 비교 제외' : '') :
      h.status === 'excluded' ? '지형 비교 제외 · 계산값은 근거에서 확인' : '지형 참고 · 갈 때 약 ' + number(h.forward_m) + 'm 상승';
    const badge = (missing ? '<span class="walk-badge is-missing">미연결</span>' : '') + (review ? '<span class="walk-badge is-excluded">연결 확인 필요</span>' : !missing && h.status === 'excluded' ? '<span class="walk-badge is-excluded">지형 비교 제외</span>' : '');
    return '<article class="walk-route" data-summary-route-id="' + esc(r.id) + '" data-purpose="' + r.purpose + '">' +
      '<span class="walk-purpose">' + purposes[r.purpose] + '</span><h4>' + esc(destinationLabel(r)) + '</h4>' +
      '<strong class="walk-route-metric">' + metric + '</strong><p class="walk-small">' + terrain + '</p>' +
      badge + qualityReasons(r) + (review ? '<p class="walk-small">실제 통행 불가 판정은 아닙니다.</p>' : r.route_review_notes.length ? '<p class="walk-small">출입 동선·직선 연결 가정은 근거에서 확인하세요.</p>' : '') + (r.purpose === 'bus_stop' ? '<p class="walk-small">운영 노선·방향 미확인</p>' : '') +
      '<details class="walk-route-detail"><summary>경로·가정·근거</summary>' + detail(r) + '</details></article>';
  }
  function complexHtml(group) {
    return '<article class="walk-complex-card" data-complex-id="' + esc(group.id) + '"><header><h3>' + esc(group.name) +
      '</h3><p>' + esc(group.address) + '</p></header>' + Object.keys(purposes).map(p => summaryRoute(group.routes[p])).join('') + '</article>';
  }
  async function init() {
    const status = document.getElementById('walk-load-status'), body = document.getElementById('walk-body');
    try {
      const data = await load(), get = id => document.getElementById('walk-' + id);
      let view = 'complexes', purpose = 'station';
      const facilityKeys = Object.keys(purposes);
      function setTabs(names, active) {
        for (const name of names) {
          const button = get('tab-' + name);
          button.setAttribute('aria-selected', String(name === active)); button.tabIndex = name === active ? 0 : -1;
        }
      }
      function render() {
        setTabs(['complexes', 'destinations'], view); setTabs(facilityKeys, purpose);
        get('complexes').hidden = view !== 'complexes'; get('destinations').hidden = view !== 'destinations';
        const query = get('query').value;
        const groups = complexGroups(data, {query, sort: get('summary-sort').value, direction: get('summary-direction').value});
        get('complex-cards').innerHTML = groups.map(complexHtml).join('');
        get('complex-count').textContent = groups.length + '개 단지 · 각 단지에 역·초등학교·버스 경로를 함께 표시합니다. 빈 값·경로 재검토는 거리 정렬의 마지막입니다.';
        get('complex-empty').hidden = groups.length !== 0;
        for (const p of facilityKeys) {
          get('panel-' + p).hidden = p !== purpose;
          const same = get(p + '-destination').value !== 'all';
          for (const option of get(p + '-sort').options) if (['forward', 'reverse'].includes(option.value)) option.disabled = !same;
          if (!same && ['forward', 'reverse'].includes(get(p + '-sort').value)) get(p + '-sort').value = 'distance';
          const rows = selected(data, {query, purpose: p, destination: get(p + '-destination').value, sort: get(p + '-sort').value, direction: get(p + '-direction').value});
          const c = counts(rows);
          get(p + '-rows').innerHTML = comparisonHtml(rows);
          get(p + '-empty').hidden = rows.length !== 0; get(p + '-table').hidden = rows.length === 0;
          get(p + '-selected').textContent = c.complexes + '개 단지 · 추정 비교 ' + c.comparison_estimated + ' · 확인 필요로 비교 보류 ' + c.review_estimated + ' · 미연결 ' + c.disconnected + ' (접속 확인 필요 ' + c.review_disconnected + ')';
          get(p + '-sort-note').textContent = (same ? '선택한 목적지로 비교합니다. ' : '높이 정렬은 같은 목적지를 고른 뒤 사용할 수 있습니다. ') +
            '확인 필요와 미연결은 별도 구역에 표시하며 숫자 순위에 넣지 않습니다. 추정 비교군도 실제 보행 검증 전입니다. 지형 비교 제외는 높이 정렬에서 뒤로 보냅니다.';
        }
      }
      for (const p of facilityKeys) {
        get(p + '-destination').innerHTML = '<option value="all">모든 ' + purposes[p] + '</option>' +
          destinationOptions(data, p).map(o => '<option value="' + esc(o.value) + '">' + esc(o.label) + '</option>').join('');
        for (const field of ['destination', 'sort', 'direction']) get(p + '-' + field).addEventListener('change', render);
        get('tab-' + p).addEventListener('click', () => {purpose = p; render();});
        get(p + '-rows').addEventListener('click', event => {
          const button = event.target.closest('[data-walk-detail]');
          if (!button) return;
          const detailRow = document.getElementById(button.dataset.walkDetail);
          detailRow.hidden = !detailRow.hidden; button.setAttribute('aria-expanded', String(!detailRow.hidden));
        });
      }
      for (const name of ['complexes', 'destinations']) get('tab-' + name).addEventListener('click', () => {view = name; render();});
      for (const tabs of document.querySelectorAll('#walk-review [role="tablist"]')) {
        tabs.addEventListener('keydown', event => {
          const buttons = [...tabs.querySelectorAll('[role="tab"]')], current = buttons.indexOf(document.activeElement);
          if (current < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 :
            (current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
          buttons[index].click(); buttons[index].focus();
        });
      }
      for (const name of ['summary-sort', 'summary-direction']) get(name).addEventListener('change', render);
      get('query').addEventListener('input', render);
      get('reset').addEventListener('click', () => {
        get('query').value = ''; get('summary-sort').value = 'name'; get('summary-direction').value = 'asc';
        for (const p of facilityKeys) {
          get(p + '-destination').value = 'all'; get(p + '-sort').value = 'distance'; get(p + '-direction').value = 'asc';
        }
        render();
      });
      get('sources').innerHTML = data.sources.map(s => '<li><a href="' + esc(s.url) + '" target="_blank" rel="noopener noreferrer">' +
        esc(s.label) + '</a> · ' + esc(s.date) + (s.attribution ? '<small>' + esc(s.attribution) + '</small>' : '') + '</li>').join('');
      render(); body.hidden = false;
      status.textContent = '문래 14개 단지·42개 경로의 검토용 자료입니다. 가격 모델·가격 순위에는 반영하지 않습니다.';
      if (location.hash === '#walk-review') document.getElementById('walk-review').scrollIntoView();
    } catch (error) {
      body.hidden = true; status.classList.add('error'); status.setAttribute('role', 'alert');
      status.textContent = error.message + ' 값을 임의로 채우지 않았습니다.';
    }
  }
  return {validate, counts, load, sortValue, compare, selected, rowHtml, comparisonGroup, comparisonHtml, destinationKey, destinationLabel, destinationOptions, complexGroups, complexHtml, init};
})();
if (typeof document !== 'undefined' && document.getElementById('walk-review')) WalkReview.init();
if (typeof module !== 'undefined') module.exports = WalkReview;
