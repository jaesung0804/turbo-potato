// Web Run:
//   cd C:\code
//   python run_real_estate_dashboard.py
// This starts the web server and opens http://127.0.0.1:8000
// Fast web-only run:
//   python run_real_estate_dashboard.py --skip-build

const MAP_URL = "data/capital_area_adm_dong_light.geojson";
const escapeHtml = ResultPages.escape;

// 이 파일의 의도:
// - 정적 JSON 데이터만으로 지도, 필터, 매물 목록, AI 추천 후보를 렌더링합니다.
// - GitHub Pages처럼 서버 코드가 없는 환경에서도 동작하도록 모든 계산을 브라우저에서 수행합니다.
// - 대표지표 변경 시 지도 색상, 전체 매물, Hot/Cold 목록이 같은 기준으로 다시 계산됩니다.
// - 모델 미산출은 미산출로 표시하며 모든 보관 결과를 조회합니다.
const metricLabels = {
  price_billion: { title: "평균 거래가", unit: "억", digits: 1 },
  price_per_pyeong: { title: "평단가_만원", unit: "만원/평", digits: 0 },
  count: { title: "거래횟수", unit: "건", digits: 0 },
  yoy_rate: { title: "전년 대비 상승률", unit: "%", digits: 1 },
  ai_score: { title: "가격 비교점수", unit: "", digits: 1 },
  area_pyeong: { title: "전용평수", unit: "평", digits: 1 },
  age: { title: "연식", unit: "년차", digits: 0 },
  households: { title: "표시 세대수", unit: "세대", digits: 0 },
};
const state = {
  summary: null,
  recommendations: null,
  recommendationByType: new Map(),
  topologyLayer: null,
  regionByCode: new Map(),
  regionByMapCode: new Map(),
  year: "all",
  metric: "ai_score",
  activeTab: "ai",
  selectedSido: "all",
  selectedGu: "all",
  selectedDong: "all",
  selectedGroupId: null,
  selectedTypeId: null,
  areaRange: "all",
  minPriceBillion: null,
  maxPriceBillion: null,
  minHouseholds: null,
  minTradeCount: null,
  minReviewScore: null,
  recentEvidenceOnly: true,
  askingPrices: new Map(),
  ageRange: "all",
  elementary500mOnly: false,
  subwayLine: "all",
  subwayWalkMinutes: "all",
  mapMode: "global",
  search: "",
  growthLevel: "gu",
  growthPeriod: "all",
  growthSearch: "",
  growthMinRate: null,
  aiScoreLevel: "gu",
  aiScoreSearch: "",
  aiScoreMin: null,
  regionValueCache: new Map(),
  periodAddressMapCache: new Map(),
  mapDist: { min: null, mid: null, max: null },
  dataStore: null, loading: false,
};

const map = globalThis.L ? L.map("map", {
  attributionControl: false,
  dragging: true,
  doubleClickZoom: false,
  scrollWheelZoom: false,
  wheelPxPerZoomLevel: 90,
  wheelDebounceTime: 30,
  boxZoom: false,
  keyboard: true,
  zoomControl: true,
  tap: true,
  touchZoom: true,
  zoomSnap: 0.1,
  zoomDelta: 0.25,
}) : null;

