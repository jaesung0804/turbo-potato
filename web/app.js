// Web Run:
//   cd C:\code
//   python run_real_estate_dashboard.py
// This starts the web server and opens http://127.0.0.1:8000
// Fast web-only run:
//   python run_real_estate_dashboard.py --skip-build

const SUMMARY_URL = "data/seoul_real_estate_summary.json";
const MAP_URL = "data/capital_area_adm_dong_light.geojson";
const RECOMMENDATIONS_URL = "data/house_match_recommendations.json";
const LIST_LIMITS = {
  all: 350,
  edge: 120,
};

// 이 파일의 의도:
// - 정적 JSON 데이터만으로 지도, 필터, 매물 목록, AI 추천 후보를 렌더링합니다.
// - GitHub Pages처럼 서버 코드가 없는 환경에서도 동작하도록 모든 계산을 브라우저에서 수행합니다.
// - 대표지표 변경 시 지도 색상, 전체 매물, Hot/Cold 목록이 같은 기준으로 다시 계산됩니다.
// - 정식 AI SCORE가 없는 매물은 화면 비교가 끊기지 않도록 보조 SCORE를 계산합니다.
const metricLabels = {
  price_billion: { title: "거래가_억원", unit: "억", digits: 1 },
  price_per_pyeong: { title: "평단가_만원", unit: "만원/평", digits: 0 },
  count: { title: "거래횟수", unit: "건", digits: 0 },
  yoy_rate: { title: "전년 대비 상승률", unit: "%", digits: 1 },
  ai_score: { title: "AI SCORE", unit: "", digits: 1 },
  area_pyeong: { title: "전용평수", unit: "평", digits: 1 },
  age: { title: "연식", unit: "년차", digits: 0 },
  households: { title: "세대수", unit: "세대", digits: 0 },
};
const state = {
  summary: null,
  recommendations: null,
  recommendationByType: new Map(),
  topologyLayer: null,
  regionByCode: new Map(),
  regionByMapCode: new Map(),
  year: "all",
  metric: "price_billion",
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
};

const map = L.map("map", {
  attributionControl: false,
  dragging: true,
  doubleClickZoom: false,
  scrollWheelZoom: true,
  wheelPxPerZoomLevel: 90,
  wheelDebounceTime: 30,
  boxZoom: false,
  keyboard: true,
  zoomControl: true,
  tap: true,
  touchZoom: true,
  zoomSnap: 0.1,
  zoomDelta: 0.25,
});

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

function bucketFor(region) {
  if (!region) return null;
  return state.year === "all" ? region.all : region.years[state.year] || null;
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

function fullPeriodYears() {
  const years = state.summary.years.map(Number).sort((a, b) => a - b);
  return {
    start: years[0] ? String(years[0]) : null,
    end: years[years.length - 1] ? String(years[years.length - 1]) : null,
  };
}

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
  const currentBucket = region.years?.[current];
  const previousBucket = region.years?.[previous];
  if (!currentBucket || !previousBucket) return null;

  const rates = currentBucket.addresses
    .filter(areaMatches)
    .map((building) => typeYoyRate(region, building))
    .filter((value) => value !== null);

  return averageValues(rates);
}

function generatedYear() {
  return Number(state.summary?.generated_at?.slice(0, 4)) || new Date().getFullYear();
}

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
  if (metric === "text") return String(value);
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
    return cachedRegionMetricValue(region) !== null;
  });
}

function regionMatchesSearch(region) {
  const query = state.search.trim().toLowerCase();
  if (!query) return true;
  const bucket = bucketFor(region);
  const regionText = `${region.gu_name} ${region.dong_name}`.toLowerCase();
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
    color: isSelected ? "#17202a" : "#ffffff",
    weight: isSelectedDong ? 1.8 : isSelectedGu ? 1.15 : isSelectedSido ? 0.9 : 0.5,
    opacity: 1,
    fillColor: inMapScope ? colorFor(value, dist) : "#ffffff",
    fillOpacity: !inMapScope ? 0.72 : value === null ? 0.24 : isSelected ? 0.98 : 0.86,
    interactive: true,
  };
}

function featureCode(feature) {
  return feature.properties.ADM_CD || feature.properties.ADM_SGG_CD || feature.properties.EMD_CD;
}

