import { $, empty, format, request } from './api.js';
import { areas } from './map.js';

const titles = {
  inspect: 'Район требует проверки', missing: 'Прогноз недоступен',
  forecast_below_threshold: 'Сигналов выше порога нет'
};
const reasons = { snow: 'снегопад', wind: 'сильный ветер или порывы', source_conflict: 'сведения расходятся' };
const time = value => new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka',
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value));

function point(coordinates, properties) {
  return { type: 'Feature', geometry: { type: 'Point', coordinates }, properties };
}

function lines(node, values) {
  node.replaceChildren(...values.map(value => {
    const p = document.createElement('p');
    p.textContent = value;
    return p;
  }));
}

function download(file, data) {
  const href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = href;
  link.download = file;
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export function createOperations(view, health) {
  let district = 'center';
  let archive;
  let calculated;
  let network;
  let weatherRequest = 0;
  let snowRequest = 0;
  let busy = false;
  const map = view.map;
  const weatherTerm = $('ops-term');
  const input = $('ops-input');
  const resultStatus = $('ops-result-status');
  const inputReady = () => input.value === 'manual' || selected()?.sample?.snow_mm_3h != null;
  const currentRows = () => archive?.rows.filter(r => r.valid_at === weatherTerm.value) ?? [];
  const selected = () => currentRows().find(r => r.district_id === district);
  const active = () => !$('operations-panel').hidden;

  function updateButton() {
    $('ops-calculate').disabled = !health.snow_solver_connected || !inputReady() || busy;
  }

  function showForecast() {
    const row = selected();
    const sample = row?.sample;
    const current = row?.observation;
    if (row) {
      $('ops-signal').dataset.state = row.status;
      $('ops-signal').textContent = [titles[row.status], ...row.reasons.map(key => reasons[key]).filter(Boolean)].join(' · ');
    }
    lines($('ops-weather'), [
      sample?.snow_mm_3h == null ? 'Снег за 3 ч · значение отсутствует' : `Снег за 3 ч · ${format(sample.snow_mm_3h)} мм водного эквивалента`,
      sample ? `Ветер · ${format(sample.wind.speed_mps)} м/с · от ${sample.wind.direction_from_deg}°` : '',
      sample?.weather.retrieved_at ? `Получено ${time(sample.weather.retrieved_at)} · OpenWeather Forecast` : '',
      current ? `OpenWeather Current на момент решения · ${time(current.weather.valid_at)}` : 'Свежая запись Current на момент решения отсутствует',
      network?.length_m_by_district[district] ? `Дорог в зоне района · ${format(network.length_m_by_district[district]/1000)} км графа` : ''
    ].filter(Boolean));
    updateButton();
    paint();
  }

  function paint() {
    const visible = active() && calculated;
    const choice = $('ops-layer').value;
    const rank = visible ? calculated.ranking[choice === 'snow_cm' ? 'combined' : choice] || [] : [];
    const markers = active() ? currentRows().map(r => point(r.coordinates, {
      status: r.status, district: r.district_id
    })) : [];
    const flagged = new Set(currentRows().filter(r => r.status === 'inspect').map(r => r.district_id));
    const highlighted = active() && !visible ? network?.features.filter(f => flagged.has(f.properties.district_id)) : [];
    map.getSource('operations-roads').setData({ ...empty(), features: highlighted || [] });
    markers.push(...rank.map(r => point([r.lon, r.lat], {
      status: 'priority', edge: r.edge_id, name: r.name, value: r.value
    })));
    map.getSource('operations-points').setData(markers.length ? { ...empty(), features: markers } : empty());
    map.setLayoutProperty('operations-snow', 'visibility', visible ? 'visible' : 'none');
    $('scene-snow-legend').hidden = !visible;
    if (!visible) return;
    const [w, s, e, n] = calculated.bounds;
    map.getSource('operations-snow').updateImage({
      url: calculated.overlays[choice], coordinates: [[w, n], [e, n], [e, s], [w, s]]
    });
    $('ops-scale-label').textContent = ({
      snow_cm: 'Модельная толщина свежего снега', terrain: 'Рельеф · индекс низины',
      uniform: 'Равномерный фон', opposite: 'Противоположный ветер · индекс',
      combined: 'Снег и рельеф · индекс'
    })[choice];
    $('ops-scale-start').textContent = choice === 'snow_cm' ? '0 см' : '0';
    $('ops-scale-end').textContent = choice === 'snow_cm' ? '15+ см' : '1';
    $('ops-roads-label').textContent = choice === 'uniform' ? 'Равномерный фон · все сегменты имеют равный приоритет' :
      rank.length ? 'Дорожные сегменты с наибольшим индексом в пилотном квадрате' : 'Приоритетные сегменты в этой области отсутствуют';
    $('ops-roads').replaceChildren(...(choice === 'uniform' ? [] : rank).map(r => {
      const li = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `${r.name || 'Сегмент дороги'} · ${format(r.value, 2)}`;
      button.title = r.edge_id;
      if (r.excess_cm != null) {
        const explanation = document.createElement('div');
        const excess = r.excess_cm > 0 ? '+' : '';
        explanation.textContent = `К фону ${excess}${format(r.excess_cm)} см` +
          (r.terrain_weight == null ? '' : ` · рельеф ${format(r.terrain_weight, 2)}`);
        li.append(button, explanation);
      } else {
        li.append(button);
      }
      button.addEventListener('click', () => map.easeTo({ center: [r.lon, r.lat], zoom: 16.2 }));
      return li;
    }));
  }

  function resetSnow() {
    ++snowRequest;
    busy = false;
    calculated = null;
    $('ops-snow-result').hidden = true;
    resultStatus.textContent = health.snow_solver_connected ? '' : 'Расчёт снега Данила не подключён.';
  }

  async function loadWeather() {
    const token = ++weatherRequest;
    resetSnow();
    archive = null;
    weatherTerm.replaceChildren();
    $('ops-weather-json').disabled = true;
    $('ops-signal').textContent = 'Загружаю прогноз';
    updateButton();
    paint();
    try {
      const result = await request(`/operations/weather?period=${$('ops-period').value}`);
      if (token !== weatherRequest) return;
      archive = result;
      const terms = [...new Set(result.rows.map(row => row.valid_at))];
      weatherTerm.replaceChildren(...terms.map(value => new Option(time(value), value)));
      const m = result.metrics;
      const b = result.benchmark.metrics;
      lines($('ops-weather-checks'), [
        `Архив · районных сигналов ${m.candidate_inspection_slots}, общий прогноз ${m.baseline_inspection_slots}`,
        `Доступность · ${m.forecast_terms} из ${m.district_terms} точек и сроков`,
        `Условные тесты · обнаружено ${b.candidate.tp} из ${b.candidate.tp+b.candidate.fn}, общий прогноз ${b.baseline.tp}`,
        'Выезды и фактическое состояние дороги в архиве отсутствуют'
      ]);
      $('ops-weather-json').disabled = false;
      showForecast();
    } catch (error) {
      if (token === weatherRequest) $('ops-signal').textContent = error.message;
    }
  }

  $('ops-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!inputReady() || !health.snow_solver_connected || busy) return;
    const token = ++snowRequest;
    busy = true;
    updateButton();
    resultStatus.textContent = 'Рассчитываю снег и сравниваю контрольные варианты…';
    const linked = input.value === 'archive';
    const data = linked ? { district_id: district, forecast_sample_id: selected().sample.sample_id,
      snow_to_liquid_ratio: $('ops-ratio').valueAsNumber } : { district_id: district,
      wind_speed_ms: $('ops-speed').valueAsNumber, wind_from_deg: $('ops-direction').valueAsNumber,
      snowfall_cm_h: $('ops-snow').valueAsNumber };
    try {
      const result = await request('/operations/snow', data);
      if (token !== snowRequest) return;
      calculated = result;
      const w = result.resolved_weather;
      resultStatus.textContent = `${result.district} · ${linked ? time(result.weather.weather.valid_at) : 'ручной сценарий'} · ${format(w.wind_speed_ms)} м/с от ${w.wind_from_deg}° · ${format(w.snowfall_cm)} см за ${w.hours} ч`;
      const m = result.metrics;
      const fraction = value => value == null ? 'нет сравнения' : `${format(value * 100, 0)}%`;
      const building = result.building_height_sources;
      lines($('ops-snow-checks'), [
        `Сегментов в расчёте · ${m.road_segments_covered}`,
        `Зданий с оценкой по сторонам · ${m.building_sides_covered}. Нажмите здание на карте.`,
        `Спокойный ветер и смена стороны за зданием · ${m.calm_flat_pass && result.directional_control.passes ? 'проверки пройдены' : 'ошибка контроля'}`,
        `Разница при развороте ветра, средняя по сетке · ${format(m.reverse_mae_cm, 2)} см`,
        `Разница при уменьшении оценочных высот на 25% · ${format(m.shorter_buildings_mae_cm, 2)} см`,
        `Совпадение списков при развороте ветра, по Жаккару · ${fraction(m.top_k_reverse_overlap)}`,
        `Неизвестная высота · ${building.unknown ?? 0} контуров в окрестности, в расчёте принято 9 м`,
        'Рельеф добавлен как вес индекса. Замеры наносов отсутствуют.'
      ]);
      $('ops-layer').value = 'combined';
      $('ops-snow-result').hidden = false;
      const [w0, s, e, n] = result.bounds;
      if (active()) map.fitBounds([[w0, s], [e, n]], { padding: 55, pitch: 0, bearing: 0, duration: 550 });
      paint();
    } catch (error) {
      if (token === snowRequest) resultStatus.textContent = error.message;
    } finally {
      if (token === snowRequest) {
        busy = false;
        updateButton();
      }
    }
  });

  weatherTerm.addEventListener('change', () => { resetSnow(); showForecast(); });
  $('ops-period').addEventListener('change', loadWeather);
  input.addEventListener('change', () => {
    $('ops-manual').hidden = input.value !== 'manual';
    $('ops-archive-input').hidden = input.value === 'manual';
    resetSnow();
    showForecast();
  });
  $('ops-layer').addEventListener('change', paint);
  $('ops-weather-json').addEventListener('click', () => { if (archive) download(`weather-${archive.run_id.slice(0, 12)}.json`, archive); });
  $('ops-snow-json').addEventListener('click', () => { if (calculated) download(`snow-${calculated.run_id.slice(0, 12)}.json`, calculated); });
  for (const tab of document.querySelectorAll('.tabs button')) tab.addEventListener('click', paint);
  map.on('click', 'operations-points', event => {
    const p = event.features[0]?.properties;
    if (p?.district && p.district !== district) {
      $('area').value = p.district;
      $('area').dispatchEvent(new Event('change'));
    }
  });
  loadWeather();
  request('/operations/network').then(data => {
    network = data;
    if (archive) showForecast();
  }).catch(() => {});
  return { buildingDetails(asset) {
    if (!calculated || !active()) return [];
    const sides = calculated.building_sides[asset];
    if (!sides) return [];
    const option = $('ops-layer').value;
    if (option === 'terrain' || option === 'uniform') return [];
    const field = option === 'opposite' ? 'opposite_cm' : 'combined_cm';
    const names = { north: 'север', east: 'восток', south: 'юг', west: 'запад' };
    const ranking = Object.entries(sides).sort((a, b) => b[1][field] - a[1][field]);
    const most = ranking[0][1][field] - ranking[1][1][field] >= .1 ? names[ranking[0][0]] : 'выраженной стороны нет';
    return [
      `Свежий снег в полосах 5–30 м вокруг здания · ${most}`,
      ranking.map(([side, values]) => `${names[side]} ${format(values[field], 1)} см`).join(' · ')
    ];
  }, setArea(area) {
    if (!areas.some(candidate => candidate.id === area.id)) return;
    district = area.id;
    resetSnow();
    showForecast();
  } };
}
