const SUMMARY_URL = "data/seoul_real_estate_summary.json";
const SEOUL_LEGAL_DONG_TOPO_URL = "data/seoul_neighborhoods_topo_simple.json";

const metricLabels = {
  price_billion: { title: "거래가_억원", unit: "억", digits: 1 },
  price_per_pyeong: { title: "평단가_만원", unit: "만원/평", digits: 0 },
  land_pyeong: { title: "대지지분_평", unit: "평", digits: 1 },
  land_ratio: { title: "대지지분율", unit: "", digits: 3 },
  land_efficiency: { title: "토지효율지표", unit: "평/억", digits: 2 },
  area_pyeong: { title: "전용평수", unit: "평", digits: 1 },
};

const state = {
  summary: null,
  regionByCode: new Map(),
  year: "all",
  metric: "price_billion",
  dongLayer: null,
};

const map = L.map("map", {
  attributionControl: false,
  dragging: false,
  doubleClickZoom: false,
  scrollWheelZoom: false,
  boxZoom: false,
  keyboard: false,
  zoomControl: false,
  tap: false,
});

function bucketFor(region) {
  if (!region) return null;
  return state.year === "all" ? region.all : region.years[state.year] || null;
}

function metricAvg(region, metric = state.metric) {
  return bucketFor(region)?.metrics?.[metric]?.avg ?? null;
}

function activeRegions() {
  return state.summary.regions.filter((region) => metricAvg(region) !== null);
}

function format(value, metric = state.metric) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const label = metricLabels[metric];
  return `${Number(value).toLocaleString("ko-KR", {
    maximumFractionDigits: label.digits,
    minimumFractionDigits: 0,
  })}${label.unit}`;
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
  const values = activeRegions().map((region) => metricAvg(region));
  return {
    min: Math.min(...values),
    q20: quantile(values, 0.2),
    mid: quantile(values, 0.5),
    q80: quantile(values, 0.8),
    max: Math.max(...values),
  };
}

function interpolateColor(from, to, ratio) {
  const t = Math.max(0, Math.min(1, ratio));
  const rgb = from.map((value, index) => Math.round(value + (to[index] - value) * t));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function colorFor(value, dist) {
  if (value === null) return "#e4e8ef";
  if (value <= dist.mid) {
    return interpolateColor([33, 102, 172], [247, 247, 247], (value - dist.min) / (dist.mid - dist.min || 1));
  }
  return interpolateColor([247, 247, 247], [178, 24, 43], (value - dist.mid) / (dist.max - dist.mid || 1));
}

function styleFeature(feature) {
  const region = state.regionByCode.get(feature.properties.EMD_CD);
  const value = metricAvg(region);
  const dist = distribution();
  return {
    color: "#ffffff",
    weight: 0.45,
    opacity: 1,
    fillColor: colorFor(value, dist),
    fillOpacity: value === null ? 0.42 : 0.92,
    interactive: false,
  };
}

function updateLegend() {
  const dist = distribution();
  const label = metricLabels[state.metric];
  document.getElementById("legend-title").textContent =
    `${state.year === "all" ? "전체" : `${state.year}년`} · ${label.title}`;
  document.getElementById("legend-min").textContent = format(dist.min);
  document.getElementById("legend-mid").textContent = format(dist.mid);
  document.getElementById("legend-max").textContent = format(dist.max);
}

function renderZoneLists() {
  const ranked = activeRegions()
    .map((region) => ({ region, value: metricAvg(region) }))
    .sort((a, b) => b.value - a.value);

  renderZoneList("hot-list", ranked.slice(0, 12));
  renderZoneList("cold-list", [...ranked].reverse().slice(0, 12));
}

function renderZoneList(id, items) {
  document.getElementById(id).innerHTML = items
    .map(
      ({ region, value }) => `
        <li>
          <div class="zone-row">
            <span>${region.gu_name} ${region.dong_name}</span>
            <strong>${format(value)}</strong>
          </div>
        </li>
      `,
    )
    .join("");
}

function renderSummary() {
  const regions = activeRegions();
  const count = regions.reduce((sum, region) => sum + (bucketFor(region)?.count || 0), 0);
  document.getElementById("total-used").textContent = count.toLocaleString("ko-KR");
  document.getElementById("total-regions").textContent = regions.length.toLocaleString("ko-KR");
  document.getElementById("active-filter").textContent =
    `${state.year === "all" ? "전체" : state.year} · ${metricLabels[state.metric].title}`;
  document.getElementById("selected-region").innerHTML = `
    <div class="map-note">
      <strong>지도 기준</strong>
      <p>회색은 선택한 연도/지표에 거래 데이터가 없는 법정동입니다. 파란색에 가까울수록 낮고, 빨간색에 가까울수록 높습니다.</p>
    </div>
  `;
}

function refresh() {
  state.dongLayer.setStyle(styleFeature);
  updateLegend();
  renderZoneLists();
  renderSummary();
}

function wireEvents() {
  document.getElementById("year-select").addEventListener("change", (event) => {
    state.year = event.target.value;
    refresh();
  });
  document.getElementById("metric-select").addEventListener("change", (event) => {
    state.metric = event.target.value;
    refresh();
  });
}

async function init() {
  const [summary, topology] = await Promise.all([
    fetch(SUMMARY_URL).then((response) => response.json()),
    fetch(SEOUL_LEGAL_DONG_TOPO_URL).then((response) => response.json()),
  ]);

  state.summary = summary;
  state.regionByCode = new Map(summary.regions.map((region) => [region.code, region]));

  document.getElementById("year-select").innerHTML = `
    <option value="all">전체</option>
    ${summary.years.map((year) => `<option value="${year}">${year}</option>`).join("")}
  `;

  const objectName = Object.keys(topology.objects)[0];
  const geojson = topojson.feature(topology, topology.objects[objectName]);
  state.dongLayer = L.geoJSON(geojson, { style: styleFeature }).addTo(map);
  map.fitBounds(state.dongLayer.getBounds(), { padding: [12, 12] });

  wireEvents();
  refresh();
}

init().catch((error) => {
  document.getElementById("selected-region").innerHTML =
    `<p class="empty-state">지도 데이터를 불러오지 못했습니다. ${error.message}</p>`;
});