function regionsForFeature(feature) {
  const code = featureCode(feature);
  if (state.regionByMapCode.has(code)) return state.regionByMapCode.get(code);
  const region = state.regionByCode.get(code);
  return region ? [region] : [];
}

function featureMetricValue(regions) {
  const values = regions.map((region) => cachedRegionMetricValue(region)).filter((value) => value !== null);
  if (!values.length) return null;
  return averageValues(values);
}

function updateLegend() {
  const dist = state.mapDist;
  const year = state.year === "all" ? "전체연도" : state.year;
  document.getElementById("legend-title").textContent =
    `${year} · ${mapScopeLabel()} · ${metricLabels[state.metric].title}`;
  document.getElementById("legend-min").textContent = format(dist.min);
  document.getElementById("legend-mid").textContent = format(dist.mid);
  document.getElementById("legend-max").textContent = format(dist.max);
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
  return state.minHouseholds === null ? "전체 세대수" : `${state.minHouseholds.toLocaleString("ko-KR")}세대 이상`;
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
    return region ? `${region.gu_name} ${region.dong_name} 기준` : "선택 동 없음";
  }
  return "수도권 전체";
}

function typeId(region, building) {
  return `${region.code}|${encodeURIComponent(building.key ?? building.address)}`;
}

function groupId(region, building) {
  return `${region.code}|${encodeURIComponent(building.building_name)}`;
}

function areaPyeong(building) {
  return building.metrics?.area_pyeong?.avg ?? null;
}

