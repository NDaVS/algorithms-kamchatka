"use strict";

// The dispatcher enters these assignments. Each ordered way follows its OSM geometry.
const shiftOrders = [
  {
    id: "З-001", vehicleId: "КМ-01", street: "Ленинская улица",
    ways: [400062402, 769907706, 769907710, 769907711, 769907712, 1218076727, 128466675],
    landmark: 11286817444, priority: "Высокий", delay: 0,
    reason: "Назначение диспетчера: очистить улицу в районе остановки «Аллея флота».",
  },
  {
    id: "З-002", vehicleId: "КМ-02", street: "Советская улица",
    ways: [771051011, 128262988],
    landmark: 11286817445, priority: "Высокий", delay: 20,
    reason: "Назначение диспетчера: рабочий проход по улице в районе одноимённой остановки.",
  },
  {
    id: "З-003", vehicleId: "КМ-03", street: "улица Красинцев",
    ways: [201238547],
    landmark: 1615059320, priority: "Обычный", delay: 55,
    reason: "Назначение диспетчера: очистить местную улицу. Остановка «Лицей №46» служит ориентиром района.",
  },
];

const vehicles = new Map([
  ["КМ-01", { label: "Плужная машина", speed: 3.2, work: "plow", initialNode: 1137636351 }],
  ["КМ-02", { label: "Плужная машина", speed: 3.9, work: "plow", initialNode: 7197555398 }],
  ["КМ-03", { label: "Компактный плуг", speed: 2.4, work: "plow", initialNode: 1137636071 }],
  ["КМ-04", { label: "Комбинированная машина", speed: 3.1, work: "both", initialNode: 458899702 }],
]);
const workLabels = { plow: "Очистка проезжей части", treat: "Противогололёдная обработка" };
const vehicleProfiles = {
  plow: { label: "Плужная машина", speed: 3.2 },
  treat: { label: "Распределитель реагентов", speed: 3.4 },
  both: { label: "Комбинированная машина", speed: 3.1 },
};
const priorities = new Set(["Обычный", "Высокий", "Критический"]);
const taskRoads = new Set(["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
  "residential", "living_street", "service", "motorway_link", "trunk_link", "primary_link",
  "secondary_link", "tertiary_link"]);
const storageKey = "kamchatka.shift.v1";
const el = id => document.getElementById(id);

function roadAssignmentBlock(road) {
  const { tags } = road;
  const access = tags.motor_vehicle ?? tags.vehicle ?? tags.access;
  if (access === "no") return "В данных OSM указан запрет проезда для машин.";
  if (access === "private") return "В данных OSM проезд отмечен как частный.";
  if (tags.area === "yes") return "В OSM это площадка. Для задания нужна линия проезда.";
  if (tags.highway === "track") return "В OSM это грунтовый или хозяйственный проезд. Для назначения техники этот класс пока выключен.";
  if (!taskRoads.has(tags.highway)) return "Этот тип линии в OSM не входит в сеть для уборочной техники.";
  return "";
}

function canAssignRoad(road) {
  return !roadAssignmentBlock(road);
}

