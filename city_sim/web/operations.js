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
  let catalog;
  let calculated;
  let network;
  let weatherRequest = 0;
  let snowRequest = 0;
  let busy = false;
  const map = view.map;
  const weatherTerm = $('ops-term');
  const input = $('ops-input');
  const cityTerm = $('ops-city-term');
  const storm = $('ops-event');
  const cityMode = () => $('ops-scope').value === 'city';
  const resultStatus = $('ops-result-status');
  const inputReady = () => input.value === 'manual' || (cityMode()
    ? (input.value === 'historic' ? storm.value : cityTerm.value)
    : selected()?.sample?.snow_mm_3h != null);
  const currentRows = () => archive?.rows.filter(r => r.valid_at === weatherTerm.value) ?? [];
  const selected = () => currentRows().find(r => r.district_id === district);
  const active = () => !$('operations-panel').hidden;

  function updateButton() {
    $('ops-calculate').disabled = !health.snow_solver_connected || !inputReady() || busy;
  }

  function updateInputs() {
    const city = cityMode();
    $('ops-district-weather').hidden = city;
    input.querySelector('[value="historic"]').hidden = !city;
    if (!city && input.value === 'historic') input.value = 'archive';
    input.querySelector('[value="archive"]').textContent = city ? 'Архивная сетка OpenWeather' : 'Выбранный срок прогноза';
    $('ops-manual').hidden = input.value !== 'manual';
    $('ops-archive-input').hidden = input.value !== 'archive';
    $('ops-city-term-label').hidden = !city || input.value !== 'archive';
    $('ops-event-label').hidden = !city || input.value !== 'historic';
    $('ops-calculate').textContent = city ? 'Рассчитать для города' : 'Рассчитать для района';
    if (city && input.value === 'archive' && catalog && !cityTerm.value) {
      resultStatus.textContent = 'Локальный архив прогнозов OpenWeather недоступен.';
    }
    updateButton();
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
    const markers = active() && !cityMode() ? currentRows().map(r => point(r.coordinates, {
      status: r.status, district: r.district_id
    })) : [];
    const flagged = new Set(currentRows().filter(r => r.status === 'inspect').map(r => r.district_id));
    const highlighted = active() && !visible && !cityMode() ? network?.features.filter(f => flagged.has(f.properties.district_id)) : [];
    map.getSource('operations-roads').setData({ ...empty(), features: highlighted || [] });
    markers.push(...rank.map(r => point([r.lon, r.lat], {
      status: 'priority', edge: r.edge_id, name: r.name, value: r.value
    })));
    if (visible && choice.startsWith('weather_')) {
      markers.push(...calculated.weather_alerts.map(alert => point(alert.coordinates, {
        status: alert.extrapolated ? 'extrapolated' : 'inspect'
      })));
    }
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
      combined: 'Снег и рельеф · индекс', weather_snow: 'Свежий снег по погоде',
      weather_wind: 'Ветер по погоде · максимум за расчётный интервал',
      weather_distance: 'Расстояние до погодной точки'
    })[choice];
    const unit = ({weather_wind: 'м/с', weather_distance: 'км', weather_snow: 'см', snow_cm: 'см'})[choice];
    $('ops-scale-start').textContent = unit ? `0 ${unit}` : '0';
    $('ops-scale-end').textContent = unit ? `${calculated.scales?.[choice] ?? 15}+ ${unit}` : '1';
    $('ops-roads-label').textContent = choice.startsWith('weather_') ?
      (calculated.weather_alerts.length ? 'Точки · погодные сигналы. Фиолетовые · источник дальше 3 км.' : '') :
      choice === 'uniform' ? 'Равномерный фон · все сегменты имеют равный приоритет' :
      rank.length ? 'Дорожные сегменты с наибольшим индексом' : 'Приоритетные сегменты в этой области отсутствуют';
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

  async function runCity(token, data) {
    const start = await request('/operations/city/runs', data);
    const url = `/operations/city/runs/${start.run_id}`;
    while (token === snowRequest) {
      const result = await request(url);
      if (token !== snowRequest) return;
      if (result.status === 'completed') return result;
      if (result.status === 'failed') throw new Error(result.error);
      resultStatus.textContent = result.total_tiles == null ? 'Готовлю городской расчёт…' :
        `Весь город · обработано ${result.completed_tiles} из ${result.total_tiles} участков`;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  function showCity(result) {
    const m = result.metrics;
    const source = result.source;
    const settings = source.kind === 'openweather_forecast'
      ? `OpenWeather · архивный прогноз на ${time(source.valid_at)}`
      : source.kind === 'manual' ? 'Ручной сценарий'
        : `${source.title} · реанализ ECMWF IFS · ${time(source.start_at)}–${time(source.end_at).slice(-5)}`;
    const signal = m.road_m_weather_alert == null ? '' :
      ` · погодный сигнал на ${format(m.road_m_weather_alert/1000)} км`;
    resultStatus.textContent = `Весь город · ${settings} · ${format(m.road_m_covered/1000)} км графа${signal}`;
    lines($('ops-snow-checks'), [
      `Рассчитано участков · ${m.computed_tiles ?? 0} из ${m.eligible_tiles}`,
      `Участки без погодных входов · ${m.missing_weather_tiles ?? 0}`,
      `Использовано погодных входов · ${m.distinct_weather_inputs}`,
      ...(m.road_m_weather_local == null ? [] : [
        `Архив OpenWeather · ${source.snapshot_points} точек запроса, ${source.provider_coordinates_reported} координат в ответах`,
        `Погода перенесена с расстояния больше 3 км · ${m.extrapolated_weather_tiles ?? 0} участков`,
        `Погодный сигнал · ${m.weather_alert_tiles ?? 0} участков, из них ${m.weather_alert_tiles_local ?? 0} с точкой в пределах 3 км`
      ]),
      `Дороги с оценкой · ${format(m.road_m_covered/1000)} из ${format(m.road_m_in_city/1000)} км`,
      `В том числе расширение графа OSM · ${format((m.extended_road_m_covered ?? 0)/1000)} км с оценкой`,
      `Сегментов с оценкой · ${m.road_segments_covered ?? 0}`,
      'Граница города в пределах доступного рельефа. Результат моделирования требует проверки на местности.'
    ]);
  }

  $('ops-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!inputReady() || !health.snow_solver_connected || busy) return;
    const token = ++snowRequest;
    busy = true;
    updateButton();
    resultStatus.textContent = 'Рассчитываю снег и сравниваю контрольные варианты…';
    const city = cityMode();
    const linked = input.value === 'archive';
    const manual = {
      wind_speed_ms: $('ops-speed').valueAsNumber, wind_from_deg: $('ops-direction').valueAsNumber,
      snowfall_cm_h: $('ops-snow').valueAsNumber };
    const ratio = $('ops-ratio').valueAsNumber;
    try {
      const result = city ? await runCity(token, linked
        ? { source: 'openweather', valid_at: cityTerm.value, snow_to_liquid_ratio: ratio }
        : input.value === 'historic' ? { source: 'reanalysis', event_id: storm.value } : { source: 'manual', ...manual })
        : await request('/operations/snow', linked
          ? { district_id: district, forecast_sample_id: selected().sample.sample_id, snow_to_liquid_ratio: ratio }
          : { district_id: district, ...manual });
      if (token !== snowRequest || !result) return;
      calculated = result;
      if (city) {
        showCity(result);
      } else {
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
      }
      for (const option of $('ops-layer').options) {
        if (option.value.startsWith('weather_')) option.hidden = !city ||
          (option.value === 'weather_distance' && result.source.kind === 'manual');
      }
      $('ops-layer').value = 'combined';
      $('ops-snow-result').hidden = false;
      const [w0, s, e, n] = result.fit_bounds || result.bounds;
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

  weatherTerm.addEventListener('change', () => {
    const same = Array.from(cityTerm.options).find(option => Date.parse(option.value) === Date.parse(weatherTerm.value));
    if (same) cityTerm.value = same.value;
    resetSnow();
    showForecast();
  });
  $('ops-period').addEventListener('change', loadWeather);
  for (const control of [input, $('ops-scope'), cityTerm, storm]) control.addEventListener('change', () => {
    resetSnow();
    updateInputs();
    showForecast();
  });
  $('ops-layer').addEventListener('change', paint);
  $('ops-weather-json').addEventListener('click', () => { if (archive) download(`weather-${archive.run_id.slice(0, 12)}.json`, archive); });
  $('ops-snow-json').addEventListener('click', () => {
    if (!calculated) return;
    if (calculated.tile_cells) {
      const link = document.createElement('a');
      link.href = `/api/v1/operations/city/runs/${calculated.run_id}/replay`;
      link.click();
    } else download(`snow-${calculated.run_id.slice(0, 12)}.json`, calculated);
  });
  for (const tab of document.querySelectorAll('.tabs button')) tab.addEventListener('click', paint);
  map.on('click', 'operations-points', event => {
    const p = event.features[0]?.properties;
    if (p?.district && p.district !== district) {
      $('area').value = p.district;
      $('area').dispatchEvent(new Event('change'));
    }
  });
  loadWeather();
  request('/operations/city/catalog').then(data => {
    catalog = data;
    cityTerm.replaceChildren(...data.openweather.map(term => new Option(time(term.valid_at), term.valid_at)));
    storm.replaceChildren(...data.storms.map(event => new Option(event.title, event.event_id)));
    updateInputs();
  }).catch(error => { if (cityMode()) resultStatus.textContent = error.message; });
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
    if (!cityMode()) resetSnow();
    showForecast();
  } };
}