async function fetchJson(url, label) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${label} 응답 오류 ${response.status}`);
  }
  return response.json();
}

function applyDarkMode(enabled) {
  document.body.classList.toggle("dark-mode", enabled);
  const button = document.getElementById("dark-mode-toggle");
  if (button) {
    button.classList.toggle("active", enabled);
    button.textContent = enabled ? "Light" : "Dark";
    button.setAttribute("aria-pressed", String(enabled));
  }
}

function setElementaryFilterChecked(checked) {
  document.querySelectorAll("#elementary-filter, #elementary-filter-mobile").forEach((input) => {
    input.checked = checked;
  });
}

function bucketFor(region) { return region?.loadedBucket ?? null; }
function historyBucket(region,year) { return state.dataStore.historyBucket(region.code,String(year)); }
function completedYears() {
 if(state.yearInfo?.summary!==state.summary)state.yearInfo={summary:state.summary,years:state.summary.years.map(Number).filter(y=>y<Number(state.summary.generated_at.slice(0,4))).sort((a,b)=>a-b)};
 return state.yearInfo.years;
}

function metricValue(region, metric = state.metric) {
  return cachedRegionMetricValue(region, metric);
}

function comparisonYears() {
  const years = state.summary.years.map(Number).sort((a, b) => b - a);
  const current = state.year === "all" ? years[0] : Number(state.year);
  const previous = years.find((year) => year < current);
  return { current: String(current), previous: previous ? String(previous) : null };
}

function fullPeriodYears() { const years=completedYears();return {start:years.length?String(years[0]):null,end:years.length?String(years.at(-1)):null}; }

function changeRate(current, previous) {
  if (current === null || current === undefined || !previous) return null;
  return ((current - previous) / previous) * 100;
}

function previousYearFor(year) {
  const target = Number(year);
  const years = state.summary.years.map(Number).sort((a, b) => b - a);
  const previous = years.find((item) => item < target);
  return previous ? String(previous) : null;
}

function regionYoyRate(region) {
  const { current, previous } = comparisonYears();
  if (!previous) return null;
  const currentBucket = historyBucket(region,current);
  const previousBucket = historyBucket(region,previous);
  if (!currentBucket || !previousBucket) return null;

  const rates = currentBucket.addresses
    .filter(areaMatches)
    .map((building) => typeYoyRate(region, building))
    .filter((value) => value !== null);

  return averageValues(rates);
}

function generatedYear() { return state.year === "all" ? Number(state.summary.generated_at.slice(0,4)) : Number(state.year); }

function buildingAge(building) {
  return building.built_year ? Math.max(0, generatedYear() - building.built_year) : null;
}

function ageCategory(building) {
  const age = buildingAge(building);
  if (age === null) return "unknown";
  if (age <= 5) return "new";
  if (age <= 15) return "semi_new";
  if (age <= 20) return "middle";
  if (age < 30) return "old";
  return "very_old";
}

function elementaryLabel(building) {
  if (!building.elementary_500m) return "-";
  const distance = building.nearest_elementary_m !== null && building.nearest_elementary_m !== undefined
    ? `${Number(building.nearest_elementary_m).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}m`
    : "500m 이내";
  return building.nearest_elementary_name ? `${building.nearest_elementary_name} ${distance}` : distance;
}

function subwayLabel(building) {
  if (!building.subway_station && !(building.subway_lines ?? []).length) return "-";
  const line = (building.subway_lines ?? []).join(", ");
  const distance = building.subway_distance_m !== null && building.subway_distance_m !== undefined
    ? `${Number(building.subway_distance_m).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}m`
    : "";
  return [line, building.subway_station, distance].filter(Boolean).join(" ");
}

function format(value, metric = state.metric) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (metric === "text") return escapeHtml(value);
  const label = metricLabels[metric];
  if (metric === "yoy_rate") {
    const sign = value > 0 ? "+" : "";
    return `${sign}${Number(value).toLocaleString("ko-KR", {
      maximumFractionDigits: label.digits,
      minimumFractionDigits: 0,
    })}${label.unit}`;
  }
  return `${Number(value).toLocaleString("ko-KR", {
    maximumFractionDigits: label.digits,
    minimumFractionDigits: 0,
  })}${label.unit}`;
}

function rateClass(value) {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return "rate-neutral";
  return value > 0 ? "rate-positive" : "rate-negative";
}

function formatHtml(value, metric = state.metric) {
  const formatted = format(value, metric);
  if (metric !== "yoy_rate" || formatted === "-") return formatted;
  return `<span class="${rateClass(value)}">${formatted}</span>`;
}

function activeRegions() {
  return state.summary.regions.filter((region) => {
    if (state.selectedSido !== "all" && region.sido_name !== state.selectedSido) return false;
    if (state.selectedGu !== "all" && region.gu_code !== state.selectedGu) return false;
    if (state.selectedDong !== "all" && region.code !== state.selectedDong) return false;
    return (bucketFor(region)?.addresses?.length ?? 0) > 0;
  });
}

function regionMatchesSearch(region) {
  const query = state.search.trim().toLowerCase();
  if (!query) return true;
  const bucket = bucketFor(region);
  const regionText = `${region.sido_name} ${region.gu_name} ${region.dong_name}`.toLowerCase();
  const buildingHit = bucket?.addresses?.some((item) =>
    `${item.address ?? ""} ${item.building_name} ${item.area_type}`.toLowerCase().includes(query),
  );
  return regionText.includes(query) || buildingHit;
}

function filteredRegions() {
  return activeRegions().filter(regionMatchesSearch);
}

function mapBaseRegions() {
  return state.summary.regions.filter((region) => {
    if (state.mapMode === "sido" && state.selectedSido !== "all" && region.sido_name !== state.selectedSido) return false;
    if (state.mapMode === "gu" && state.selectedGu !== "all" && region.gu_code !== state.selectedGu) return false;
    if (state.mapMode === "dong" && state.selectedDong !== "all" && region.code !== state.selectedDong) return false;
    return cachedRegionMetricValue(region) !== null;
  });
}

function quantile(values, q) {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return sorted[base + 1] === undefined ? sorted[base] : sorted[base] + rest * (sorted[base + 1] - sorted[base]);
}

function distribution() {
  const values = mapBaseRegions()
    .map((region) => cachedRegionMetricValue(region))
    .filter((value) => value !== null);

  if (!values.length) return { min: null, mid: null, max: null };
  if (state.metric === "ai_score") return {min:10,mid:50,max:90};
  return {
    min: Math.min(...values),
    mid: quantile(values, 0.5),
    max: Math.max(...values),
  };
}

function interpolateColor(from, to, ratio) {
  const t = Math.max(0, Math.min(1, ratio));
  const rgb = from.map((value, index) => Math.round(value + (to[index] - value) * t));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function colorFor(value, dist) {
  if (value === null || dist.min === null || dist.mid === null || dist.max === null) return "#dfe5ed";
  if (value <= dist.mid) {
    return interpolateColor([33, 102, 172], [247, 247, 247], (value - dist.min) / (dist.mid - dist.min || 1));
  }
  return interpolateColor([247, 247, 247], [178, 24, 43], (value - dist.mid) / (dist.max - dist.mid || 1));
}

function styleFeature(feature) {
  const regions = regionsForFeature(feature);
  const region = regions[0];
  const value = featureMetricValue(regions);
  const isSelectedDong = state.selectedDong !== "all" && state.selectedDong === region?.code;
  const isSelectedGu = state.selectedDong === "all" && state.selectedGu !== "all" && state.selectedGu === region?.gu_code;
  const isSelectedSido =
    state.selectedDong === "all" &&
    state.selectedGu === "all" &&
    state.selectedSido !== "all" &&
    state.selectedSido === region?.sido_name;
  const isSelected = isSelectedDong || isSelectedGu || isSelectedSido;
  const inMapScope =
    state.mapMode === "global" ||
    (state.mapMode === "sido" && state.selectedSido !== "all" && region?.sido_name === state.selectedSido) ||
    (state.mapMode === "gu" && state.selectedGu !== "all" && region?.gu_code === state.selectedGu) ||
    (state.mapMode === "dong" && state.selectedDong !== "all" && region?.code === state.selectedDong);
  const dist = state.mapDist;

  return {
    color: isSelected ? "#0f172a" : "#687b90",
    weight: isSelectedGu ? 2.6 : isSelectedSido ? 1.7 : 1.05,
    opacity: 1,
    fillColor: inMapScope ? colorFor(value, dist) : "#ffffff",
    fillOpacity: !inMapScope ? 0.18 : value === null ? 0.2 : isSelected ? 0.94 : 0.82,
    interactive: true,
  };
}

function featureCode(feature) {
  return feature.properties.ADM_CD || feature.properties.ADM_SGG_CD || feature.properties.EMD_CD;
}

function regionsForFeature(feature) { const code=String(featureCode(feature));return state.regionByMapCode.get(code)??state.regionByMapCode.get(code.slice(0,5))??[]; }

function featureMetricValue(regions) {
 const items=regions.flatMap(regionTypeItems);
 return state.metric === "count" ? (items.length?items.reduce((s,x)=>s+x.building.count,0):null) : weightedAverage(items,state.metric);
}

function updateLegend() {
  const dist = state.mapDist;
  const year = state.year === "all" ? "전체연도" : state.year;
  document.getElementById("legend-title").textContent =
    `${year} · ${mapScopeLabel()} · ${metricLabels[state.metric].title}`;
  document.getElementById("legend-min").textContent = format(dist.min);
  document.getElementById("legend-mid").textContent = format(dist.mid);
  document.getElementById("legend-max").textContent = format(dist.max);
  const note=document.getElementById('legend-explanation');
  if(note)note.textContent=state.metric==='ai_score'?'파랑: 기준보다 높은 거래가격 · 50: 중립 · 빨강: 기준보다 낮은 거래가격. 지역 평균이며 미래 상승률이 아닙니다.':'색은 선택한 지표의 지역 평균입니다. 회색은 자료가 부족한 지역입니다.';
  document.querySelectorAll("[data-map-mode]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.mapMode === state.mapMode);
  });
}

function guName(code) {
  return state.summary.regions.find((region) => region.gu_code === code)?.gu_name ?? "전체";
}

function selectedSidoLabel() {
  return state.selectedSido === "all" ? "수도권 전체" : state.selectedSido;
}

function areaRangeLabel() {
  const labels = {
    all: "전체 평형",
    lte14: "14평 이하",
    gt14_lte26: "14평 초과 26평 이하",
    gt26_lte34: "26평 초과 34평 이하",
    gt34: "34평 초과",
  };
  return labels[state.areaRange] ?? "전체 평형";
}

function priceFilterLabel() {
  if (state.minPriceBillion === null && state.maxPriceBillion === null) return "전체 가격";
  if (state.minPriceBillion !== null && state.maxPriceBillion !== null) {
    return `${format(state.minPriceBillion, "price_billion")} 이상 ${format(state.maxPriceBillion, "price_billion")} 이하`;
  }
  if (state.minPriceBillion !== null) return `${format(state.minPriceBillion, "price_billion")} 이상`;
  return `${format(state.maxPriceBillion, "price_billion")} 이하`;
}

function householdFilterLabel() {
  return state.minHouseholds === null ? "세대수 조건 없음" : `표시 세대수 ${state.minHouseholds.toLocaleString("ko-KR")}세대 이상`;
}

function tradeCountFilterLabel() {
  return state.minTradeCount === null ? "전체 거래수" : `${state.minTradeCount.toLocaleString("ko-KR")}건 이상`;
}

function ageFilterLabel() {
  const labels = {
    all: "전체 연식",
    new: "신축 0~5년",
    semi_new: "준신축 5~15년",
    middle: "중간연식 15~20년",
    old: "구축 20~30년",
    very_old: "노후 구축 30년+",
  };
  return labels[state.ageRange] ?? labels.all;
}

function elementaryFilterLabel() {
  return state.elementary500mOnly ? "초품아만" : "초품아 전체";
}

function subwayFilterLabel() {
  const line = state.subwayLine === "all" ? "전체 호선" : state.subwayLine;
  const walk = state.subwayWalkMinutes === "all" ? "전체 역세권" : `도보 ${state.subwayWalkMinutes}분`;
  return `${line} · ${walk}`;
}

function mapScopeLabel() {
  if (state.mapMode === "sido") {
    return state.selectedSido === "all" ? "선택 시도 없음" : `${state.selectedSido} 기준`;
  }
  if (state.mapMode === "gu") {
    return state.selectedGu === "all" ? "선택 구 없음" : `${guName(state.selectedGu)} 기준`;
  }
  if (state.mapMode === "dong") {
    const region = state.regionByCode.get(state.selectedDong);
    return region ? `${escapeHtml(region.gu_name)} ${escapeHtml(region.dong_name)} 기준` : "선택 동 없음";
  }
  return "수도권 전체";
}

function typeId(region, building) {
  return `${region.code}|${encodeURIComponent(building.key ?? building.address)}`;
}

function groupId(region,building) { return `${region.code}|${encodeURIComponent(building.complex_key ?? building.building_name)}`; }

function areaPyeong(building) {
  return building.metrics?.area_pyeong?.avg ?? null;
}

function priceBillion(building) {
  return building.metrics?.price_billion?.median ?? building.metrics?.price_billion?.avg ?? null;
}

function areaMatches(building) {
  const area = areaPyeong(building);
  if (area === null) return state.areaRange === "all";
  if (state.areaRange === "lte14") return area <= 14;
  if (state.areaRange === "gt14_lte26") return area > 14 && area <= 26;
  if (state.areaRange === "gt26_lte34") return area > 26 && area <= 34;
  if (state.areaRange === "gt34") return area > 34;
  return true;
}

function comparisonPriceForItem(building, region) {
  if(state.year===state.recommendations?.target_year) {
    const rec=region?aiRecommendationForItem({region,building}):null;
    return valuationComparison(rec)?.comparison_price_billion ?? (rec?.recent_price_comparison?.status==='available'?rec.recent_price_comparison.median_price_billion:null);
  }
  return priceBillion(building);
}

function priceMatches(building, region) {
  const price = comparisonPriceForItem(building, region);
  if (state.minPriceBillion !== null && (price === null || price < state.minPriceBillion)) return false;
  if (state.maxPriceBillion !== null && (price === null || price > state.maxPriceBillion)) return false;
  return true;
}

function householdsMatch(building) {
  if (state.minHouseholds === null) return true;
  return building.households !== null && building.households !== undefined && building.households >= state.minHouseholds;
}

function tradeCountMatches(building) {
  return state.minTradeCount === null || building.count >= state.minTradeCount;
}

function ageMatches(building) {
  return state.ageRange === "all" || ageCategory(building) === state.ageRange;
}

function elementaryMatches(building) {
  return !state.elementary500mOnly || building.elementary_500m === true;
}

function subwayMatches(building) {
  if (state.subwayLine !== "all" && !(building.subway_lines ?? []).includes(state.subwayLine)) return false;
  if (state.subwayWalkMinutes === "all") return true;
  const distance = building.subway_distance_m;
  if (distance === null || distance === undefined) return false;
  return distance <= Number(state.subwayWalkMinutes) * 80;
}

function reviewScoreMatches(building, region) {
  if (state.minReviewScore === null) return true;
  const score = region ? aiScoreForItem({region, building}) : null;
  return Number.isFinite(score) && score >= state.minReviewScore;
}

function hasRecentEvidence(rec) {
  const c=valuationComparison(rec),v=currentValuation(rec);
  return Boolean(c && v && v.recent_trade_count>=3 && c.comparison_trade_count>=3);
}

function recentEvidenceMatches(building, region) {
  return !state.recentEvidenceOnly || state.year!==state.recommendations?.target_year || hasRecentEvidence(region?aiRecommendationForItem({region,building}):null);
}

function buildingMatchesFilters(building, region) {
  return recentEvidenceMatches(building, region) && areaMatches(building) && priceMatches(building, region) && householdsMatch(building) && tradeCountMatches(building) && ageMatches(building) && elementaryMatches(building) && subwayMatches(building) && reviewScoreMatches(building, region);
}

function viewKey(){return JSON.stringify([state.dataStore?.generation,state.year,state.metric,state.selectedSido,state.selectedGu,state.selectedDong,state.areaRange,state.minPriceBillion,state.maxPriceBillion,state.minHouseholds,state.minTradeCount,state.minReviewScore,state.recentEvidenceOnly,state.ageRange,state.elementary500mOnly,state.subwayLine,state.subwayWalkMinutes,state.search]);}
function viewCache(){const key=viewKey();if(state.view?.key!==key||state.view?.summary!==state.summary){state.view={key,summary:state.summary};state.regionValueCache.clear();}return state.view;}
function typeItems() {
 const cache=viewCache();if(cache.items)return cache.items;
 const q=state.search.trim().toLowerCase();
 return cache.items=activeRegions().flatMap(region=>(bucketFor(region)?.addresses??[]).filter(building=>buildingMatchesFilters(building,region))
  .filter(b=>!q||`${region.sido_name} ${region.gu_name} ${region.dong_name} ${b.key} ${b.building_name} ${b.area_type}`.toLowerCase().includes(q)).map(building=>({region,building})));
}

function regionTypeItems(region) {
  const bucket = bucketFor(region);
  return (bucket?.addresses ?? [])
    .filter(building=>buildingMatchesFilters(building,region))
    .map((building) => ({ region, building }));
}

function cachedRegionMetricValue(region, metric = state.metric) {
  if (!region) return null;
  const key = `${region.code}|${metric}`;
  if (state.regionValueCache.has(key)) return state.regionValueCache.get(key);
  const value = regionFilteredMetricValue(region, metric);
  state.regionValueCache.set(key, value);
  return value;
}

function regionFilteredMetricValue(region, metric = state.metric) {
  const items = regionTypeItems(region);
  if (!items.length) return null;
  if (metric === "count") return items.reduce((sum, item) => sum + item.building.count, 0);
  if (metric === "yoy_rate") {
    return averageValues(items.map((item) => buildingYoyRate(item)).filter((value) => value !== null));
  }
  if (metric === "ai_score") {
    return averageValues(items.map((item) => aiScoreForItem(item)).filter((value) => value !== null));
  }
  return weightedAverage(items, metric);
}

function buildingValue(item, metric = state.metric) {
  if (metric === "count") return item.building.count;
  if (metric === "yoy_rate") return buildingYoyRate(item);
  if (metric === "ai_score") return aiScoreForItem(item);
  return item.building.metrics?.[metric]?.avg ?? null;
}

function recommendationKey(regionCode, buildingKey) {
  return `${regionCode}|${buildingKey}`;
}

function aiRecommendationForItem(item) { if(state.year!==state.recommendations?.target_year)return null;return state.recommendationByType.get(recommendationKey(item.region.code,item.building.key ?? item.building.address)) ?? null; }

function valuationComparison(rec) {
  const c=rec?.valuation_comparison;
  return c?.status==='available' && Number.isFinite(c.score) && c.neutral_price_billion>0 && c.comparison_price_billion>0 ? c : null;
}
function aiScoreForItem(item) { return valuationComparison(aiRecommendationForItem(item))?.score ?? null; }

function currentValuation(rec) {
  const v=rec?.current_valuation;
  return v?.status==='available' && Number.isFinite(v.price_billion) && v.price_billion>0 ? v : null;
}

function canonicalPrice(value) { return Number.isFinite(value) && value>0 ? Math.round(value*10000)/10000 : null; }
function neutralPrice(rec) { return canonicalPrice(valuationComparison(rec)?.neutral_price_billion ?? currentValuation(rec)?.price_billion); }
function reviewScoreAtPrice(rec, price) {
  const neutral=neutralPrice(rec),observed=canonicalPrice(price),scale=valuationComparison(rec)?.score_error_scale ?? currentValuation(rec)?.score_error_scale;
  if (!(observed>0 && neutral>0 && Number.isFinite(scale) && scale>0)) return null;
  return Math.round((50+40*Math.tanh(Math.log(neutral/observed)/scale))*10)/10;
}
function discountLabel(c) {
  if (!c || !Number.isFinite(c.discount_pct)) return '가격 차이 미산출';
  if (Math.abs(c.discount_pct)<.05) return '기준가와 비슷';
  return `기준가보다 ${Math.abs(c.discount_pct).toFixed(1)}% ${c.discount_pct>0?'낮음':'높음'}`;
}

function totalPriceLabel(value) {
  return Number.isFinite(value) && value>0 ? `${value.toLocaleString('ko-KR',{minimumFractionDigits:2,maximumFractionDigits:4})}억` : '미산출';
}

function householdEvidenceNote(building) {
  if(!building.households_verified)return '세대수는 기존 시설 자료의 값입니다. 단지 전체와 동별 수치의 구분·지번 대조가 완료되지 않아 세대수 대비 거래 회전율이나 모델 가중치에 사용하지 않습니다.';
  const source=/^https:\/\//.test(building.household_source_url??'')?` · <a href="${escapeHtml(building.household_source_url)}" target="_blank" rel="noopener noreferrer">공식 출처</a>`:'';
  return `공식 단지 전체 세대수 · 관측 시점 ${escapeHtml(building.household_observed_at??'미확인')}${source}. 도로명·지번과 대조한 현재 표시값이며 과거 모델이나 세대수 대비 거래 회전율에는 사용하지 않습니다.`;
}

function askingPriceResult(rec, text) {
  if (!text.trim()) return '<p class="score-note">비교할 실거래가격을 입력하세요. 예: 8억 5천만원 → 8.5</p>';
  const price=canonicalPrice(Number(text)),neutral=neutralPrice(rec);
  if (!(price>0)) return '<p class="score-note">0보다 큰 가격을 억원 단위로 입력하세요.</p>';
  if (!(neutral>0)) return '<p class="score-note">이 평형의 현재 기준가격이 아직 없습니다.</p>';
  const score=reviewScoreAtPrice(rec,price),diff=price-neutral,pct=diff/neutral*100;
  const amount=Math.abs(diff)<.01?`${Math.round(Math.abs(diff)*10000).toLocaleString('ko-KR')}만원`:totalPriceLabel(Math.abs(diff));
  const comparison=Math.abs(diff)<.00005?'50점 기준가와 같습니다.':`기준가보다 ${amount} (${Math.abs(pct).toFixed(2)}%) ${diff>0?'높습니다':'낮습니다'}.`;
  return `<div class="metric-grid">${metricCard('입력한 가격',totalPriceLabel(price),'text')}${metricCard('동일 기준 가격 비교점수',score,'ai_score')}</div><p class="price-verdict">${comparison}</p>`;
}

function priceConfidenceLabel(rec) {
  const q=currentValuation(rec)?.confidence;
  if(!q || !/^[ABCD]$/.test(q.grade??''))return '가격 신뢰도 미산출';
  return `가격 신뢰도 ${q.grade}${q.status==='available'?'':' · 범위 미검증'}`;
}

function priceConfidencePanel(rec) {
  const q=currentValuation(rec)?.confidence;
  if(!q)return '';
  if(q.status!=='available')return `<p class="score-note">${escapeHtml(priceConfidenceLabel(rec))} · 검증 근거가 충분한 범위는 아직 없습니다.</p>`;
  return `<div class="confidence-panel"><h3>${escapeHtml(priceConfidenceLabel(rec))}</h3><p><b>참고 가격 범위 ${totalPriceLabel(q.lower_price_billion)} ~ ${totalPriceLabel(q.upper_price_billion)}</b></p><p class="score-note">과거 오차로 보정한 목표 80% 범위입니다. 2026년 3~7월 실제 포함률은 전체 77.2%, 이 등급 ${Number(q.historical_grade_coverage_pct).toFixed(1)}%였습니다. 거래수·거래 경과일·가격 분산·주변 비교 근거·층 등을 함께 반영합니다.</p><p class="score-note">A: 범위 상단 +10% 이내 · B: +20% 이내 · C: +35% 이내 · D: 그보다 넓음. 기준가 주변의 로그 대칭 범위입니다. 실제 거래 층의 과거 오차로 검증했으므로 대표 층·비슷한 상태끼리 비교하세요. 상승 가능성이나 가격 점수와는 별개이며 개별 매물의 정확도를 보장하지 않습니다. <a href="model.html#access-confidence">등급 검증 →</a></p></div>`;
}

function askingPricePanel(selected) {
  const rec=aiRecommendationForItem(selected),v=currentValuation(rec),c=valuationComparison(rec),recent=rec?.recent_price_comparison;
  if (!v) return `<section class="asking-price-panel"><h3>현재 가격 비교</h3><p>현재 기준가를 산출할 거래 이력이 부족합니다. 아래 실거래 통계에서 가격·거래수·층을 확인하세요.</p>${transactionValuationPanel(rec)}</section>`;
  const key=state.year+'|'+typeId(selected.region,selected.building);
  const text=state.askingPrices.get(key)??(c?String(c.comparison_price_billion):'');
  const period=recent?`${recent.window_start} ~ ${recent.window_end}`:'기간 미확인';
  const comparisonNote=c?`${escapeHtml(v.month)} 기준가격과 ${escapeHtml(period)} 계약 ${c.comparison_trade_count}건의 중앙가를 비교합니다. 계약월별 소급 재평가는 아래에서 따로 확인합니다.`:`${escapeHtml(period)}에 공개된 자료의 계약이 없어 목록 점수는 미산출입니다. 연간 중앙가로 대체하지 않습니다. 비교할 가격을 직접 입력할 수 있습니다.`;
  return `<section class="asking-price-panel" aria-label="현재 기준가와 실거래가격 비교"><h3>현재 기준가와 최근 실거래가격 비교</h3>
    <div class="metric-grid">${metricCard('현재 50점 기준가 · 총액',totalPriceLabel(v.price_billion),'text')}${metricCard('최근 90일 실거래 중앙가',totalPriceLabel(c?.comparison_price_billion),'text')}${metricCard('가격 비교점수 · 목록과 동일',c?.score??null,'ai_score')}${metricCard('기준가 대비 가격 차이',discountLabel(c),'text')}</div>
    <p class="score-note">${comparisonNote} 자료 마감 ${escapeHtml(recent?.data_through??'미확인')} · 양끝 날짜 포함 90일입니다. 연간 실거래 중앙가는 아래 통계에 별도로 표시합니다.</p>
    <p class="score-note">적용 모델: ${escapeHtml(v.model_version??'버전 미확인')} · ${v.model_version==='estate-nowcast-full-history-v2'?'전체 과거 재학습 · 상대가격·최근성 반영':'기존 월별 가격 모델'}</p><div class="evidence-strip"><span>비교 기간 ${c?.comparison_trade_count??0}건</span><span>기준가 입력 ${escapeHtml(v.recent_history_start??'')} ~ ${escapeHtml(v.feature_cutoff)} · ${v.recent_trade_count}건 · ${v.active_trade_days}개 거래일</span><span>마지막 입력 거래 ${v.last_trade_age_days??'미확인'}일 경과 · ${escapeHtml(v.month)} 월초 기준</span><span>대표 ${v.floor==null?'층 미확인':v.floor+'층'}</span></div>
    <p class="score-note">${hasRecentEvidence(rec)?'최근 근거 조건 충족':'자료 희소 · 기본 추천 목록에서 제외'} · 기준가 입력과 비교 기간이 각각 3건 이상인 조건은 탐색용이며 정확도를 보장하지 않습니다. 거래수는 가격 점수를 50으로 줄이지 않습니다.</p>
    <p class="score-note">기준가는 31일 신고 지연을 가정한 월별 추정입니다. 대표 층은 입력 마감까지 과거 365일 중앙층으로 유지하며, 최근 비교 거래의 층으로 다시 추정한 값이 아닙니다. 입력 이력의 90일 경과 상한은 기존 학습 정의를 유지합니다.</p>
    ${priceConfidencePanel(rec)}
    <div class="asking-price-controls"><label><span>비교할 실거래가격 (억)</span><input id="asking-price-input" type="number" min="0" step="0.0001" placeholder="예: 8.5" value="${escapeHtml(text)}" aria-describedby="asking-price-note"/></label><button id="use-neutral-price" type="button">50점 가격 넣기</button></div>
    <div id="asking-price-result" role="status" aria-live="polite">${askingPriceResult(rec,text)}</div>
    <p id="asking-price-note" class="score-note">위 최근 중앙가를 넣으면 목록과 같은 점수가 나옵니다. 50점 가격을 넣으면 정확히 50점입니다. 금액은 만원 단위로 통일합니다. 동일 면적·비슷한 층끼리 비교하고 차이율을 먼저 읽으세요.</p>
    ${transactionValuationPanel(rec)}
    ${marketContextPanel(selected.region)}
  </section>`;
}

function settleTransactionValuations(error=null) {
  state.transactionLoading=false;
  state.transactionError=error?.message??null;
  // The shared ledger hydrates every candidate. A selection made while it was
  // loading must receive the data too, rather than waiting for the old selection.
  if(state.selectedTypeId)renderSelectedRegion();
}

function transactionValuationPanel(rec) {
  const t=rec?.transaction_valuation;
  if(t?.status!=='available')return '';
  if(!Array.isArray(t.recent_transactions))return `<section class="transaction-panel"><h3>계약월별 재평가</h3><p class="score-note" role="status">${state.transactionError?'계약별 자료를 불러오지 못했습니다.':`${t.trade_count}건의 계약월별 재평가를 불러오는 중입니다…`}</p>${state.transactionError?'<button id="retry-transaction-valuation" type="button">다시 불러오기</button>':''}</section>`;
  const rows=(t.recent_transactions??[]).map(r=>`<tr><td>${escapeHtml(r.contract_date)}</td><td>${r.floor==null?'—':r.floor+'층'}</td><td>${totalPriceLabel(r.price_billion)}</td><td>${totalPriceLabel(r.neutral_price_billion)}</td><td>${format(r.score,'ai_score')}</td><td>${discountLabel(r)}</td></tr>`).join('');
  const months=(t.monthly??[]).map(r=>`<tr><td>${escapeHtml(r.month)}</td><td>${r.trade_count}</td><td>${format(r.median_score,'ai_score')}</td><td>${discountLabel({discount_pct:r.median_discount_pct})}</td></tr>`).join('');
  return `<section class="transaction-panel"><h3>계약월별 가격 재평가</h3><p class="score-note">각 계약 월 이전에 반영 가능한 실거래와 해당 거래의 층으로 기준가를 계산합니다. ${t.method==='retrospective_policy_revaluation'?'2026년 9월 9일 선택한 모델 사양을 과거 입력에 적용한 소급 분석입니다. 당시 공개했던 예측이나 실현 투자 성과가 아닙니다.':'현재 기준가와의 비교와는 입력 시점이 다릅니다.'}</p><div class="table-scroll"><table><thead><tr><th>최근 계약일</th><th>층</th><th>실거래가</th><th>계약월 50점 기준가</th><th>재평가 점수</th><th>재평가 가격 차이</th></tr></thead><tbody>${rows}</tbody></table></div><details><summary>월별 계약 재평가 · ${t.trade_count}건</summary><p class="score-note">월별 점수는 각 거래 점수의 중앙값입니다. 중앙 실거래가와 중앙 기준가를 나눠 재계산한 값이 아닙니다.</p><div class="table-scroll"><table><thead><tr><th>계약 월</th><th>거래수</th><th>재평가 점수 중앙값</th><th>재평가 가격 차이 중앙값</th></tr></thead><tbody>${months}</tbody></table></div></details></section>`;
}

function marketContextPanel(region) {
  const context=state.dataStore?.manifest?.market_context;
  if(!context||state.year!==state.recommendations?.target_year)return '';
  const code=String(region.gu_code??''),data=context.regions?.[code]??context.regions?.[code.slice(0,2)];
  if(!data)return '';
  const change=n=>Number.isFinite(n)?`${n>0?'+':''}${n.toFixed(2)}%`:'미확인';
  return `<div class="market-context"><strong>${escapeHtml(data.name)}의 매매·전세 흐름</strong><p>KB 아파트 지수 · ${escapeHtml(context.reference_month)} 기준 · 최근 3개월 매매 ${change(data.sale_3m_pct)}, 전세 ${change(data.rent_3m_pct)}</p><p class="score-note">지역 흐름을 비교하는 참고 자료입니다. 이 단지의 전세가율이나 미래 상승 확률을 뜻하지 않습니다. <a href="model.html#market-research">50점 가격에 추가 가중하지 않은 검증 이유 →</a></p></div>`;
}

function referenceBasisLabel(rec) {
  const b=rec?.reference_basis;
  if (!b) return '';
  if (b.kind==='same_complex_area') return `모델 비교 기준: 같은 단지 ${b.year}년 전용 ${b.area_pyeong.toFixed(1)}평 ${b.trade_count}건의 중앙 평단가. `;
  if (b.kind==='exact_prior'||b.kind==='exact_history') return `모델 비교 기준: 동일 단지·전용면적의 ${b.year}년 중앙 평단가. `;
  return b.kind==='external_peer'?'모델 비교 기준: 과거 주변 단지 가격. ':'모델 비교 기준: 과거 학습자료 중앙 가격. ';
}

function groupAiScore(group) { const values=group.types.map(aiScoreForItem).filter(x=>x!==null);return values.length?Math.max(...values):null; }

function clampScore(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, value));
}



function buildingYoyRate(item) {
  return typeYoyRate(item.region, item.building);
}

function typeYoyRate(region, building) {
  const { current, previous } = comparisonYears();
  return typeYoyRateForYears(region, building, current, previous);
}

function typeYoyRateForYears(region,building,current,previous) {
 if(!current||!previous||!completedYears().includes(Number(current)))return null;
 const key=building.key??building.address,a=periodAddressMap(region,current).get(key),b=periodAddressMap(region,previous).get(key);
 if((a?.count??0)<3||(b?.count??0)<3)return null;
 return changeRate(a.metrics.price_per_pyeong.median??a.metrics.price_per_pyeong.avg,b.metrics.price_per_pyeong.median??b.metrics.price_per_pyeong.avg);
}

function periodAddressMap(region,year) {
 const key=`${region.code}|${year}`;if(!state.periodAddressMapCache.has(key))state.periodAddressMapCache.set(key,new Map(historyBucket(region,year).addresses.map(b=>[b.key??b.address,b])));
 return state.periodAddressMapCache.get(key);
}

function typePeriodRate(region,building) {const {start,end}=fullPeriodYears();if(!start||!end||start===end)return null;return typeYoyRateForYears(region,building,end,start);}

function regionPeriodRate(region) {
  const { end } = fullPeriodYears();
  const endBucket = end ? historyBucket(region,end) : null;
  if (!endBucket) return null;
  const rates = endBucket.addresses
    .filter(building=>buildingMatchesFilters(building,region))
    .map((building) => typePeriodRate(region, building))
    .filter((value) => value !== null);
  return averageValues(rates);
}

function regionYearRate(region, year) {
  const previous = previousYearFor(year);
  const currentBucket = historyBucket(region,year);
  if (!previous || !currentBucket) return null;
  const rates = currentBucket.addresses
    .filter(building=>buildingMatchesFilters(building,region))
    .map((building) => typeYoyRateForYears(region, building, year, previous))
    .filter((value) => value !== null);
  return averageValues(rates);
}

function regionGrowthRateForRanking(region) {
  if (state.growthPeriod === "all") return regionPeriodRate(region);
  return regionYearRate(region, state.growthPeriod);
}

function averageValues(values) {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function weightedAverage(items, metric) {
  if (metric === "yoy_rate") {
    return averageValues(items.map((item) => buildingYoyRate(item)).filter((value) => value !== null));
  }
  if (metric === "ai_score") {
    return averageValues(items.map((item) => aiScoreForItem(item)).filter((value) => value !== null));
  }

  let totalWeight = 0;
  let total = 0;
  for (const item of items) {
    const value = buildingValue(item, metric);
    const weight = item.building.count || 1;
    if (value === null) continue;
    total += value * weight;
    totalWeight += weight;
  }
  return totalWeight ? total / totalWeight : null;
}

function groupedBuildings() {
  const cache=viewCache();if(cache.groups)return cache.groups;
  const groups = new Map();
  for (const item of typeItems()) {
    const id = groupId(item.region, item.building);
    if (!groups.has(id)) {
      groups.set(id, {
        id,
        region: item.region,
        building_name: item.building.building_name,
        types: [],
        count: 0,
      });
    }
    const group = groups.get(id);
    group.types.push(item);
    group.count += item.building.count;
  }

  return cache.groups=[...groups.values()]
    .map((group) => {
      const types = group.types.sort((a, b) => (areaPyeong(b.building) ?? -1) - (areaPyeong(a.building) ?? -1));
      return {
        ...group,
        types,
        value: state.metric === "count" ? group.count : state.metric === "ai_score" ? groupAiScore({ ...group, types }) : weightedAverage(types, state.metric),
        typeCount: types.length,
        latestBuiltYear: types.find((item) => item.building.built_year)?.building.built_year ?? null,
      };
    })
    .sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity) || String(a.id ?? a.key).localeCompare(String(b.id ?? b.key)));
}

function selectedGroup(groups = groupedBuildings()) {
  if (!state.selectedGroupId) return null;
  return groups.find((group) => group.id === state.selectedGroupId) ?? null;
}

function selectedType(groups = groupedBuildings()) {
  const group = selectedGroup(groups);
  if (!group) return null;
  const selected = group.types.find((item) => typeId(item.region, item.building) === state.selectedTypeId);
  return selected ?? group.types[0] ?? null;
}

function renderAssetLists(groups=groupedBuildings()) {
 const valid=groups.filter(g=>g.value!==null),n=Math.max(1,Math.ceil(valid.length*.3));
 document.getElementById('all-count').textContent=groups.length.toLocaleString('ko-KR');
 document.getElementById('hot-count').textContent=Math.min(n,valid.length).toLocaleString('ko-KR');document.getElementById('cold-count').textContent=Math.min(n,valid.length).toLocaleString('ko-KR');
 if(state.activeTab==='all')renderGroupList('all-list','all-count',groups);
 if(state.activeTab==='hot')renderGroupList('hot-list','hot-count',valid.slice(0,n));
 if(state.activeTab==='cold')renderGroupList('cold-list','cold-count',[...valid].reverse().slice(0,n));
}

function recommendationMatchesFilters(item) {
  if (!item) return false;
  if (state.year !== "all" && item.year !== state.year) return false;
  if (state.selectedSido !== "all" && item.sido_name !== state.selectedSido) return false;
  if (state.selectedGu !== "all" && item.gu_code !== state.selectedGu) return false;
  if (state.selectedDong !== "all" && item.region_code !== state.selectedDong) return false;

  const pseudoBuilding = {
    key: item.building_key,
    count: item.trade_count,
    households: item.households,
    elementary_500m: item.elementary_500m,
    subway_lines: item.subway_lines ?? [],
    subway_distance_m: item.subway_distance_m,
    metrics: {
      price_billion: { avg: item.price_billion },
      area_pyeong: { avg: item.area_pyeong },
    },
  };
  return buildingMatchesFilters(pseudoBuilding, {code:item.region_code});
}

function renderAiRecommendations() {
 const cache=viewCache(),rows=cache.ai??(cache.ai=typeItems().map(type=>({type,rec:aiRecommendationForItem(type)})).filter(x=>valuationComparison(x.rec)).sort((a,b)=>aiScoreForItem(b.type)-aiScoreForItem(a.type)||a.rec.building_key.localeCompare(b.rec.building_key)));
 document.getElementById("ai-count").textContent=rows.length.toLocaleString("ko-KR");if(state.activeTab!=='ai')return;const page=ResultPages.view("ai-list",rows);
 document.getElementById("ai-list").innerHTML=page.rows.length?page.rows.map(({type,rec})=>{const c=valuationComparison(rec),v=currentValuation(rec);return `<li><button type="button" class="ai-row" data-group-id="${escapeHtml(groupId(type.region,type.building))}" data-type-id="${escapeHtml(typeId(type.region,type.building))}"><span class="ai-main"><strong>${escapeHtml(rec.building_name)}</strong><small>${escapeHtml(rec.gu_name)} ${escapeHtml(rec.dong_name)} · ${escapeHtml(rec.area_type)}</small><small class="neutral-price">50점 기준가 ${totalPriceLabel(c.neutral_price_billion)} · 비교 실거래 ${totalPriceLabel(c.comparison_price_billion)}</small><span class="discount-value">${discountLabel(c)}</span><small>${escapeHtml(c.valuation_month)} 기준가 ↔ 최근 90일 중앙가 · ${c.comparison_trade_count}건</small><small>${escapeHtml(c.comparison_window_start)} ~ ${escapeHtml(c.comparison_window_end)}</small><small>${escapeHtml(priceConfidenceLabel(rec))}</small><small>${hasRecentEvidence(rec)?'최근 근거 조건 충족':'자료 희소'} · 기준가 입력 ${v?.recent_trade_count??0}건 · 마지막 입력 ${v?.last_trade_age_days??'미확인'}일 전</small></span><span class="ai-score"><small>가격 비교</small><strong>${format(c.score,"ai_score")}</strong><small>50 = 기준가</small></span></button></li>`;}).join(""):`<li class="growth-empty">${state.year===state.recommendations?.target_year?'현재 조건에 맞는 가격 비교 결과가 없습니다. 자료가 희소한 단지는 거래 근거 범위를 ‘전체 평형 보기’로 바꾸고, 점수 조건을 풀어 거래 통계를 확인하세요.':'과거 연도는 실거래 통계만 제공합니다. 현재 가격 비교는 최신 연도를 선택하세요.'}</li>`;
}

function renderGroupList(listId,countId,groups) {
 document.getElementById(countId).textContent=groups.length.toLocaleString("ko-KR");const page=ResultPages.view(listId,groups);
 document.getElementById(listId).innerHTML=page.rows.map(g=>`<li><button type="button" class="asset-row" data-group-id="${escapeHtml(g.id)}"><span>${escapeHtml(g.building_name)}</span><small>${escapeHtml(g.region.gu_name)} ${escapeHtml(g.region.dong_name)} · ${g.typeCount}개 평형</small><strong>${formatHtml(g.value)} · ${g.count.toLocaleString("ko-KR")}건</strong></button></li>`).join("");
}

function renderSummary(groups=groupedBuildings()) {document.getElementById("total-used").textContent=typeItems().reduce((s,r)=>s+r.building.count,0).toLocaleString("ko-KR");document.getElementById("total-buildings").textContent=groups.length.toLocaleString("ko-KR");}

function renderSelectedRegion(groups = groupedBuildings()) {
  const group = selectedGroup(groups);
  const selected = selectedType(groups);
  document.getElementById('selected-region').hidden = !(group && selected);

  if (group && selected) {
    const { region, building } = selected;
    const selectedRate = buildingYoyRate(selected);
    state.selectedTypeId = typeId(region, building);
    document.getElementById("selected-region").innerHTML = `
      <div class="region-title">
        <div>
          <h2>${escapeHtml(group.building_name)}</h2>
          <span>${escapeHtml(region.gu_name)} ${escapeHtml(region.dong_name)}</span>
        </div>
        <div class="selected-actions">
          <label class="type-picker">
            <span>평형</span>
            <select id="type-select">
              ${group.types
                .map((item) => {
                  const id = typeId(item.region, item.building);
                  const isSelected = id === state.selectedTypeId ? "selected" : "";
                  return `<option value="${escapeHtml(id)}" ${isSelected}>${escapeHtml(item.building.area_type)}</option>`;
                })
                .join("")}
            </select>
          </label>
          <div class="title-rate">
            <span>전년 대비 상승률</span>
            <strong>${formatHtml(selectedRate, "yoy_rate")}</strong>
          </div>
        </div>
      </div>
      ${askingPricePanel(selected)}
      <div class="metric-grid compact-detail">
        ${metricCard("해당 연도 평균 거래가", building.metrics.price_billion.avg, "price_billion")}
        ${metricCard("해당 연도 실거래 중앙가", totalPriceLabel(building.metrics.price_billion.median), "text")}
        ${metricCard("평단가_만원", building.metrics.price_per_pyeong.avg, "price_per_pyeong")}
        ${metricCard("거래횟수", building.count, "count")}
        ${metricCard("가격 비교점수", aiScoreForItem(selected), "ai_score")}
        ${metricCard("전용평수", building.metrics.area_pyeong.avg, "area_pyeong")}
        ${metricCard("연식", `${format(buildingAge(building), "age")} · ${ageFilterText(ageCategory(building))}`, "text")}
        ${metricCard(building.households_verified?"단지 전체 세대수 · 검증":"세대수 · 기존 자료", building.households, "households")}
        ${metricCard("거래된 층 (중앙 / 범위)", building.observed_floor?.median==null?'미확인':`${building.observed_floor.median}층 / ${building.observed_floor.min}~${building.observed_floor.max}층`, "text")}
        ${metricCard("해당 면적 세대수", "미확인", "text")}
        ${metricCard("초품아/역세권", `${elementaryLabel(building)} · ${subwayLabel(building)}`, "text")}
        <p class="score-note">${householdEvidenceNote(building)}</p>
        <p class="score-note">${escapeHtml(building.amenity_source ?? "시설 자료 미확인")} · 시설 필터는 확인된 자료가 있는 단지에만 적용됩니다.</p>
      </div>
    `;
    document.getElementById("type-select").addEventListener("change", (event) => {
      state.selectedTypeId = event.target.value;
      renderSelectedRegion();
      renderAssetLists();
    });
    const askingInput=document.getElementById("asking-price-input");
    if (askingInput) {
      const rec=aiRecommendationForItem(selected),key=state.year+'|'+typeId(region,building);
      const update=()=>{state.askingPrices.set(key,askingInput.value);document.getElementById("asking-price-result").innerHTML=askingPriceResult(rec,askingInput.value);};
      askingInput.addEventListener('input',update);
      document.getElementById("use-neutral-price").addEventListener('click',()=>{askingInput.value=neutralPrice(rec).toFixed(4);update();});
    }
    const transactionRec=aiRecommendationForItem(selected);
    const retry=document.getElementById('retry-transaction-valuation');
    if(retry)retry.addEventListener('click',()=>{state.transactionError=null;renderSelectedRegion();});
    if(transactionRec?.transaction_valuation?.status==='available' && !Array.isArray(transactionRec.transaction_valuation.recent_transactions) && !state.transactionLoading && !state.transactionError){
      state.transactionLoading=true;
      state.dataStore.ensureTransactionValuations().then(()=>settleTransactionValuations()).catch(settleTransactionValuations);
    }
    return;
  }

  const regions = filteredRegions();
  const title = state.selectedDong !== "all"
    ? `${state.regionByCode.get(state.selectedDong)?.gu_name} ${state.regionByCode.get(state.selectedDong)?.dong_name}`
    : state.selectedGu !== "all"
      ? guName(state.selectedGu)
      : selectedSidoLabel();

  document.getElementById("selected-region").innerHTML = `
    <div class="region-title">
      <div>
        <h2>${title}</h2>
        <span>지도에서 지역을 선택하거나 전체 매물에서 건물을 선택하세요.</span>
      </div>
    </div>
    <div class="metric-grid">
      ${metricCard("거래가_억원", averageMetric(regions, "price_billion"), "price_billion")}
      ${metricCard("평단가_만원", averageMetric(regions, "price_per_pyeong"), "price_per_pyeong")}
      ${metricCard("거래횟수", sumMetric(regions, "count"), "count")}
      ${metricCard("가격 비교점수", averageMetric(regions, "ai_score"), "ai_score")}
    </div>
  `;
}

function ageFilterText(category) {
  const labels = {
    new: "신축",
    semi_new: "준신축",
    middle: "중간연식",
    old: "구축",
    very_old: "노후 구축",
    unknown: "-",
  };
  return labels[category] ?? "-";
}
function averageMetric(regions, metric) {
  const values = regions.map((region) => metricValue(region, metric)).filter((value) => value !== null);
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function sumMetric(regions, metric) {
  const values = regions.map((region) => metricValue(region, metric)).filter((value) => value !== null);
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0);
}

function groupKeyForLevel(region, level) {
  if (level === "sido") return region.sido_name || "기타";
  if (level === "gu") return region.gu_code;
  return region.code;
}

function groupNameForLevel(region, level) {
  if (level === "sido") return region.sido_name || "기타";
  if (level === "gu") return `${region.sido_name} ${region.gu_name}`;
  return `${region.sido_name} ${region.gu_name} ${region.dong_name}`;
}

function aggregateRegionRates(level, valueGetter, scoped = false) {
  const groups = new Map();
  for (const region of state.summary.regions) {
    if (scoped && state.selectedSido !== "all" && region.sido_name !== state.selectedSido) continue;
    if (scoped && level !== "sido" && state.selectedGu !== "all" && region.gu_code !== state.selectedGu) continue;
    if (scoped && level === "dong" && state.selectedDong !== "all" && region.code !== state.selectedDong) continue;

    const value = valueGetter(region);
    if (value === null) continue;
    const key = groupKeyForLevel(region, level);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        level,
        name: groupNameForLevel(region, level),
        values: [],
        count: 0,
      });
    }
    const group = groups.get(key);
    group.values.push(value);
    group.count += 1;
  }

  return [...groups.values()]
    .map((group) => ({
      ...group,
      value: averageValues(group.values),
    }))
    .sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity) || String(a.id ?? a.key).localeCompare(String(b.id ?? b.key)));
}

function renderGrowthSummary() {
  const { start, end } = fullPeriodYears();
  const labels = {
    "서울특별시": "서울",
    "경기도": "경기",
    "인천광역시": "인천",
  };
  const sidos = ["서울특별시", "경기도", "인천광역시"];
  const rows = [
    {
      label: "전체기간",
      values: new Map(aggregateRegionRates("sido", regionPeriodRate, false).map((group) => [group.key, group.value])),
    },
    ...completedYears().map(String).reverse()
      .filter((year) => previousYearFor(year))
      .map((year) => ({
        label: `${year}`,
        values: new Map(aggregateRegionRates("sido", (region) => regionYearRate(region, year), false).map((group) => [group.key, group.value])),
      })),
  ];
  const rowHtml = rows
    .map((row) => `
      <div class="growth-summary-row">
        <b>${row.label}</b>
        ${sidos.map((sido) => `<span><em>${labels[sido]}</em>${formatHtml(row.values.get(sido), "yoy_rate")}</span>`).join("")}
      </div>
    `)
    .join("");
  document.getElementById("growth-summary").innerHTML = `
    <strong>${start ?? "-"}-${end ?? "-"} 상승률 요약</strong>
    ${rowHtml}
  `;
}

function renderGrowthRankings() {
  const query = state.growthSearch.trim().toLowerCase();
  const rows = aggregateRegionRates(state.growthLevel, regionGrowthRateForRanking, false)
    .filter((row) => !query || row.name.toLowerCase().includes(query))
    .filter((row) => state.growthMinRate === null || row.value >= state.growthMinRate)
    ;
  const page=ResultPages.view("growth-ranking-list",rows,30);

  document.getElementById("growth-ranking-list").innerHTML = rows.length
    ? page.rows
        .map((row, index) => `
          <li>
            <button type="button" class="growth-rank-row" data-growth-level="${row.level}" data-growth-key="${escapeHtml(row.key)}">
              <span>${page.offset + index + 1}. ${escapeHtml(row.name)}</span>
              <strong>${formatHtml(row.value, "yoy_rate")}</strong>
            </button>
          </li>
        `)
        .join("")
    : `<li class="growth-empty">조건에 맞는 지역이 없습니다.</li>`;
}

function officialRegionAiScore(region) {
  const values = regionTypeItems(region)
    .map(aiScoreForItem)
    .filter((value) => value !== null);
  return averageValues(values);
}

function renderAiScoreRankings() {
  const query = state.aiScoreSearch.trim().toLowerCase();
  const rows = aggregateRegionRates(state.aiScoreLevel, officialRegionAiScore, false)
    .filter((row) => !query || row.name.toLowerCase().includes(query))
    .filter((row) => state.aiScoreMin === null || row.value >= state.aiScoreMin)
    ;
  const page=ResultPages.view("ai-score-ranking-list",rows,30);

  document.getElementById("ai-score-ranking-list").innerHTML = rows.length
    ? page.rows
        .map((row, index) => `
          <li>
            <button type="button" class="growth-rank-row" data-growth-level="${row.level}" data-growth-key="${escapeHtml(row.key)}">
              <span>${page.offset + index + 1}. ${escapeHtml(row.name)}</span>
              <strong>${format(row.value, "ai_score")}</strong>
            </button>
          </li>
        `)
        .join("")
    : `<li class="growth-empty">조건에 맞는 가격 비교점수 지역이 없습니다.</li>`;
}

function metricCard(label, value, metric) {
  return `
    <div class="metric-card">
      <span>${label}</span>
      <strong>${formatHtml(value, metric)}</strong>
    </div>
  `;
}

function populateSidoSelect() {
  const order = ["서울특별시", "경기도", "인천광역시"];
  const sidos = [...new Set(state.summary.regions.map((region) => region.sido_name || "기타"))]
    .sort((a, b) => {
      const ai = order.indexOf(a);
      const bi = order.indexOf(b);
      if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      return a.localeCompare(b, "ko-KR");
    });
  document.getElementById("sido-select").innerHTML = `
    <option value="all">전체 시도</option>
    ${sidos.map((name) => `<option value="${name}">${name}</option>`).join("")}
  `;
  document.getElementById("sido-select").value = state.selectedSido;
}

function populateGuSelect() {
  const regions = state.summary.regions
    .filter((region) => state.selectedSido === "all" || region.sido_name === state.selectedSido);
  const guItems = [...new Map(regions.map((region) => [region.gu_code, region.gu_name])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1], "ko-KR"));
  document.getElementById("gu-select").innerHTML = `
    <option value="all">전체 시군구</option>
    ${guItems.map(([code, name]) => `<option value="${code}">${name}</option>`).join("")}
  `;
  document.getElementById("gu-select").value = state.selectedGu;
}

function populateDongSelect() {
  const dongs = state.summary.regions
    .filter((region) => state.selectedSido === "all" || region.sido_name === state.selectedSido)
    .filter((region) => state.selectedGu === "all" || region.gu_code === state.selectedGu)
    .sort((a, b) => a.dong_name.localeCompare(b.dong_name, "ko-KR"));
  document.getElementById("dong-select").innerHTML = `
    <option value="all">전체 읍면동</option>
    ${dongs.map((region) => `<option value="${region.code}">${escapeHtml(region.gu_name)} ${escapeHtml(region.dong_name)}</option>`).join("")}
  `;
  document.getElementById("dong-select").value = state.selectedDong;
}

function populateGrowthPeriodSelect() {document.getElementById("growth-period-select").innerHTML='<option value="all">전체 완결기간</option>'+completedYears().slice().reverse().map(y=>`<option value="${y}">${y}</option>`).join("");document.getElementById("growth-period-select").value=state.growthPeriod;}

function populateSubwayLineSelect() {
  const lines = new Set();
  for (const region of state.summary.regions) {
    for (const bucket of [bucketFor(region)]) {
      for (const building of bucket?.addresses ?? []) {
        for (const line of building.subway_lines ?? []) {
          lines.add(line);
        }
      }
    }
  }
  const sorted = [...lines].sort((a, b) => a.localeCompare(b, "ko-KR", { numeric: true }));
  document.getElementById("subway-line-select").innerHTML = `
    <option value="all">전체</option>
    ${sorted.map((line) => `<option value="${line}">${line}</option>`).join("")}
  `;
  document.getElementById("subway-line-select").value = state.subwayLine;
}

function refresh(resetPages=true) {
  if(state.loading)return;
  if(resetPages)ResultPages.reset();
  viewCache();
  state.mapDist = distribution();
  state.topologyLayer?.setStyle(styleFeature);
  updateLegend();
  if(document.getElementById('analysis-panel')?.open)renderAnalysis().catch(e=>{document.getElementById('growth-summary').textContent=e.message;});
  const groups = groupedBuildings();
  renderSummary(groups);
  renderSelectedRegion(groups);
  renderAiRecommendations();
  renderAssetLists(groups);
  renderDataStatus();
  renderReviewScoreFilter();
}

function renderReviewScoreFilter() {
  const target=state.recommendations?.target_year;
  const active=state.minReviewScore!==null;
  document.getElementById("review-score-filter-note").textContent = active
    ? (state.year===target ? `${state.minReviewScore}점 이상인 평형만 지도·목록·CSV에 표시합니다. 미산출 평형은 제외됩니다.` : `가격 비교점수는 ${target ?? '최신'}년 자료에만 있습니다. 해당 연도를 선택하거나 점수 조건을 해제하세요.`)
    : (state.recentEvidenceOnly&&state.year===target?'점수 조건 없음 · 최근 근거 조건을 충족한 평형입니다.':'점수 조건 없음 · 미산출 평형도 포함합니다.');
  const evidence=document.getElementById('recent-evidence-select');if(evidence){evidence.value=state.recentEvidenceOnly?'recent':'all';evidence.disabled=state.year!==target;}
  const evidenceNote=document.getElementById('recent-evidence-note');if(evidenceNote)evidenceNote.textContent=state.year!==target?'과거 연도는 해당 연도의 전체 실거래 통계입니다.':(state.recentEvidenceOnly?'기본 탐색: 기준가 입력 이력과 비교 90일 거래가 각각 3건 이상인 평형입니다. 3건은 정확도 보장 기준이 아닙니다.':'전체 평형 보기: 자료가 희소하거나 현재 점수가 없는 평형도 보존합니다. 가격 점수와 자료의 충분함을 따로 확인하세요.');
  document.querySelectorAll('[data-review-score]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.reviewScore===(active?String(state.minReviewScore):''))));
}

function setMinReviewScore(value) {
  const number=Number.parseFloat(value);
  state.minReviewScore=Number.isFinite(number)?Math.max(0,Math.min(100,number)):null;
  state.selectedGroupId=null;state.selectedTypeId=null;
  scheduleRefresh();
}

function scheduleRefresh(){clearTimeout(state.searchTimer);state.searchTimer=setTimeout(()=>refresh(),180);}
async function ensureHistory(){if(state.historyReady)return;await state.dataStore.ensureHistory();state.periodAddressMapCache.clear();state.historyReady=true;}
async function renderAnalysis(){
 const panel=document.getElementById('analysis-panel');if(!panel?.open)return;
 await ensureHistory();
 if(!panel.open)return;
 const key=viewKey()+state.growthPeriod+state.growthLevel+state.growthSearch+state.growthMinRate+state.aiScoreLevel+state.aiScoreSearch+state.aiScoreMin;
 if(state.analysisKey===key)return;
 renderGrowthSummary();renderGrowthRankings();renderAiScoreRankings();state.analysisKey=key;
}

function selectSido(sidoName) {
  state.selectedSido = sidoName;
  state.selectedGu = "all";
  state.selectedDong = "all";
  state.selectedGroupId = null;
  state.selectedTypeId = null;
  populateSidoSelect();
  populateGuSelect();
  populateDongSelect();
  refresh();
  focusSelectedMap();
}

function selectGu(guCode) {
  const region = state.summary.regions.find((item) => item.gu_code === guCode);
  if (region) state.selectedSido = region.sido_name || "all";
  state.selectedGu = guCode;
  state.selectedDong = "all";
  state.selectedGroupId = null;
  state.selectedTypeId = null;
  document.getElementById("sido-select").value = state.selectedSido;
  populateGuSelect();
  document.getElementById("gu-select").value = guCode;
  populateDongSelect();
  refresh();
  focusSelectedMap();
}

function selectDong(dongCode) {
  const region = state.regionByCode.get(dongCode);
  if (!region) return;
  state.selectedSido = region.sido_name || "all";
  state.selectedGu = region.gu_code;
  state.selectedDong = dongCode;
  state.selectedGroupId = null;
  state.selectedTypeId = null;
  document.getElementById("sido-select").value = state.selectedSido;
  populateGuSelect();
  document.getElementById("gu-select").value = region.gu_code;
  populateDongSelect();
  document.getElementById("dong-select").value = dongCode;
  refresh();
  focusSelectedMap();
}

function setMapMode(mode) {
  if (mode === "sido" && state.selectedSido === "all") {
    state.mapMode = "global";
  } else if (mode === "gu" && state.selectedGu === "all") {
    state.mapMode = "global";
  } else if (mode === "dong" && state.selectedDong === "all") {
    state.mapMode = "global";
  } else {
    state.mapMode = mode;
  }
  refresh();
  if(state.mapMode==="global")fitDefaultMapView();else focusSelectedMap();
}

async function selectGroup(id, preferredTypeId = null) {
  const group = groupedBuildings().find((item) => item.id === id);
  if (!group) return;

  state.selectedGroupId = id;
  const preferred = group.types.find((item) => typeId(item.region, item.building) === preferredTypeId);
  const bestAiType = state.metric === "ai_score"
    ? [...group.types].sort((a, b) => (aiScoreForItem(b) ?? -1) - (aiScoreForItem(a) ?? -1))[0]
    : null;
  const selected = preferred ?? bestAiType ?? group.types[0];
  state.selectedTypeId = typeId(selected.region, selected.building);
  renderSelectedRegion();
  document.getElementById('selected-region').scrollIntoView({block:'start',behavior:'smooth'});
  try{await ensureHistory();renderSelectedRegion();}
  catch(e){document.getElementById('data-status').textContent='과거 비교 자료를 불러오지 못했습니다. '+e.message;}
}

function wireEvents() {
  document.getElementById("dark-mode-toggle").addEventListener("click", () => {
    const enabled = !document.body.classList.contains("dark-mode");
    try { localStorage.setItem("realEstateDashboardDarkMode", enabled ? "1" : "0"); } catch (_) {}
    applyDarkMode(enabled);
  });

  document.getElementById("year-select").addEventListener("change", (event) => {
    changeYear(event.target.value);
  });

  document.getElementById("metric-select").addEventListener("change", async (event) => {
    const previous=state.metric;
    state.metric = event.target.value;
    try{if(state.metric==='yoy_rate')await ensureHistory();}
    catch(e){state.metric=previous;event.target.value=previous;document.getElementById('data-status').textContent=e.message;return;}
    refresh();
  });

  document.getElementById("area-range-select").addEventListener("change", (event) => {
    state.areaRange = event.target.value;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.getElementById("price-input").addEventListener("input", (event) => {
    const value = parseFloat(event.target.value);
    state.maxPriceBillion = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    scheduleRefresh();
  });

  document.getElementById("min-price-input").addEventListener("input", (event) => {
    const value = parseFloat(event.target.value);
    state.minPriceBillion = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    scheduleRefresh();
  });

  document.getElementById("households-input").addEventListener("input", (event) => {
    const value = parseInt(event.target.value, 10);
    state.minHouseholds = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    scheduleRefresh();
  });

  document.getElementById("recent-evidence-select").addEventListener("change",event=>{state.recentEvidenceOnly=event.target.value==='recent';state.selectedGroupId=null;state.selectedTypeId=null;refresh();});
  document.getElementById("review-score-min-input").addEventListener("input",event=>setMinReviewScore(event.target.value));
  document.getElementById("review-score-min-input").addEventListener("change",event=>{event.target.value=state.minReviewScore??'';});
  document.querySelectorAll('[data-review-score]').forEach(button=>button.addEventListener('click',()=>{
    document.getElementById("review-score-min-input").value=button.dataset.reviewScore;
    setMinReviewScore(button.dataset.reviewScore);
  }));

  document.getElementById("trade-count-input").addEventListener("input", (event) => {
    const value = parseInt(event.target.value, 10);
    state.minTradeCount = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    scheduleRefresh();
  });

  document.getElementById("age-range-select").addEventListener("change", (event) => {
    state.ageRange = event.target.value;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.querySelectorAll("#elementary-filter, #elementary-filter-mobile").forEach((input) => {
    input.addEventListener("change", (event) => {
      state.elementary500mOnly = event.target.checked;
      setElementaryFilterChecked(state.elementary500mOnly);
      state.selectedGroupId = null;
      state.selectedTypeId = null;
      refresh();
    });
  });

  document.getElementById("subway-line-select").addEventListener("change", (event) => {
    state.subwayLine = event.target.value;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.getElementById("subway-walk-select").addEventListener("change", (event) => {
    state.subwayWalkMinutes = event.target.value;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.getElementById("sido-select").addEventListener("change", (event) => {
    selectSido(event.target.value);
  });

  document.getElementById("gu-select").addEventListener("change", (event) => {
    selectGu(event.target.value);
  });

  document.getElementById("dong-select").addEventListener("change", (event) => {
    if (event.target.value === "all") {
      state.selectedDong = "all";
      state.selectedGroupId = null;
      state.selectedTypeId = null;
      refresh();
    } else {
      selectDong(event.target.value);
    }
  });

  document.getElementById("search-input").addEventListener("input", (event) => {
    state.search = event.target.value;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    scheduleRefresh();
  });

  document.getElementById("growth-level-select").addEventListener("change", (event) => {
    state.growthLevel = event.target.value;
    renderGrowthRankings();
  });

  document.getElementById("growth-period-select").addEventListener("change", (event) => {
    state.growthPeriod = event.target.value;
    renderGrowthRankings();
  });

  document.getElementById("growth-search-input").addEventListener("input", (event) => {
    state.growthSearch = event.target.value;
    renderGrowthRankings();
  });

  document.getElementById("growth-min-input").addEventListener("input", (event) => {
    const value = parseFloat(event.target.value);
    state.growthMinRate = Number.isFinite(value) ? value : null;
    renderGrowthRankings();
  });

  document.getElementById("ai-score-level-select").addEventListener("change", (event) => {
    state.aiScoreLevel = event.target.value;
    renderAiScoreRankings();
  });

  document.getElementById("ai-score-search-input").addEventListener("input", (event) => {
    state.aiScoreSearch = event.target.value;
    renderAiScoreRankings();
  });

  document.getElementById("ai-score-min-input").addEventListener("input", (event) => {
    const value = parseFloat(event.target.value);
    state.aiScoreMin = Number.isFinite(value) ? value : null;
    renderAiScoreRankings();
  });

  document.getElementById("clear-filter").addEventListener("click", () => {
    state.selectedSido = "all";
    state.selectedGu = "all";
    state.selectedDong = "all";
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    state.search = "";
    state.areaRange = "all";
    state.minPriceBillion = null;
    state.maxPriceBillion = null;
    state.minHouseholds = null;
    state.minTradeCount = null;
    state.minReviewScore = null;
    state.recentEvidenceOnly = true;
    state.ageRange = "all";
    state.elementary500mOnly = false;
    state.subwayLine = "all";
    state.subwayWalkMinutes = "all";
    state.mapMode = "global";
    state.growthSearch = "";
    state.growthMinRate = null;
    state.aiScoreSearch = "";
    state.aiScoreMin = null;
    document.getElementById("sido-select").value = "all";
    populateGuSelect();
    document.getElementById("gu-select").value = "all";
    document.getElementById("area-range-select").value = "all";
    document.getElementById("min-price-input").value = "";
    document.getElementById("price-input").value = "";
    document.getElementById("households-input").value = "";
    document.getElementById("trade-count-input").value = "";
    document.getElementById("review-score-min-input").value = "";
    document.getElementById("age-range-select").value = "all";
    setElementaryFilterChecked(false);
    document.getElementById("subway-line-select").value = "all";
    document.getElementById("subway-walk-select").value = "all";
    document.getElementById("search-input").value = "";
    document.getElementById("growth-search-input").value = "";
    document.getElementById("growth-min-input").value = "";
    document.getElementById("ai-score-search-input").value = "";
    document.getElementById("ai-score-min-input").value = "";
    populateDongSelect();
    refresh();
  });

  document.getElementById("map-reset").addEventListener("click", () => {
    state.mapMode = "global";
    refresh();
    fitDefaultMapView();
  });

  document.getElementById("map-full-extent").addEventListener("click",()=>fitDefaultMapView(true));

  document.querySelectorAll("[data-map-mode]").forEach((button) => {
    button.addEventListener("click", () => setMapMode(button.dataset.mapMode));
  });

  document.querySelectorAll("[data-market-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.marketTab;
      state.activeTab=tab;
      document.querySelectorAll("[data-market-tab]").forEach((item) => {
        item.classList.toggle("active", item.dataset.marketTab === tab);
      });
      document.querySelectorAll("[data-market-panel]").forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.marketPanel === tab);
      });
      renderAiRecommendations();renderAssetLists();
    });
  });

  document.body.addEventListener("click", (event) => {
    const dongButton = event.target.closest("[data-dong-code]");
    if (dongButton) {
      selectDong(dongButton.dataset.dongCode);
      return;
    }

    const groupButton = event.target.closest("[data-group-id]");
    if (groupButton) {
      selectGroup(groupButton.dataset.groupId, groupButton.dataset.typeId ?? null);
      return;
    }

    const growthButton = event.target.closest("[data-growth-key]");
    if (growthButton) {
      if (growthButton.dataset.growthLevel === "sido") {
        selectSido(growthButton.dataset.growthKey);
      } else if (growthButton.dataset.growthLevel === "gu") {
        selectGu(growthButton.dataset.growthKey);
      } else {
        selectDong(growthButton.dataset.growthKey);
      }
    }
  });
}

async function init() {
 const compact=window.matchMedia('(max-width:600px)');
 const syncFilters=()=>{document.getElementById('location-filters').open=!compact.matches;};
 syncFilters();compact.addEventListener('change',syncFilters);
 try { applyDarkMode(localStorage.getItem("realEstateDashboardDarkMode")==="1"); } catch (_) { applyDarkMode(false); }
 const store=await DashboardData.open();state.dataStore=store;state.summary=store.summary;state.year=store.manifest.default_year;state.recommendations=store.recommendations;
 state.recommendationByType=new Map(store.recommendations.recommendations.map(r=>[recommendationKey(r.region_code,r.building_key),r]));
 await store.loadPeriod(state.year);const deepSearch=new URLSearchParams(window.location.search).get('search');if(deepSearch){state.recentEvidenceOnly=false;state.search=deepSearch;document.getElementById('search-input').value=deepSearch;}const summary=state.summary;state.regionByCode=new Map(summary.regions.map(r=>[r.code,r]));
 for(const r of summary.regions)for(const code of new Set((r.map_codes??[r.code]).flatMap(c=>[c,c.slice(0,5)]))){if(!state.regionByMapCode.has(code))state.regionByMapCode.set(code,[]);state.regionByMapCode.get(code).push(r);}
 document.getElementById("year-select").innerHTML='<option value="all">전체연도</option>'+summary.years.map(y=>`<option value="${y}">${y}</option>`).join("");document.getElementById("year-select").value=state.year;
 document.getElementById('metric-select').value=state.metric;
 populateSidoSelect();populateGuSelect();populateDongSelect();populateGrowthPeriodSelect();populateSubwayLineSelect();wireEvents();wireResultEvents();refresh();
 document.getElementById('analysis-panel').addEventListener('toggle',()=>renderAnalysis().catch(e=>{document.getElementById('growth-summary').textContent=e.message;}));
 try {
  if(!map)throw Error("지도 로드 실패");const data=await DashboardData.compressed(store.manifest.map);
  const geojson=data.type==="Topology"?topojson.feature(data,data.objects[Object.keys(data.objects)[0]]):data;
  state.topologyLayer=L.geoJSON(geojson,{style:styleFeature,onEachFeature(feature,layer){const regions=regionsForFeature(feature),r=regions[0];if(!r)return;layer.bindTooltip(escapeHtml(`${r.sido_name} ${r.gu_name} · 시군구 합산`),{sticky:true});layer.on("click",()=>selectGu(r.gu_code));}}).addTo(map);
  state.mapGeojson=geojson;state.mainlandBounds=geojson.mainland_bounds;
  map.createPane('provinceBorders');map.getPane('provinceBorders').style.zIndex=410;map.getPane('provinceBorders').style.pointerEvents='none';
  state.provinceLayer=L.geoJSON(geojson.province_boundaries??{type:'FeatureCollection',features:[]},{pane:'provinceBorders',interactive:false,style:{color:'#263b53',weight:2.6,fill:false,opacity:.95}}).addTo(map);
  state.mapLabels=L.layerGroup().addTo(map);map.on('zoomend',renderMapLabels);
  map.invalidateSize();fitDefaultMapView();renderMapLabels();
 }catch(e){document.getElementById("map-status").textContent="지도를 불러오지 못했습니다. 전체 목록·검색·다운로드는 이용할 수 있습니다.";}
}

function fitDefaultMapView(full=false) {
 if(!map)return;
 const bounds=!full&&state.mainlandBounds ? L.latLngBounds(state.mainlandBounds) : state.topologyLayer?.getBounds();
 if(bounds?.isValid())map.fitBounds(bounds,{padding:[22,22],maxZoom:10.6,animate:false});
}

function focusSelectedMap() {
 if(!map||!state.topologyLayer)return;
 if(state.selectedSido==='all'){fitDefaultMapView();return;}
 const bounds=L.latLngBounds([]);
 state.topologyLayer.eachLayer(layer=>{
  const matches=regionsForFeature(layer.feature).some(r=>state.selectedGu!=='all'?r.gu_code===state.selectedGu:r.sido_name===state.selectedSido);
  if(matches)bounds.extend(layer.getBounds());
 });
 if(bounds.isValid())map.fitBounds(bounds,{padding:[28,28],maxZoom:11.5,animate:false});
}

function renderMapLabels() {
 if(!map||!state.mapLabels)return;
 state.mapLabels.clearLayers();const zoom=map.getZoom();
 const province=zoom<9.2;
 const features=province ? state.mapGeojson?.province_boundaries?.features : state.mapGeojson?.features;
 for(const f of features??[]){
  const point=f.properties.label_point;if(!point)continue;
  const name=province?f.properties.name:regionsForFeature(f)[0]?.gu_name;
  if(!name)continue;
  L.marker(point,{interactive:false,icon:L.divIcon({className:province?'province-map-label':'district-map-label',html:escapeHtml(name.replace('특별시','').replace('광역시','')),iconSize:province?[84,26]:[82,20],iconAnchor:province?[42,13]:[41,10]})}).addTo(state.mapLabels);
 }
}


function renderDataStatus(){const m=state.dataStore.manifest,c=m.coverage[state.year];document.getElementById("data-status").textContent=`자료 ${m.generated_at} · 모델 ${m.model.model_month} · ${m.model.nowcast?.release_note??'월별 가격 비교'} · ${c.available_types.toLocaleString("ko-KR")}개 평형 전체 조회`;document.getElementById("coverage-note").textContent=c.complete?"모든 집계 거래가 단지·평형 목록에 보존돼 있습니다. 페이지 수와 관계없이 전체를 검색·다운로드합니다.":`기존 저장본에서 개별 목록 ${c.unrepresented_trades.toLocaleString("ko-KR")}건이 누락돼 있습니다. 전체 복구와 구분해 표시합니다.`;}
async function changeYear(year){const id=(state.yearRequest??0)+1;state.yearRequest=id;state.loading=true;document.querySelector(".detail-pane").setAttribute("aria-busy","true");try{if(!await state.dataStore.loadPeriod(year))return;state.year=year;state.selectedGroupId=null;state.selectedTypeId=null;state.loading=false;populateSubwayLineSelect();refresh();}catch(e){if(id===state.yearRequest){document.getElementById("year-select").value=state.year;document.getElementById("data-status").textContent=`불러오기 실패: ${e.message}. 이전 결과를 유지합니다.`;}}finally{if(id===state.yearRequest){state.loading=false;document.querySelector(".detail-pane").setAttribute("aria-busy","false");}}}
function exportResults(){const rows=typeItems().map(({region:r,building:b})=>{const m=aiRecommendationForItem({region:r,building:b}),c=valuationComparison(m),v=currentValuation(m),recent=m?.recent_price_comparison;return [state.year,r.sido_name,r.gu_name,r.dong_name,b.building_name,b.area_type,b.key,b.count,b.metrics.price_billion.avg,b.metrics.price_billion.median,b.metrics.price_per_pyeong.median,b.households,b.built_year,c?.score,v?.price_billion,c?.comparison_price_billion,c?.discount_pct,c?.valuation_month,c?.comparison_period,v?.recent_trade_count,c?.score_version,recent?.window_start,recent?.window_end,recent?.data_through,recent?.trade_count,v?.recent_history_start,v?.feature_cutoff,v?.last_trade_age_days,c?.evidence_level,v?.floor,v?.floor_basis,b.households_verified?'공식 주소 대조 완료':'기존 자료·범위 미검증',b.official_complex_id,b.household_observed_at,v?.model_version,v?.release_version,v?.confidence?.grade,v?.confidence?.lower_price_billion,v?.confidence?.upper_price_billion,v?.confidence?.historical_grade_coverage_pct,v?.confidence?.version];});ResultPages.downloadCsv(`apartment-valuation-${state.year}.csv`,["실거래 집계 연도","시도","시군구","읍면동","단지","평형","식별키","연간 거래수","연간 평균 거래가(억)","연간 중앙 거래가(억)","중앙 평단가(만원)","표시 세대수","준공연도","가격 비교점수","현재 50점 기준가(억)","점수에 사용한 최근90일 중앙가(억)","기준가보다 낮은 비율(%)","기준가 산출 월","비교 실거래 기간","기준가 입력 최근 이력 거래수","점수 버전","비교90일 시작일(포함)","비교90일 종료일(포함)","비교 자료 마감일","비교90일 거래수","기준가 입력 최근 이력 시작일","기준가 입력 마감일","마지막 입력거래 경과일(기준월초)","최근 근거 구분","기준가 대표층","대표층 기준","세대수 출처 상태","공식 단지 ID","세대수 관측일","적용 가격 모델","가격 모델 배포 버전","가격 추정 신뢰도","목표80% 참고가격 하단(억)","목표80% 참고가격 상단(억)","2026년 해당등급 실제 포함률(%)","신뢰도 버전"],rows);}
function renderPage(id){if(id==="ai-list")renderAiRecommendations();else if(id==="growth-ranking-list")renderGrowthRankings();else if(id==="ai-score-ranking-list")renderAiScoreRankings();else renderAssetLists();}
function wireResultEvents(){document.getElementById("export-results").addEventListener("click",exportResults);document.body.addEventListener("click",e=>{const b=e.target.closest("[data-page-list]");if(b){ResultPages.set(b.dataset.pageList,b.dataset.page);renderPage(b.dataset.pageList);}});document.body.addEventListener("change",e=>{if(e.target.dataset.pageInput){ResultPages.set(e.target.dataset.pageInput,e.target.value);renderPage(e.target.dataset.pageInput);}});}

window.addEventListener("resize", () => {
  window.clearTimeout(state.resizeTimer);
  state.resizeTimer = window.setTimeout(() => {
    map?.invalidateSize();
  }, 120);
});

init().catch((error) => {
  document.getElementById("selected-region").innerHTML =
    `<p class="empty-state">데이터를 불러오지 못했습니다. ${escapeHtml(error.message)}<br>잠시 후 새로고침해 주세요.</p>`;
});