function priceBillion(building) {
  return building.metrics?.price_billion?.avg ?? null;
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

function priceMatches(building) {
  const price = priceBillion(building);
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

function buildingMatchesFilters(building) {
  return areaMatches(building) && priceMatches(building) && householdsMatch(building) && tradeCountMatches(building) && ageMatches(building) && elementaryMatches(building) && subwayMatches(building);
}

function typeItems() {
  return filteredRegions().flatMap((region) => {
    const bucket = bucketFor(region);
    return (bucket?.addresses ?? [])
      .filter(buildingMatchesFilters)
      .map((building) => ({ region, building }));
  });
}

function regionTypeItems(region) {
  const bucket = bucketFor(region);
  return (bucket?.addresses ?? [])
    .filter(buildingMatchesFilters)
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

function aiRecommendationForItem(item) {
  const key = item.building.key ?? item.building.address;
  return state.recommendationByType.get(recommendationKey(item.region.code, key)) ?? null;
}

function aiScoreForItem(item) {
  const recommendation = aiRecommendationForItem(item);
  return recommendation?.house_match_score ?? fallbackAiScoreForItem(item);
}

function groupAiScore(group) {
  const scores = group.types.map((item) => aiScoreForItem(item)).filter((value) => value !== null);
  return scores.length ? Math.max(...scores) : null;
}

function clampScore(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function fallbackAiScoreForItem(item) {
  const yoy = buildingYoyRate(item);
  const period = typePeriodRate(item.region, item.building);
  const count = item.building.count || 0;
  const households = item.building.households || 0;
  const subwayDistance = item.building.subway_distance_m;
  const liquidityScore = clampScore((count / 20) * 100);
  const scaleScore = clampScore((households / 2000) * 100);
  const yoyScore = clampScore(((yoy ?? 0) + 10) * 2.5);
  const periodScore = clampScore(((period ?? 0) + 20) * 1.2);
  const schoolScore = item.building.elementary_500m ? 100 : 0;
  const subwayScore = subwayDistance === null || subwayDistance === undefined
    ? 0
    : clampScore(100 - (Math.min(subwayDistance, 1600) / 1600) * 100);
  return (
    liquidityScore * 0.24
    + scaleScore * 0.2
    + yoyScore * 0.2
    + periodScore * 0.18
    + schoolScore * 0.08
    + subwayScore * 0.1
  );
}

function buildingYoyRate(item) {
  return typeYoyRate(item.region, item.building);
}

function typeYoyRate(region, building) {
  const { current, previous } = comparisonYears();
  return typeYoyRateForYears(region, building, current, previous);
}

function typeYoyRateForYears(region, building, current, previous) {
  if (!current || !previous) return null;
  const key = building.key ?? building.address;
  const currentBuilding = periodAddressMap(region, current).get(key);
  const previousBuilding = periodAddressMap(region, previous).get(key);
  const currentValue = currentBuilding?.metrics?.price_billion?.avg ?? null;
  const previousValue = previousBuilding?.metrics?.price_billion?.avg ?? null;
  return changeRate(currentValue, previousValue);
}

function periodAddressMap(region, year) {
  const key = `${region.code}|${year}`;
  if (state.periodAddressMapCache.has(key)) return state.periodAddressMapCache.get(key);
  const mapForYear = new Map((region.years?.[year]?.addresses ?? []).map((item) => [item.key ?? item.address, item]));
  state.periodAddressMapCache.set(key, mapForYear);
  return mapForYear;
}

function typePeriodRate(region, building) {
  const { start, end } = fullPeriodYears();
  if (!start || !end || start === end) return null;
  const key = building.key ?? building.address;
  const endBuilding = periodAddressMap(region, end).get(key);
  const startBuilding = periodAddressMap(region, start).get(key);
  const endValue = endBuilding?.metrics?.price_billion?.avg ?? null;
  const startValue = startBuilding?.metrics?.price_billion?.avg ?? null;
  return changeRate(endValue, startValue);
}

function regionPeriodRate(region) {
  const { end } = fullPeriodYears();
  const endBucket = end ? region.years?.[end] : null;
  if (!endBucket) return null;
  const rates = endBucket.addresses
    .filter(buildingMatchesFilters)
    .map((building) => typePeriodRate(region, building))
    .filter((value) => value !== null);
  return averageValues(rates);
}

function regionYearRate(region, year) {
  const previous = previousYearFor(year);
  const currentBucket = region.years?.[year];
  if (!previous || !currentBucket) return null;
  const rates = currentBucket.addresses
    .filter(buildingMatchesFilters)
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

  return [...groups.values()]
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
    .filter((group) => group.value !== null)
    .sort((a, b) => b.value - a.value);
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

function renderAssetLists(groups = groupedBuildings()) {
  const edgeCount = Math.max(1, Math.ceil(groups.length * 0.3));
  const hot = groups.slice(0, edgeCount);
  const cold = [...groups].reverse().slice(0, edgeCount);

  renderGroupList("all-list", "all-count", groups, LIST_LIMITS.all);
  renderGroupList("hot-list", "hot-count", hot, LIST_LIMITS.edge);
  renderGroupList("cold-list", "cold-count", cold, LIST_LIMITS.edge);
}

function recommendationMatchesFilters(item) {
  if (!item) return false;
  if (state.year !== "all" && item.year !== state.year) return false;
  if (state.selectedSido !== "all" && item.sido_name !== state.selectedSido) return false;
  if (state.selectedGu !== "all" && item.gu_code !== state.selectedGu) return false;
  if (state.selectedDong !== "all" && item.region_code !== state.selectedDong) return false;

  const pseudoBuilding = {
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
  return buildingMatchesFilters(pseudoBuilding);
}

function renderAiRecommendations() {
  const list = document.getElementById("ai-list");
  const count = document.getElementById("ai-count");
  if (!list || !count) return;

  const rows = groupedBuildings()
    .map((group) => {
      const scoredTypes = group.types
        .map((type) => ({ type, recommendation: aiRecommendationForItem(type) }))
        .filter((row) => row.recommendation);
      const best = scoredTypes.sort((a, b) => b.recommendation.house_match_score - a.recommendation.house_match_score)[0];
      const bestType = best?.type ?? null;
      const recommendation = best?.recommendation ?? null;
      const score = recommendation?.house_match_score ?? null;
      return { group, bestType, recommendation, score };
    })
    .filter((row) => row.bestType && row.recommendation && row.score !== null)
    .sort((a, b) => b.score - a.score)
    .slice(0, 40);

  count.textContent = rows.length.toLocaleString("ko-KR");
  list.innerHTML = rows.length
    ? rows
        .map(({ group, bestType, recommendation, score }) => {
          const building = bestType.building;
          const region = bestType.region;
          const type = typeId(region, building);
          const targetYear = recommendation?.forecast_target_year ?? state.recommendations?.forecast_target_year ?? "-";
          const fairPrice = recommendation.fair_price_per_pyeong ?? recommendation.predicted_price_per_pyeong;
          const forecastPrice = recommendation.forecast_price_per_pyeong;
          const currentPrice = Number(building.metrics.price_per_pyeong.avg).toLocaleString("ko-KR", { maximumFractionDigits: 0 });
          const fairText = Number.isFinite(Number(fairPrice))
            ? Number(fairPrice).toLocaleString("ko-KR", { maximumFractionDigits: 0 })
            : "-";
          const forecastText = Number.isFinite(Number(forecastPrice)) && Number(forecastPrice) > 0
            ? Number(forecastPrice).toLocaleString("ko-KR", { maximumFractionDigits: 0 })
            : "-";
          const expectedGrowth = recommendation.expected_growth_pct;
          return `
            <li>
              <button type="button" class="ai-row" data-group-id="${group.id}" data-type-id="${type}">
                <span class="ai-main">
                  <strong>${group.building_name}</strong>
                  <small>${region.gu_name ?? ""} ${region.dong_name ?? ""} · ${building.area_type}</small>
                  <small>현재 ${currentPrice}만원/평 · 모델 적정 ${fairText}만원/평 · ${targetYear}년 예측 ${forecastText}만원/평 (${formatHtml(expectedGrowth, "yoy_rate")})</small>
                </span>
                <span class="ai-score">
                  <small>Score</small>
                  <strong>${Number(score).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</strong>
                </span>
              </button>
            </li>
          `;
        })
        .join("")
    : `<li class="growth-empty">조건에 맞는 AI 추천 후보가 없습니다.</li>`;
}
function renderGroupList(listId, countId, groups, limit) {
  document.getElementById(countId).textContent = groups.length.toLocaleString("ko-KR");
  const visibleGroups = groups.slice(0, limit);
  document.getElementById(listId).innerHTML = visibleGroups
    .map((group) => {
      const selected = state.selectedGroupId === group.id ? " selected" : "";
      return `
        <li>
          <button type="button" class="asset-row${selected}" data-group-id="${group.id}">
            <span>${group.building_name}</span>
            <small>${group.region.gu_name} ${group.region.dong_name} · ${group.typeCount}개 평형</small>
            <strong>${formatHtml(group.value)} · ${group.count.toLocaleString("ko-KR")}건</strong>
          </button>
        </li>
      `;
    })
    .join("");
}

function renderSummary(groups = groupedBuildings()) {
  const fullCount = filteredRegions().reduce((sum, region) => {
    const bucket = bucketFor(region);
    return sum + (bucket?.count || 0);
  }, 0);
  document.getElementById("total-used").textContent = fullCount.toLocaleString("ko-KR");
  document.getElementById("total-buildings").textContent = groups.length.toLocaleString("ko-KR");
}
function renderSelectedRegion(groups = groupedBuildings()) {
  const group = selectedGroup(groups);
  const selected = selectedType(groups);

  if (group && selected) {
    const { region, building } = selected;
    const selectedRate = buildingYoyRate(selected);
    state.selectedTypeId = typeId(region, building);
    document.getElementById("selected-region").innerHTML = `
      <div class="region-title">
        <div>
          <h2>${group.building_name}</h2>
          <span>${region.gu_name} ${region.dong_name}</span>
        </div>
        <div class="selected-actions">
          <label class="type-picker">
            <span>평형</span>
            <select id="type-select">
              ${group.types
                .map((item) => {
                  const id = typeId(item.region, item.building);
                  const isSelected = id === state.selectedTypeId ? "selected" : "";
                  return `<option value="${id}" ${isSelected}>${item.building.area_type}</option>`;
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
      <div class="metric-grid compact-detail">
        ${metricCard("거래가_억원", building.metrics.price_billion.avg, "price_billion")}
        ${metricCard("평단가_만원", building.metrics.price_per_pyeong.avg, "price_per_pyeong")}
        ${metricCard("거래횟수", building.count, "count")}
        ${metricCard("AI SCORE", aiScoreForItem(selected), "ai_score")}
        ${metricCard("전용평수", building.metrics.area_pyeong.avg, "area_pyeong")}
        ${metricCard("연식", `${format(buildingAge(building), "age")} · ${ageFilterText(ageCategory(building))}`, "text")}
        ${metricCard("세대수", building.households, "households")}
        ${metricCard("초품아/역세권", `${elementaryLabel(building)} · ${subwayLabel(building)}`, "text")}
      </div>
    `;
    document.getElementById("type-select").addEventListener("change", (event) => {
      state.selectedTypeId = event.target.value;
      renderSelectedRegion();
      renderAssetLists();
    });
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
      ${metricCard("AI SCORE", averageMetric(regions, "ai_score"), "ai_score")}
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
    .filter((group) => group.value !== null)
    .sort((a, b) => b.value - a.value);
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
    ...state.summary.years
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
    .slice(0, 80);

  document.getElementById("growth-ranking-list").innerHTML = rows.length
    ? rows
        .map((row, index) => `
          <li>
            <button type="button" class="growth-rank-row" data-growth-level="${row.level}" data-growth-key="${row.key}">
              <span>${index + 1}. ${row.name}</span>
              <strong>${formatHtml(row.value, "yoy_rate")}</strong>
            </button>
          </li>
        `)
        .join("")
    : `<li class="growth-empty">조건에 맞는 지역이 없습니다.</li>`;
}

function officialRegionAiScore(region) {
  const values = regionTypeItems(region)
    .map((item) => aiRecommendationForItem(item)?.house_match_score ?? null)
    .filter((value) => value !== null);
  return averageValues(values);
}

function renderAiScoreRankings() {
  const query = state.aiScoreSearch.trim().toLowerCase();
  const rows = aggregateRegionRates(state.aiScoreLevel, officialRegionAiScore, false)
    .filter((row) => !query || row.name.toLowerCase().includes(query))
    .filter((row) => state.aiScoreMin === null || row.value >= state.aiScoreMin)
    .slice(0, 80);

  document.getElementById("ai-score-ranking-list").innerHTML = rows.length
    ? rows
        .map((row, index) => `
          <li>
            <button type="button" class="growth-rank-row" data-growth-level="${row.level}" data-growth-key="${row.key}">
              <span>${index + 1}. ${row.name}</span>
              <strong>${format(row.value, "ai_score")}</strong>
            </button>
          </li>
        `)
        .join("")
    : `<li class="growth-empty">조건에 맞는 AI SCORE 지역이 없습니다.</li>`;
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
    ${dongs.map((region) => `<option value="${region.code}">${region.gu_name} ${region.dong_name}</option>`).join("")}
  `;
  document.getElementById("dong-select").value = state.selectedDong;
}

function populateGrowthPeriodSelect() {
  document.getElementById("growth-period-select").innerHTML = `
    <option value="all">전체기간</option>
    ${state.summary.years.map((year) => `<option value="${year}">${year}</option>`).join("")}
  `;
  document.getElementById("growth-period-select").value = state.growthPeriod;
}

function populateSubwayLineSelect() {
  const lines = new Set();
  for (const region of state.summary.regions) {
    for (const bucket of [region.all, ...Object.values(region.years ?? {})]) {
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

function refresh() {
  state.regionValueCache = new Map();
  state.mapDist = distribution();
  state.topologyLayer.setStyle(styleFeature);
  updateLegend();
  renderGrowthSummary();
  renderGrowthRankings();
  renderAiScoreRankings();
  const groups = groupedBuildings();
  renderSummary(groups);
  renderSelectedRegion(groups);
  renderAiRecommendations();
  renderAssetLists(groups);
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
}

function selectGroup(id, preferredTypeId = null) {
  const group = groupedBuildings().find((item) => item.id === id);
  if (!group) return;

  state.selectedGroupId = id;
  const preferred = group.types.find((item) => typeId(item.region, item.building) === preferredTypeId);
  const bestAiType = state.metric === "ai_score"
    ? [...group.types].sort((a, b) => (aiScoreForItem(b) ?? -1) - (aiScoreForItem(a) ?? -1))[0]
    : null;
  const selected = preferred ?? bestAiType ?? group.types[0];
  state.selectedTypeId = typeId(selected.region, selected.building);
  refresh();
}

function wireEvents() {
  document.getElementById("dark-mode-toggle").addEventListener("click", () => {
    const enabled = !document.body.classList.contains("dark-mode");
    localStorage.setItem("realEstateDashboardDarkMode", enabled ? "1" : "0");
    applyDarkMode(enabled);
  });

  document.getElementById("year-select").addEventListener("change", (event) => {
    state.year = event.target.value;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.getElementById("metric-select").addEventListener("change", (event) => {
    state.metric = event.target.value;
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
    refresh();
  });

  document.getElementById("min-price-input").addEventListener("input", (event) => {
    const value = parseFloat(event.target.value);
    state.minPriceBillion = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.getElementById("households-input").addEventListener("input", (event) => {
    const value = parseInt(event.target.value, 10);
    state.minHouseholds = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
  });

  document.getElementById("trade-count-input").addEventListener("input", (event) => {
    const value = parseInt(event.target.value, 10);
    state.minTradeCount = Number.isFinite(value) ? value : null;
    state.selectedGroupId = null;
    state.selectedTypeId = null;
    refresh();
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
    refresh();
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

  document.querySelectorAll("[data-map-mode]").forEach((button) => {
    button.addEventListener("click", () => setMapMode(button.dataset.mapMode));
  });

  document.querySelectorAll("[data-market-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.marketTab;
      document.querySelectorAll("[data-market-tab]").forEach((item) => {
        item.classList.toggle("active", item.dataset.marketTab === tab);
      });
      document.querySelectorAll("[data-market-panel]").forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.marketPanel === tab);
      });
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
  applyDarkMode(localStorage.getItem("realEstateDashboardDarkMode") === "1");

  const [summary, mapData] = await Promise.all([
    fetchJson(SUMMARY_URL, "요약 데이터"),
    fetchJson(MAP_URL, "지도 경계 데이터"),
  ]);

  state.summary = summary;
  state.recommendations = null;
  state.recommendationByType = new Map();
  state.regionByCode = new Map(summary.regions.map((region) => [region.code, region]));
  state.regionByMapCode = new Map();
  for (const region of summary.regions) {
    const mapCodes = region.map_codes || (region.map_code ? [region.map_code] : [region.code]);
    for (const mapCode of mapCodes) {
      if (!state.regionByMapCode.has(mapCode)) {
        state.regionByMapCode.set(mapCode, []);
      }
      state.regionByMapCode.get(mapCode).push(region);
    }
  }
  document.getElementById("year-select").innerHTML = `
    <option value="all">전체연도</option>
    ${summary.years.map((year) => `<option value="${year}">${year}</option>`).join("")}
  `;
  populateSidoSelect();
  populateGuSelect();
  populateDongSelect();
  populateGrowthPeriodSelect();
  populateSubwayLineSelect();

  const geojson = mapData.type === "Topology"
    ? topojson.feature(mapData, mapData.objects[Object.keys(mapData.objects)[0]])
    : mapData;
  state.topologyLayer = L.geoJSON(geojson, {
    style: styleFeature,
    onEachFeature(feature, layer) {
      const regions = regionsForFeature(feature);
      const region = regions[0];
      if (!region) return;
      layer.bindTooltip(`${region.sido_name} ${region.gu_name} ${region.dong_name}`, { sticky: true });
      layer.on("click", () => selectGu(region.gu_code));
    },
  }).addTo(map);

  fitDefaultMapView();
  wireEvents();
  refresh();

  fetchJson(RECOMMENDATIONS_URL, "AI 추천 데이터")
    .then((recommendations) => {
      state.recommendations = recommendations;
      state.recommendationByType = new Map(
        (recommendations?.recommendations ?? []).map((item) => [
          recommendationKey(item.region_code, item.building_key),
          item,
        ]),
      );
      refresh();
    })
    .catch((error) => {
      console.warn(error);
    });
}

function fitDefaultMapView() {
  const isMobile = window.matchMedia("(max-width: 980px)").matches;
  map.fitBounds(
    isMobile
      ? [
          [37.28, 126.43],
          [37.86, 127.26],
        ]
      : [
          [37.33, 126.49],
          [37.81, 127.18],
        ],
    { padding: isMobile ? [8, 8] : [12, 12], maxZoom: isMobile ? 10.2 : 10.6 },
  );
}

window.addEventListener("resize", () => {
  window.clearTimeout(state.resizeTimer);
  state.resizeTimer = window.setTimeout(() => {
    map.invalidateSize();
  }, 120);
});

init().catch((error) => {
  document.getElementById("selected-region").innerHTML =
    `<p class="empty-state">데이터를 불러오지 못했습니다. ${error.message}<br>반드시 http://localhost:8000/ 주소로 열어주세요.</p>`;
});
