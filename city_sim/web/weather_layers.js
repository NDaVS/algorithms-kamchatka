import { $, empty, format, request } from './api.js';

export const localTerm = value => new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka',
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value));

const lastImages = new WeakMap();

export function raster(map, id, bounds, url) {
  const source = map.getSource(id);
  const previous = lastImages.get(source);
  if (previous?.url === url && previous.bounds === bounds) return;
  const [w, s, e, n] = bounds;
  source.updateImage({ url, coordinates: [[w, n], [e, n], [e, s], [w, s]] });
  lastImages.set(source, { url, bounds });
}

function feature(coordinates, properties) {
  return { type: 'Feature', geometry: { type: 'Point', coordinates }, properties };
}

function windFeature(row, name, field) {
  const heading = row.wind_from_deg ?? row.wind_deg;
  const speed = row.wind_speed_ms ?? row.wind_speed;
  const gust = row.gust_ms ?? row.wind_gust;
  return feature(row.coordinates, {
    name,
    toward: (heading+180) % 360,
    speed,
    wind: heading == null || speed == null ? null : `Ветер ${format(speed)} м/с · откуда ${format(heading, 0)}°`,
    gust: gust == null ? null : `Порывы ${format(gust)} м/с`,
    reading: field?.value == null ? null : `${field.name} · ${format(field.value)} ${field.unit}`,
    distance: row.distance_to_source_m == null ? null : `До погодной точки ${format(row.distance_to_source_m/1000)} км`
  });
}

export function createWeatherLayers(map) {
  let dataset;
  let stamp;
  let catalog;
  let atlas;
  let result;
  let modelFrame;
  let token = 0;
  const field = $('atlas-field');
  const status = $('atlas-status');
  const display = () => field.value !== 'none';

  function legend() {
    $('scene-snow-legend').hidden = ['ops-scale-model', 'atlas-scale', 'ops-wind-legend'].every(id => $(id).hidden);
  }

  function paint() {
    const showAtlas = atlas && display() && atlas.grid_cells > 0;
    map.setLayoutProperty('operations-atlas', 'visibility', showAtlas ? 'visible' : 'none');
    if (showAtlas) {
      raster(map, 'operations-atlas', atlas.bounds, atlas.overlay);
      const { name, colors, unit, range } = atlas.field;
      $('atlas-scale-label').textContent = name;
      $('atlas-scale-start').textContent = `${format(range[0], 0)} ${unit}`;
      $('atlas-scale-mid').textContent = format((range[0]+range[1])/2);
      $('atlas-scale-end').textContent = `${format(range[1], 0)} ${unit}`;
      $('atlas-scale-gradient').style.background = `linear-gradient(90deg,${colors.join(',')})`;
    }
    $('atlas-scale').hidden = !showAtlas;
    const weather = result?.wind_frames?.[modelFrame ?? 0];
    const wind = weather?.points || atlas?.points || [];
    const arrows = $('ops-wind-arrows').checked
      ? wind.filter(p => (p.wind_speed_ms ?? p.wind_speed) > .2 && (p.wind_from_deg ?? p.wind_deg) != null)
        .map(p => windFeature(p, weather ? 'Ветер на входе модели' : atlas.source))
      : [];
    const points = $('atlas-points').checked && atlas
      ? atlas.points.map(p => windFeature(p, atlas.source, { ...atlas.field, value: p[field.value] })) : [];
    map.getSource('weather-arrows').setData({ ...empty(), features: arrows });
    map.getSource('weather-points').setData({ ...empty(), features: points });
    $('ops-wind-legend').hidden = !arrows.length;
    legend();
  }

  async function refresh() {
    const epoch = ++token;
    if (!dataset || !stamp || !catalog?.[dataset]) {
      atlas = undefined;
      status.textContent = '';
      paint();
      return;
    }
    const name = display() ? field.value : 'wind_speed';
    const params = new URLSearchParams({
      dataset, valid_at: stamp, field: name, method: $('atlas-method').value
    });
    try {
      const frame = await request(`/operations/atlas/frame?${params}`);
      if (epoch !== token) return;
      atlas = frame;
      status.textContent = `${frame.source} · ${localTerm(frame.valid_at)} · ${frame.points.length} точек` +
        (display() && !frame.grid_cells ? ' · значений слоя нет' : '');
      paint();
    } catch (error) {
      if (epoch !== token) return;
      atlas = undefined;
      status.textContent = error.message;
      paint();
    }
  }

  field.addEventListener('change', refresh);
  $('atlas-method').addEventListener('change', refresh);
  for (const id of ['ops-wind-arrows', 'atlas-points']) $(id).addEventListener('change', paint);
  $('atlas-opacity').addEventListener('input', event => {
    map.setPaintProperty('operations-atlas', 'raster-opacity', event.target.valueAsNumber/100);
  });
  map.setPaintProperty('operations-atlas', 'raster-opacity', $('atlas-opacity').valueAsNumber/100);

  return {
    setCatalog(value) { catalog = value; },
    setWeather(source, timestamp) {
      const sourceChanged = source !== dataset;
      if (sourceChanged || timestamp !== stamp) atlas = undefined;
      if (sourceChanged) {
        const previous = field.value;
        const options = catalog?.[source]?.fields || {};
        field.replaceChildren(new Option('Выключен', 'none'), ...Object.entries(options)
          .map(([key, item]) => new Option(item.name, key)));
        field.value = previous in options ? previous : options.snow ? 'snow' : 'none';
      }
      [dataset, stamp] = [source, timestamp];
      paint();
      for (const id of ['atlas-field-row', 'atlas-points-row', 'atlas-display-settings']) $(id).hidden = !source;
      refresh();
    },
    setResult(value) {
      result = value;
      modelFrame = 0;
      paint();
    },
    setFrame(index) { modelFrame = index; paint(); },
    legend
  };
}