function setupCanvas(id) {
  const canvas = el(id);
  const { width, height } = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const bitmapWidth = Math.round(width * ratio);
  const bitmapHeight = Math.round(height * ratio);
  if (canvas.width !== bitmapWidth || canvas.height !== bitmapHeight) {
    canvas.width = bitmapWidth;
    canvas.height = bitmapHeight;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function fieldList(values) {
  const contents = document.createElement("dl");
  for (const [label, value] of values) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const meaning = document.createElement("dd");
    meaning.textContent = value || "нет данных";
    row.append(term, meaning);
    contents.append(row);
  }
  return contents;
}

function processedCoverage(jobs) {
  const byDirection = new Map();
  for (const job of jobs) {
    for (const [i, edge] of job.edges.entries()) {
      const start = job.cumulative[i];
      if (start >= job.seen) break;
      if (!edge.work && (job.type !== "plow" || job.schedule)) continue;
      const drivenLength = job.cumulative[i + 1] - start;
      if (drivenLength <= 0) continue;
      const fraction = edge.startFraction ?? 0;
      const length = drivenLength / (1 - fraction);
      const offset = fraction * length;
      const from = offset + (edge.work ? 0 : Math.max(0, job.plowFrom - start));
      const to = offset + Math.min(drivenLength, job.seen - start);
      if (to <= from) continue;
      const key = edge.way + ":" + edge.from + ":" + edge.to;
      if (!byDirection.has(key)) {
        const b = job.points[i + 1];
        const a = job.points[i].map((value, axis) => (value - fraction * b[axis]) / (1 - fraction));
        byDirection.set(key, { a, b, length, spans: [] });
      }
      byDirection.get(key).spans.push([from, to]);
    }
  }

  const lines = [];
  let meters = 0;
  for (const { a, b, length, spans } of byDirection.values()) {
    const merged = [];
    for (const span of spans.sort((first, second) => first[0] - second[0])) {
      const last = merged.at(-1);
      if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
      else merged.push(span);
    }
    const pointAt = distance => a.map((value, i) => value + (b[i] - value) * distance / length);
    for (const [from, to] of merged) {
      meters += to - from;
      lines.push([pointAt(from), pointAt(to)]);
    }
  }
  return { lines, meters };
}

function createShift(city, project, repaint, focusRoute) {
  const scale = 16;
  const fmt = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
  const meters = value => value >= 1000 ? fmt.format(value / 1000) + " км" : Math.round(value) + " м";
  const timestamp = value => [Math.floor(value / 60), Math.floor(value % 60)].map(part => String(part).padStart(2, "0")).join(":");
  let catalog = new Map(vehicles);
  const nodeCoordinates = (spec, node) => spec.planNodes?.[node] ?? city.nodes[node];

  function buildOrder(spec, source = catalog) {
    const machine = source.get(spec.vehicleId);
    if (!machine || !priorities.has(spec.priority) || !Object.hasOwn(workLabels, spec.type) ||
        !Number.isFinite(spec.delay) || spec.delay < 0 || !spec.ways.length || !allowedType(machine, spec.type)) {
      throw new Error("Не удалось прочитать задание " + spec.id);
    }
    const edges = spec.edges ?? spec.ways.flatMap(way => {
      const [nodes, tags] = city.ways[way];
      const ordered = tags.oneway === "-1" ? [...nodes].reverse() : nodes;
      return ordered.slice(1).map((to, i) => ({ from: ordered[i], to, way, work: true }));
    });
    if (!edges.length) throw new Error("Нет маршрута " + spec.id);
    for (const [i, edge] of edges.entries()) {
      const way = city.ways[edge.way];
      if ((!spec.timeline && (!way || !canAssignRoad({ tags: way[1] }))) ||
          (i && edges[i - 1].to !== edge.from) || !nodeCoordinates(spec, edge.to) || !nodeCoordinates(spec, edge.from)) {
        throw new Error("Разрыв или недоступный участок " + spec.id);
      }
    }
    const points = [edges[0].from, ...edges.map(edge => edge.to)].map(ref => project(nodeCoordinates(spec, ref)));
    const fraction = edges[0].startFraction ?? 0;
    if (!Number.isFinite(fraction) || fraction < 0 || fraction >= 1) throw new Error("Некорректная позиция машины");
    if (fraction) points[0] = points[0].map((coordinate, axis) => coordinate + (points[1][axis] - coordinate) * fraction);
    const cumulative = [0];
    for (let i = 1; i < points.length; i++) {
      cumulative.push(cumulative[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]));
    }
    const length = cumulative.at(-1);
    if (length <= 0) throw new Error("Нулевая длина участка " + spec.id);
    const seen = Math.min(length, Math.max(0, Number(spec.seen) || 0));
    const plowFrom = Number.isFinite(spec.plowFrom) ? Math.max(0, Math.min(spec.plowFrom, seen)) : seen;
    let edgeIndex = 0;
    const schedule = spec.timeline?.map(([start, end, mode]) => {
      const a = cumulative[edgeIndex];
      if (mode !== "w") edgeIndex++;
      return { start, end, mode, a, b: cumulative[edgeIndex] };
    });
    if (schedule && (edgeIndex !== edges.length || !schedule.length)) throw new Error("Некорректное расписание");
    return {
      spec, id: spec.id, vehicleId: spec.vehicleId, vehicle: machine.label,
      street: spec.street, priority: spec.priority, type: spec.type, reason: spec.reason,
      state: spec.state ?? "active",
      speed: machine.speed, delay: spec.delay, points, cumulative, length, edges,
      landmarkPoint: spec.landmark ? project(city.nodes[spec.landmark]) : null,
      seen, plowFrom, blocked: spec.blocked === true, schedule, elapsed: spec.elapsed ?? 0,
    };
  }

  function allowedType(machine, type) {
    return machine.work === "both" || machine.work === type;
  }

  let time = 0;
  let playing = false;
  let previous = null;
  let frame = null;
  let lastUpdate = "";
  let lastCheckpoint = 0;
  let dataChanged = false;
  const store = shiftStore(window.sharedWorkspace === true);
  let operator = "Диспетчер";
  try { operator = sessionStorage.getItem("kamchatka.operator") || operator; } catch {}
  let meta = { id: newShiftId(), title: "Учебная смена", status: "open", createdAt: new Date().toISOString() };
  let vehicleIds = new Set(vehicles.keys());
  let revision = 0;
  let saving = null;
  let ready = false;
  let conflicted = false;
  let jobs = shiftOrders.map(order => buildOrder({ ...order, type: "plow" }));
  let selected = jobs[0];
  let events = [{ time: 0, message: "Назначены три задания смены" }];
  let targetRoads = [];
  let currentPlan = null;
  let plannedVehicle = null;
  let roadLinks;
  let coverage = { lines: [], meters: 0 };
  let action = null;

  function done(job) {
    return job.schedule ? job.elapsed >= job.schedule.at(-1).end : job.seen >= job.length;
  }

  function movable(job) {
    return job.state === "active" && !done(job);
  }

  function editable() {
    return ready && !conflicted && meta.status === "open";
  }

  function snapshot() {
    return {
      ...meta, version: 2, time, selectedId: selected?.id, events: [...events], vehicleIds: [...vehicleIds],
      addedVehicles: [...catalog].filter(([id]) => !vehicles.has(id)),
      jobs: jobs.map(job => ({ ...job.spec, priority: job.priority, state: job.state,
        seen: job.seen, plowFrom: job.plowFrom, elapsed: job.elapsed, blocked: job.blocked })),
    };
  }

  function activate(saved, savedRevision) {
    if (saved.version !== 2 || !Number.isFinite(saved.time) || saved.time < 0) throw new Error("Неизвестный формат смены");
    const restoredCatalog = new Map(vehicles);
    for (const [id, item] of saved.addedVehicles ?? []) {
      const initial = item.initialCoordinates ?? city.nodes[item.initialNode];
      if (restoredCatalog.has(id) || !Object.hasOwn(vehicleProfiles, item.work) ||
          !Array.isArray(initial) || initial.length !== 2 || !initial.every(Number.isFinite)) {
        throw new Error("Некорректная запись парка");
      }
      restoredCatalog.set(id, { ...vehicleProfiles[item.work], work: item.work, initialNode: item.initialNode, initialCoordinates: initial });
    }
    const restored = saved.jobs.map(job => buildOrder(job, restoredCatalog));
    const occupied = restored.filter(movable);
    if (new Set(restored.map(job => job.id)).size !== restored.length ||
        new Set(occupied.map(job => job.vehicleId)).size !== occupied.length ||
        occupied.some(job => !saved.vehicleIds.includes(job.vehicleId))) throw new Error("Некорректные назначения смены");
    stop();
    const { id, title, status, createdAt, closedAt, plan } = saved;
    meta = { id, title, status, createdAt, closedAt, plan };
    catalog = restoredCatalog;
    vehicleIds = new Set(saved.vehicleIds.filter(id => catalog.has(id)));
    jobs = restored;
    selected = jobs.find(job => job.id === saved.selectedId) ?? jobs[0] ?? null;
    time = saved.time;
    events = [...saved.events];
    revision = savedRevision;
    dataChanged = false;
    conflicted = false;
    try { sessionStorage.setItem(currentShiftKey, id); } catch {}
    rows.clear();
    el("shift-fleet").replaceChildren();
    jobs.forEach(addRow);
    renderEvents();
    lastUpdate = "";
    el("shift-notice").hidden = true;
    el("save-status").textContent = savedRevision ? "Смена загружена" : "";
    window.dispatchEvent(new Event("shift-changed"));
    render();
  }

  async function save(explicit = false) {
    if (saving) {
      const success = await saving;
      if (!success) return false;
      return save(false);
    }
    if (!dataChanged && !explicit) return true;
    if (!ready || conflicted || (meta.status === "closed" && !dataChanged)) return false;
    const state = snapshot();
    dataChanged = false;
    saving = (async () => {
    try {
        const saved = await store.write(state, revision);
        revision = saved.revision;
        el("save-status").textContent = store.shared ? "Сохранено в общей базе" : "Сохранено в браузере";
        return true;
      } catch (error) {
        dataChanged = true;
        if (error.message === "conflict") {
          conflicted = true;
          stop();
          notice("Смена уже изменена. Откройте её заново через «Смены». Несохранённые действия этой вкладки не отправлены.");
        }
        el("save-status").textContent = "Есть несохранённые изменения";
        render();
        return false;
      } finally {
        saving = null;
      }
    })();
    return saving;
  }

  function jobStatus(job) {
    if (job.state === "accepted") return "Принято";
    if (job.state === "canceled") return "Отменено";
    if (job.state === "transferred") return "Передано";
    if (done(job)) return "На приёмке";
    if (job.blocked) return "Препятствие";
    if (job.schedule) {
      const step = job.schedule.find(item => item.end > job.elapsed);
      return step.start > job.elapsed ? "Ожидает" :
        { d: "В пути", s: "В работе", w: "Ожидает", r: "Возвращается" }[step.mode];
    }
    if (job.seen === 0) return "Ожидает";
    const upcoming = job.cumulative.findIndex(distance => distance > job.seen) - 1;
    return job.edges[upcoming]?.work ? "В работе" : job.type === "plow" ? "Убирает по пути" : "В пути";
  }

  function workDistance(job, traveled) {
    return job.edges.reduce((sum, edge, i) => sum + (edge.work ?
      Math.max(0, Math.min(traveled, job.cumulative[i + 1]) - job.cumulative[i]) : 0), 0);
  }

  function originFor(id) {
    const prior = jobs.findLast(job => job.vehicleId === id);
    if (!prior) return { node: catalog.get(id).initialNode, previous: null };
    if (prior.seen >= prior.length) return { node: prior.edges.at(-1).to, previous: prior.edges.at(-1) };
    const index = prior.cumulative.findIndex(distance => distance > prior.seen) - 1;
    const edge = prior.edges[index];
    const part = (prior.seen - prior.cumulative[index]) / (prior.cumulative[index + 1] - prior.cumulative[index]);
    const initial = edge.startFraction ?? 0;
    const startFraction = initial + (1 - initial) * part;
    if (!startFraction) return { node: edge.from, previous: prior.edges[index - 1] ?? null };
    return { node: edge.to, previous: edge, departure: { ...edge, startFraction, work: false } };
  }

  function renderEvents() {
    const entries = events.slice(0, 5).map(item => {
      const row = document.createElement("li");
      const detail = document.createElement("span");
      detail.textContent = item.actor ? item.actor + " · " + item.message : item.message;
      const when = document.createElement("span");
      when.textContent = timestamp(item.time);
      row.append(detail, when);
      return row;
    });
    el("shift-events").replaceChildren(...entries);
  }

  function record(message) {
    events.unshift({ time, message, actor: operator, at: new Date().toISOString() });
    dataChanged = true;
    renderEvents();
  }

  const rows = new Map();
  function addRow(job) {
    const button = document.createElement("button");
    const id = document.createElement("span");
    id.className = "vehicle-number";
    id.textContent = job.vehicleId;
    const label = document.createElement("span");
    const heading = document.createElement("strong");
    heading.textContent = job.street;
    const work = document.createElement("small");
    work.textContent = job.id + " · " + workLabels[job.type];
    label.append(heading, work);
    const badge = document.createElement("span");
    button.append(id, label, badge);
    button.addEventListener("click", () => select(job, true));
    rows.set(job.id, { button, badge });
    el("shift-fleet").append(button);
  }

  function freeVehicles(type) {
    const busy = new Set(jobs.filter(movable).map(job => job.vehicleId));
    return [...catalog].filter(([id, item]) => vehicleIds.has(id) && !busy.has(id) && (!type || allowedType(item, type)));
  }

  function notice(message) {
    el("shift-notice").hidden = !message;
    el("shift-notice").textContent = message;
  }

  function render() {
    coverage = processedCoverage(jobs);
    const state = [meta.id, meta.status, editable(), selected?.id, selected?.priority, operator, vehicleIds.size,
      time.toFixed(0), playing, ...jobs.map(jobStatus)].join("|");
    if (lastUpdate !== state) {
      lastUpdate = state;
      el("shift-title").textContent = meta.title;
      el("shift-mode").textContent = (meta.status === "closed" ? "Закрыта" : "Открыта") +
        " · " + (store.shared ? "Общая база" : "Этот браузер") + " · " + operator;
      el("workspace-shifts").disabled = !ready;
      el("shift-park").disabled = !editable();
      el("shift-close").disabled = !editable();
      el("shift-save").disabled = !editable();
      el("shift-export").disabled = !ready;
      el("pick-assignment").disabled = !editable();
      el("segment-assignment").disabled = !editable();
      el("assignment-next").disabled = !editable() || !el("picked-roads").children.length;
      el("shift-clock").textContent = timestamp(time);
      el("shift-play").textContent = playing ? "Пауза" : time ? "Продолжить" : "Запустить смену";
      el("shift-play").disabled = !editable() || !jobs.some(movable);
      el("shift-active").textContent = jobs.filter(job => playing && movable(job) && !job.blocked && time > job.delay).length + " / " + vehicleIds.size;
      el("shift-completed").textContent = jobs.filter(job => job.state === "accepted").length + " / " +
        jobs.filter(job => job.state === "accepted" || job.state === "active").length;
      el("shift-distance").textContent = meters(coverage.meters);
      el("shift-reserve").textContent = "Свободно " + freeVehicles().length + " из " + vehicleIds.size;
      for (const job of jobs) {
        const { button, badge } = rows.get(job.id);
        button.setAttribute("aria-pressed", job === selected);
        badge.textContent = jobStatus(job);
        badge.className = "vehicle-status" + (job.state === "accepted" ? " status-good" : jobStatus(job) === "Препятствие" ? " status-warn" : "");
      }
      el("assignment-card").hidden = !selected;
      if (selected) {
      el("assignment-title").textContent = selected.street;
      const fields = [
        ["Задание", selected.id],
        ["Техника", selected.vehicleId + " · " + selected.vehicle],
        ["Работа", workLabels[selected.type]],
        ["Приоритет", selected.priority],
        ["По заданию", meters(workDistance(selected, selected.seen)) + " из " + meters(workDistance(selected, selected.length))],
        ["Переезд", meters(selected.seen - workDistance(selected, selected.seen)) + " из " + meters(selected.length - workDistance(selected, selected.length))],
      ];
      if (selected.type === "plow" && !selected.schedule) {
        const transitPlowed = selected.seen - selected.plowFrom -
          (workDistance(selected, selected.seen) - workDistance(selected, selected.plowFrom));
        fields.push(["Убрано по пути", meters(Math.max(0, transitPlowed))]);
      }
      if (selected.spec.landmark) fields.push(["Ориентир", city.points[selected.spec.landmark].name]);
      el("assignment-fields").replaceChildren(fieldList(fields));
      el("assignment-reason").textContent = selected.reason;
      el("assignment-block").textContent = selected.blocked ? "Снять препятствие" : "Отметить препятствие";
      el("assignment-block").hidden = !editable() || !movable(selected);
      el("assignment-edit").hidden = !editable() || !movable(selected) || Boolean(selected.schedule);
      el("assignment-accept").hidden = !editable() || selected.state !== "active" || !done(selected);
      el("assignment-cancel").hidden = !editable() || selected.state !== "active";
      }
    }
    repaint();
  }

  function select(job, focus) {
    selected = job;
    if (focus) focusRoute(job.points);
    render();
  }

  function tick(now) {
    frame = null;
    if (!playing) return;
    const before = time;
    if (previous !== null) time += Math.min((now - previous) / 1000, .1) * scale;
    previous = now;
    if (time !== before) dataChanged = true;
    for (const job of jobs) {
      if (job.blocked || !movable(job)) continue;
      const delta = Math.max(0, time - Math.max(before, job.delay));
      if (job.schedule) {
        const end = job.schedule.at(-1).end;
        job.elapsed = Math.min(end, job.elapsed + delta);
        const step = job.schedule.find(item => item.end > job.elapsed);
        const fraction = step ? Math.max(0, (job.elapsed - step.start) / (step.end - step.start)) : 0;
        job.seen = step ? step.a + (step.b - step.a) * fraction : job.length;
      } else {
        job.seen = Math.min(job.length, job.seen + job.speed * delta);
      }
      if (done(job)) {
        record(job.vehicleId + " · " + job.id + " передано на приёмку");
        if (el("assignment-dialog").open) refreshVehicleOptions();
      }
    }
    if (!jobs.some(movable)) {
      playing = false;
      previous = null;
      record("Движение остановлено: активных проходов нет");
      save();
    } else if (now - lastCheckpoint >= 1000) {
      lastCheckpoint = now;
      save();
    }
    render();
    if (playing) frame = requestAnimationFrame(tick);
  }

  function stop() {
    playing = false;
    previous = null;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }

  function planFrom(vehicleId, ways) {
    roadLinks ??= createRoadLinks(city, project);
    const origin = originFor(vehicleId);
    if (!city.nodes[origin.node] || (origin.departure && !city.ways[origin.departure.way])) return null;
    const edges = roadLinks.plan(origin, ways);
    if (!edges?.length) return null;
    return origin.departure ? [origin.departure, ...edges] : edges;
  }

  function nextJobId() {
    return "З-" + String(jobs.length + 1).padStart(3, "0");
  }

  function refreshVehicleOptions() {
    const options = freeVehicles(el("assignment-type").value);
    const menu = el("assignment-vehicle");
    const oldValue = menu.value;
    menu.replaceChildren(...options.map(([id, item]) => new Option(id + " · " + item.label, id)));
    if (options.some(([id]) => id === oldValue)) menu.value = oldValue;
    refreshRoutePreview();
  }

  function refreshRoutePreview() {
    const id = el("assignment-vehicle").value;
    if (id && (id !== plannedVehicle || currentPlan === null)) {
      currentPlan = planFrom(id, targetRoads.map(road => Number(road.id)));
      plannedVehicle = id;
    }
    el("assignment-submit").disabled = !id || !currentPlan;
    el("assignment-error").textContent = !id ? "Для этой работы сейчас нет свободной подходящей машины." :
      !currentPlan ? "Для этой машины не найден связный проезд. Измените порядок или состав участков." : "";
    if (!id || !currentPlan) {
      el("assignment-preview").textContent = "";
      return;
    }
    const lengths = { work: 0, travel: 0 };
    for (const edge of currentPlan) {
      const a = project(city.nodes[edge.from]);
      const b = project(city.nodes[edge.to]);
      lengths[edge.work ? "work" : "travel"] += Math.hypot(b[0] - a[0], b[1] - a[1]) * (1 - (edge.startFraction ?? 0));
    }
    el("assignment-preview").textContent = "Переезд " + meters(lengths.travel) + " · Рабочие проходы " + meters(lengths.work) +
      (el("assignment-type").value === "plow" && lengths.travel ? " · Уборка по пути включена" : "");
  }

  function openAssignment(roads) {
    if (!editable() || !roads.length || !roads.every(canAssignRoad)) return;
    targetRoads = [...roads];
    plannedVehicle = null;
    currentPlan = null;
    el("assignment-form").reset();
    const names = [...new Set(roads.map(road => road.name || "Участок без названия"))];
    el("assignment-road").textContent = "Участков: " + roads.length + " · " + names.join(", ");
    refreshVehicleOptions();
    el("assignment-dialog").showModal();
  }

  el("assignment-type").addEventListener("change", refreshVehicleOptions);
  el("assignment-vehicle").addEventListener("change", refreshRoutePreview);
  el("assignment-close").addEventListener("click", () => el("assignment-dialog").close());
  el("assignment-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!editable()) return;
    const vehicleId = el("assignment-vehicle").value;
    const type = el("assignment-type").value;
    const priority = el("assignment-priority").value;
    if (!targetRoads.length || !priorities.has(priority) || !freeVehicles(type).some(([id]) => id === vehicleId)) {
      refreshVehicleOptions();
      return;
    }
    refreshRoutePreview();
    if (!currentPlan) return;
    const id = nextJobId();
    const names = [...new Set(targetRoads.map(road => road.name || "Участок без названия"))];
    const job = buildOrder({
      id, vehicleId, type, priority, delay: time, ways: targetRoads.map(road => Number(road.id)),
      edges: currentPlan, street: names.join(" · "),
      reason: "Диспетчер выбрал участки в порядке: " + targetRoads.map(road => road.id).join(", ") + ".",
    });
    jobs.push(job);
    addRow(job);
    el("assignment-dialog").close();
    record(job.id + " · " + job.street + " назначено " + vehicleId);
    select(job, true);
    save();
    window.dispatchEvent(new Event("assignment-created"));
  });

  function remainingWays(job) {
    return [...new Set(job.edges.filter((edge, i) => edge.work && job.cumulative[i + 1] > job.seen).map(edge => edge.way))];
  }

  function openAction(kind) {
    if (!editable() || selected?.state !== "active" || (kind === "accept" ? !done(selected) : kind === "edit" && !movable(selected))) return;
    if (playing) {
      stop();
      record("Движение приостановлено для работы с заданием");
      save();
      render();
    }
    action = { kind, job: selected };
    const title = { accept: "Принять работу", cancel: "Отменить задание", edit: "Изменить задание" }[kind];
    el("action-title").textContent = title + " · " + selected.id;
    el("action-submit").textContent = title;
    el("action-edit-fields").hidden = kind !== "edit";
    el("action-priority").value = selected.priority;
    el("action-vehicle").replaceChildren(new Option(selected.vehicleId + " · текущее назначение", selected.vehicleId),
      ...freeVehicles(selected.type).map(([id, item]) => new Option(id + " · " + item.label, id)));
    el("action-reason").required = kind !== "accept";
    el("action-reason").value = "";
    el("action-error").textContent = "";
    el("action-dialog").showModal();
  }

  for (const kind of ["accept", "cancel", "edit"]) el("assignment-" + kind).addEventListener("click", () => openAction(kind));
  el("action-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!editable() || action?.job.state !== "active") return;
    const { job, kind } = action;
    const comment = el("action-reason").value.trim();
    if (kind !== "accept" && !comment) {
      el("action-error").textContent = "Укажите причину изменения.";
      return;
    }
    if (kind === "accept" && done(job)) {
      job.state = "accepted";
      record(job.id + " · работа принята" + (comment ? " · " + comment : ""));
    } else if (kind === "cancel") {
      job.state = "canceled";
      record(job.id + " · задание отменено · " + comment);
    } else if (kind === "edit" && movable(job)) {
      const priority = el("action-priority").value;
      const vehicleId = el("action-vehicle").value;
      if (!priorities.has(priority)) return;
      if (vehicleId === job.vehicleId) {
        if (job.priority === priority) {
          el("action-error").textContent = "Измените приоритет или выберите другую машину.";
          return;
        }
        record(job.id + " · приоритет: " + job.priority + " → " + priority + " · " + comment);
        job.priority = priority;
      } else {
        if (!freeVehicles(job.type).some(([id]) => id === vehicleId)) return;
        const ways = remainingWays(job);
        const edges = ways.length ? planFrom(vehicleId, ways) : null;
        if (!edges) {
          el("action-error").textContent = "Для новой машины нет связного пути к оставшимся участкам.";
          return;
        }
        const replacement = buildOrder({
          ...job.spec, id: nextJobId(), vehicleId, priority, delay: time, ways, edges,
          state: "active", seen: 0, plowFrom: 0, blocked: false, previousJobId: job.id,
          reason: "Передано из " + job.id + ". " + comment,
        });
        job.state = "transferred";
        jobs.push(replacement);
        addRow(replacement);
        record(job.id + " → " + replacement.id + " · " + job.vehicleId + " → " + vehicleId + " · " + comment);
        selected = replacement;
        focusRoute(replacement.points);
      }
    } else return;
    el("action-dialog").close();
    save();
    render();
  });

  function fleetFields(id, initial, lockBusy) {
    const fieldset = el(id);
    const entries = [...(lockBusy ? catalog : vehicles)].map(([id, vehicle]) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = id;
      checkbox.checked = initial.has(id);
      checkbox.disabled = lockBusy && jobs.some(job => job.vehicleId === id && movable(job));
      label.append(checkbox, id + " · " + vehicle.label + (checkbox.disabled ? " · есть активное задание" : ""));
      return label;
    });
    fieldset.replaceChildren(fieldset.querySelector("legend"), ...entries);
  }

  function pickedFleet(id) {
    return [...el(id).querySelectorAll("input:checked")].map(input => input.value);
  }

  async function history() {
    const error = el("workspace-error");
    error.textContent = "";
    try {
      const list = await store.list();
      const buttons = list.map(item => {
        const button = document.createElement("button");
        button.textContent = item.title + " · " + (item.status === "closed" ? "закрыта" : "открыта");
        button.addEventListener("click", async () => {
          try {
            stop();
            render();
            if (!conflicted && !await save()) throw new Error("Сначала сохраните текущую смену");
            const row = await store.read(item.id);
            activate(row.snapshot, row.revision);
            el("workspace-dialog").close();
          } catch (failure) {
            error.textContent = failure.message;
          }
        });
        return button;
      });
      el("workspace-list").replaceChildren(...(buttons.length ? buttons : [document.createTextNode("Сохранённых смен пока нет.")]));
    } catch (failure) {
      error.textContent = failure.message;
    }
  }

  el("workspace-shifts").addEventListener("click", () => {
    fleetFields("new-shift-fleet", new Set(vehicles.keys()), false);
    el("operator-name").value = operator;
    el("workspace-dialog").showModal();
    history();
  });
  el("operator-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = el("operator-name").value.trim();
    if (!name) return;
    operator = name;
    try { sessionStorage.setItem("kamchatka.operator", name); } catch {}
    render();
  });
  el("new-shift-form").addEventListener("submit", async event => {
    event.preventDefault();
    const title = el("new-shift-title").value.trim();
    if (!title) return;
    const button = event.submitter;
    button.disabled = true;
    try {
      stop();
      render();
      if (!conflicted && !await save()) throw new Error("Сначала сохраните текущую смену");
      const createdAt = new Date().toISOString();
      const state = { version: 2, id: newShiftId(), title, status: "open", createdAt, time: 0,
        vehicleIds: pickedFleet("new-shift-fleet"), jobs: [], events: [{ time: 0, at: createdAt, actor: operator, message: "Смена создана" }] };
      const row = await store.write(state, 0);
      activate(row.snapshot, row.revision);
      el("new-shift-form").reset();
      el("workspace-dialog").close();
    } catch (error) {
      el("workspace-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  el("shift-park").addEventListener("click", () => {
    if (!editable()) return;
    fleetFields("park-fleet", vehicleIds, true);
    el("vehicle-form").reset();
    el("vehicle-error").textContent = "";
    el("park-dialog").showModal();
  });
  el("park-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!editable()) return;
    const nextFleet = new Set(pickedFleet("park-fleet"));
    const added = [...nextFleet].filter(id => !vehicleIds.has(id));
    const removed = [...vehicleIds].filter(id => !nextFleet.has(id));
    if (added.length || removed.length) {
      vehicleIds = nextFleet;
      record([added.length ? "Добавлены в парк: " + added.join(", ") : "",
        removed.length ? "Сняты с линии: " + removed.join(", ") : ""].filter(Boolean).join(" · "));
      save();
      render();
    }
    el("park-dialog").close();
  });
  el("vehicle-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!editable()) return;
    const id = el("vehicle-id").value.trim().toLocaleUpperCase("ru-RU");
    const work = el("vehicle-type").value;
    const initialNode = Number(el("vehicle-node").value);
    if (!id || catalog.has(id)) {
      el("vehicle-error").textContent = "Укажите уникальный номер машины.";
      return;
    }
    if (!Object.hasOwn(vehicleProfiles, work) || !city.nodes[initialNode]) return;
    const picked = new Set(pickedFleet("park-fleet"));
    catalog.set(id, { ...vehicleProfiles[work], work, initialNode });
    vehicleIds.add(id);
    picked.add(id);
    fleetFields("park-fleet", picked, true);
    record("В парк поставлена машина " + id + " · " + vehicleProfiles[work].label);
    el("vehicle-form").reset();
    el("vehicle-error").textContent = "";
    save();
    render();
  });
  el("shift-close").addEventListener("click", async () => {
    if (!editable()) return;
    const pending = jobs.filter(job => job.state === "active");
    if (pending.length) {
      notice("В смене есть незакрытые задания (" + pending.length + "). Примите выполненные или отмените оставшиеся.");
      return;
    }
    stop();
    meta = { ...meta, status: "closed", closedAt: new Date().toISOString() };
    record("Смена закрыта");
    lastUpdate = "";
    render();
    const saved = await save();
    if (saved) {
      window.dispatchEvent(new Event("shift-changed"));
      notice("Смена закрыта. Её можно открыть в истории и выгрузить журнал.");
    }
    else if (!conflicted) {
      meta = { ...meta, status: "open", closedAt: undefined };
      events.shift();
      renderEvents();
      notice("Закрытие не сохранено. Проверьте доступность хранилища и повторите.");
      render();
    }
  });
  el("shift-export").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot(), null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "shift-" + meta.id + ".json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  for (const button of document.querySelectorAll("[data-close-dialog]")) {
    button.addEventListener("click", () => button.closest("dialog").close());
  }

  el("shift-play").addEventListener("click", () => {
    if (!editable() || !jobs.some(movable)) return;
    if (playing) {
      stop();
      record("Смена приостановлена");
    } else {
      record(time ? "Смена продолжена" : "Смена запущена");
      playing = true;
      frame = requestAnimationFrame(tick);
    }
    save();
    render();
  });
  el("shift-save").addEventListener("click", () => save(true));
  window.addEventListener("pagehide", () => save());
  el("assignment-block").addEventListener("click", () => {
    if (!editable() || !selected || !movable(selected)) return;
    selected.blocked = !selected.blocked;
    record(selected.vehicleId + (selected.blocked ? " · препятствие отмечено" : " · препятствие снято"));
    save();
    render();
  });
  el("assignment-focus").addEventListener("click", () => focusRoute(selected.points));
  el("fullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      el("fullscreen").title = "Полноэкранный режим доступен через меню браузера";
    }
  });

  async function initialize() {
    try {
      const list = await store.list();
      let current;
      try { current = sessionStorage.getItem(currentShiftKey); } catch {}
      const available = list.find(item => item.id === current) ?? list[0];
      if (available) {
        const row = await store.read(available.id);
        ready = true;
        activate(row.snapshot, row.revision);
        return;
      }
      if (!store.shared) {
        const legacy = JSON.parse(localStorage.getItem(storageKey) || "null");
        if (legacy?.version === 1) {
          ready = true;
          activate({ ...snapshot(), time: legacy.time, selectedId: legacy.selectedId,
            jobs: legacy.jobs, events: legacy.events }, 0);
          record("Локальная смена перенесена из предыдущей версии");
          await save();
          return;
        }
      }
    } catch (error) {
      if (store.shared) conflicted = true;
      notice(error.message);
    }
    ready = true;
    render();
  }

  async function acceptPlan(bundle, method) {
    const plan = bundle.methods[method];
    if (!ready || !plan?.routes.some(route => route.steps.some(step => step[2] === "s"))) {
      throw new Error("Нет рассчитанных рабочих проходов для назначения");
    }
    const addedVehicles = bundle.fleet.map(vehicle => [vehicle.vehicle_id, {
      work: "plow", initialNode: vehicle.depot_node, initialCoordinates: bundle.nodes[vehicle.depot_node],
    }]);
    const reason = `План ${method} · ${plan.id}. Проходы ${plan.validation.completed_passes} из ${plan.validation.required_passes}.`;
    const plannedJobs = plan.routes.filter(route => route.steps.some(step => step[2] !== "w")).map(route => {
      const edges = [], planNodes = {};
      for (const [, , mode, from, to, way, taskId] of route.steps) {
        if (mode === "w") continue;
        edges.push({ from, to, way, work: mode === "s", taskId });
        planNodes[from] = bundle.nodes[from];
        planNodes[to] = bundle.nodes[to];
      }
      const service = edges.filter(edge => edge.work);
      return {
        id: `ПЛАН-${route.vehicle}`, vehicleId: route.vehicle, street: `Маршрут ${route.vehicle}`,
        reason, type: "plow", priority: "Обычный", delay: 0,
        ways: [...new Set((service.length ? service : edges).map(edge => edge.way))],
        edges, planNodes, timeline: route.steps.map(step => step.slice(0, 3)),
      };
    });
    const createdAt = new Date().toISOString();
    const state = {
      version: 2, id: newShiftId(), title: `Смена по плану ${method} · ${plan.id.slice(0, 8)}`,
      status: "open", createdAt, time: 0, addedVehicles, vehicleIds: addedVehicles.map(([id]) => id),
      jobs: plannedJobs, events: [{ time: 0, at: createdAt, actor: operator, message: reason }],
      plan: { id: plan.id, method, status: plan.status, inputHash: bundle.hash, graphVersion: bundle.graphVersion,
        completedPasses: plan.validation.completed_passes, requiredPasses: plan.validation.required_passes },
    };
    const plannedCatalog = new Map(addedVehicles.map(([id, vehicle]) => [id, { ...vehicleProfiles.plow, ...vehicle }]));
    plannedJobs.forEach(job => buildOrder(job, plannedCatalog));
    stop();
    render();
    if (editable() && !await save(true)) throw new Error("Сначала сохраните текущую смену");
    const saved = await store.write(state, 0);
    activate(saved.snapshot, saved.revision);
  }

  jobs.forEach(addRow);
  renderEvents();
  initialize();
  return {
    get fleet() { return [...new Map(jobs.filter(job => vehicleIds.has(job.vehicleId)).map(job => [job.vehicleId, job])).values()]; },
    get unassignedVehicles() {
      return [...catalog].filter(([id, vehicle]) => vehicleIds.has(id) && vehicle.initialNode && !jobs.some(job => job.vehicleId === id))
        .map(([id, vehicle]) => ({ id, point: project(vehicle.initialCoordinates ?? city.nodes[vehicle.initialNode]) }));
    },
    get selected() { return selected; },
    get editable() { return editable(); },
    get coverage() { return coverage; },
    openAssignment, select, render, acceptPlan,
  };
}
