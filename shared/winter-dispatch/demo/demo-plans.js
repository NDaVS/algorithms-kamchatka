"use strict";

(() => {
  const city = window.petropavlovsk;
  const get = id => document.getElementById(id);
  const screen = get("demo-workspace");
  const map = get("demo-map");
  const base = get("demo-base-map");
  const ctx = map.getContext("2d");
  const bg = base.getContext("2d");
  const project = ([lon, lat]) => [(lon - 158.62) * 66900, (53.05 - lat) * 111200];
  const format = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
  const stamp = seconds => [Math.floor(seconds / 60), Math.floor(seconds % 60)].map(n => String(n).padStart(2, "0")).join(":");
  const colors = { plan: "#70d9ef", clean: "#b9f472", reserve: "#ffc486", missed: "#ff8c80" };
  const pilot = window.snowOpsPilot;
  const roadPoints = Object.fromEntries(Object.entries(city.nodes).map(([id, coord]) => [id, project(coord)]));
  const major = new Set(["trunk", "primary", "secondary", "tertiary", "primary_link", "secondary_link"]);
  const backgroundRoads = Object.values(city.ways).filter(([, tags]) => tags.highway)
    .map(([refs, tags]) => ({ points: refs.map(id => roadPoints[id]), major: major.has(tags.highway), name: tags.name }));
  const areas = Object.values(city.land).filter(([kind, refs]) => refs.length > 2 &&
    ["water", "wood", "forest", "park", "grass", "building"].includes(kind))
    .map(([kind, refs]) => ({ kind, points: refs.map(id => roadPoints[id]) }));
  const source = new Map(Object.entries(pilot).map(([key, data]) => [key, { data, live: false }]));
  let bundle, method = "O1", points, routes, missed, tasks, eligible, reserves, incidents;
  let time = 0, nextEvent = 0, running = false, lastFrame = null, frame = null, horizon = 0;
  let size = [0, 0], zoom = 1, center = [0, 0], cameraDirty = true, drag;
  let links, request = 0, calculating = false, accepting = false, live = false;
  const current = () => bundle?.methods[method];
  const setNotice = message => { get("demo-notice").textContent = message; };
  const interpolate = (a, b, part) => [a[0] + (b[0] - a[0]) * part, a[1] + (b[1] - a[1]) * part];

  function open(showDemo) {
    screen.hidden = !showDemo;
    get("dispatch-workspace").hidden = showDemo;
    get("show-demo").removeAttribute("aria-current");
    get("show-dispatch").removeAttribute("aria-current");
    get(showDemo ? "show-demo" : "show-dispatch").setAttribute("aria-current", "page");
    if (showDemo) requestAnimationFrame(() => { if (!screen.hidden) fit(); });
    else pause();
  }

  function fit() {
    if (!points || screen.hidden) return;
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const [x, y] of Object.values(points)) {
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    center = [(left + right) / 2, (top + bottom) / 2];
    zoom = Math.min(Math.max(1, map.clientWidth - 90) / Math.max(100, right - left),
      Math.max(1, map.clientHeight - 75) / Math.max(100, bottom - top));
    cameraDirty = true;
    draw();
  }

  function mapPoint([x, y]) {
    return [(x - center[0]) * zoom + size[0] / 2, (y - center[1]) * zoom + size[1] / 2];
  }

  function trace(context, line) {
    line.forEach((point, index) => {
      const [x, y] = mapPoint(point);
      if (index) context.lineTo(x, y);
      else context.moveTo(x, y);
    });
  }

  function line(context, path, color, width) {
    context.beginPath();
    trace(context, path);
    context.strokeStyle = color;
    context.lineWidth = width;
    context.stroke();
  }

  function background() {
    const [w, h] = size;
    bg.fillStyle = "#14242e";
    bg.fillRect(0, 0, w, h);
    for (const area of areas) {
      bg.fillStyle = area.kind === "water" ? "#12303c" : area.kind === "building" ? "#233641" : "#1a3534";
      bg.beginPath();
      trace(bg, area.points);
      bg.fill();
    }
    for (const prominent of [false, true]) {
      bg.beginPath();
      for (const road of backgroundRoads) {
        if (road.major === prominent) trace(bg, road.points);
      }
      bg.strokeStyle = prominent ? "#6b8391" : "#345160";
      bg.lineWidth = prominent ? 1.35 : .75;
      bg.stroke();
    }
    const occupied = new Set();
    bg.font = "600 11px system-ui";
    bg.textAlign = "center";
    for (const road of backgroundRoads) {
      if (!road.major || !road.name || occupied.has(road.name)) continue;
      const [x, y] = mapPoint(road.points[Math.floor(road.points.length / 2)]);
      const key = Math.floor(x / 145) + ":" + Math.floor(y / 25);
      if (x < 75 || y < 20 || x > w - 75 || y > h - 15 || occupied.has(key)) continue;
      bg.lineWidth = 3;
      bg.strokeStyle = "#14242e";
      bg.strokeText(road.name, x, y - 4);
      bg.fillStyle = "#b5c7d1";
      bg.fillText(road.name, x, y - 4);
      occupied.add(key);
      occupied.add(road.name);
    }
  }

  function pointAt(steps, at, origin) {
    let low = 0, high = steps.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (steps[mid].end <= at) low = mid + 1;
      else high = mid;
    }
    const step = steps[low];
    if (!step) return { position: steps.at(-1)?.b ?? origin, mode: "w" };
    if (at < step.start) return { position: step.a, mode: "w" };
    return { position: interpolate(step.a, step.b, (at - step.start) / (step.end - step.start)), mode: step.mode };
  }

  function marker(position, label, color, mode) {
    const [x, y] = mapPoint(position);
    ctx.strokeStyle = "#12212a";
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    if (mode === "event") {
      ctx.moveTo(x, y - 8); ctx.lineTo(x + 8, y); ctx.lineTo(x, y + 8); ctx.lineTo(x - 8, y); ctx.closePath();
    } else {
      ctx.roundRect(x - 6, y - 4.5, 12, 9, 2);
    }
    ctx.fill();
    ctx.stroke();
    if (!label) return;
    ctx.font = "700 11px system-ui";
    ctx.textAlign = "left";
    ctx.lineWidth = 3;
    ctx.strokeText(label, x + 9, y - 6);
    ctx.fillText(label, x + 9, y - 6);
  }

  function draw() {
    if (screen.hidden) return;
    const dimensions = [map.clientWidth, map.clientHeight];
    if (!dimensions[0] || !dimensions[1]) return;
    if (dimensions.some((value, axis) => value !== size[axis])) {
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      size = dimensions;
      for (const [canvas, context] of [[map, ctx], [base, bg]]) {
        canvas.width = Math.round(size[0] * pixelRatio);
        canvas.height = Math.round(size[1] * pixelRatio);
        context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      }
      cameraDirty = true;
    }
    if (cameraDirty) { background(); cameraDirty = false; }
    ctx.clearRect(0, 0, ...size);
    if (!bundle) return;
    ctx.lineCap = "round";
    for (const task of missed) line(ctx, [points[task.from], points[task.to]], colors.missed, 3.5);
    for (const route of routes) {
      const active = route.steps.find(step => step.start <= time && step.end > time);
      if (active && ["d", "r"].includes(active.mode)) {
        ctx.setLineDash([4, 4]);
        line(ctx, [pointAt(route.steps, time).position, active.b], "#91a9b4", 1.8);
        ctx.setLineDash([]);
      }
      for (const step of route.service) {
        if (time <= step.start) line(ctx, [step.a, step.b], colors.plan, 3.2);
        else if (time >= step.end) line(ctx, [step.a, step.b], colors.clean, 3.2);
        else {
          const middle = interpolate(step.a, step.b, (time - step.start) / (step.end - step.start));
          line(ctx, [middle, step.b], colors.plan, 3.2);
          line(ctx, [step.a, middle], colors.clean, 3.2);
        }
      }
    }
    for (const event of incidents) {
      if (["queued", "driving", "working"].includes(event.status)) {
        marker(interpolate(points[event.task.from], points[event.task.to], .5), "", colors.reserve, "event");
      }
    }
    for (const reserve of reserves) {
      const steps = reserve.steps;
      for (const step of steps) {
        if (time >= step.end) continue;
        const start = time <= step.start ? step.a : interpolate(step.a, step.b, (time - step.start) / (step.end - step.start));
        ctx.setLineDash(step.mode === "s" ? [] : [4, 4]);
        line(ctx, [start, step.b], colors.reserve, 2.5);
      }
      ctx.setLineDash([]);
      const state = pointAt(steps, time, roadPoints[reserve.node]);
      marker(state.position, reserve.id, colors.reserve);
    }
    const positions = new Set();
    for (const route of routes) {
      const state = pointAt(route.steps, time);
      const key = state.position.join(":");
      marker(state.position, positions.has(key) ? "" : route.vehicle, state.mode === "s" ? colors.clean : colors.plan);
      positions.add(key);
    }
  }

  function describe() {
    const comparison = get("demo-comparison");
    comparison.replaceChildren();
    if (!bundle) return;
    const columns = [
      ["Проходы", p => p.validation.completed_passes + " / " + p.validation.required_passes],
      ["Уборка", p => format.format(p.metrics.service_km) + " км"],
      ["Переезд и возврат", p => format.format(p.metrics.deadhead_km + p.metrics.return_km) + " км"],
      ["Переменные затраты¹", p => format.format(p.metrics.C_var_rub) + " ₽"],
      ["Машины в плане", p => p.metrics.vehicles_used],
    ];
    for (const [label, value] of columns) {
      const row = comparison.insertRow();
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.textContent = label;
      row.append(heading);
      for (const id of ["B0", "O1"]) {
        const cell = row.insertCell();
        cell.textContent = value(bundle.methods[id]);
        cell.classList.toggle("chosen", method === id);
      }
    }
    const plan = current();
    get("demo-summary").textContent = (plan.status === "ok" ? method + " покрывает все проходы. " :
      method + " покрывает часть проходов. ") + "¹ По сценарному тарифу.";
    get("demo-unserved").hidden = !missed.length;
    get("demo-unserved-count").textContent = "Участки с пропусками · " + missed.length;
    const passes = new Map(plan.validation.violations.map(item => [item.task_id, item.missing_passes]));
    get("demo-unserved-list").replaceChildren(...missed.map(task => {
      const item = document.createElement("li");
      item.textContent = (task.name || "Проезд") + " · OSM " + task.way + " · пропущено " + (passes.get(task.id) ?? task.passes);
      return item;
    }));
    updateControls();
  }

  function selectMethod(value) {
    method = value;
    get("demo-b0").setAttribute("aria-pressed", String(value === "B0"));
    get("demo-o1").setAttribute("aria-pressed", String(value === "O1"));
    if (!bundle) return;
    routes = current().routes.filter(route => route.steps.length).map(route => {
      const steps = route.steps.map(([start, end, mode, from, to]) => ({
        start, end, mode, a: points[from], b: points[to],
      }));
      return { vehicle: route.vehicle, steps, service: steps.filter(step => step.mode === "s") };
    });
    missed = current().validation.unserved_tasks.map(task => tasks.get(task));
    horizon = Math.max(0, ...routes.map(route => route.steps.at(-1).end));
    describe();
    draw();
  }

  function reserveFleet() {
    const depots = [...new Set(bundle.fleet.map(vehicle => vehicle.depot_node))].filter(node => roadPoints[node]);
    const fallback = eligible.map(task => task.from);
    const starts = [...new Set([...depots, ...fallback])].slice(0, 2);
    return starts.map((node, i) => ({ id: "РЗ-0" + (i + 1), node, previous: null, steps: [], event: null }));
  }

  function rewind(redraw = true) {
    pause();
    time = 0;
    nextEvent = 40 + Math.random() * 25;
    incidents = [];
    reserves = bundle ? reserveFleet() : [];
    renderEvents();
    if (redraw) draw();
  }

  function load(entry) {
    bundle = entry?.data;
    live = entry?.live === true;
    if (!bundle) {
      points = routes = missed = tasks = eligible = null;
      rewind();
      get("demo-comparison").replaceChildren();
      get("demo-summary").textContent = "";
      get("demo-unserved").hidden = true;
      setNotice(window.sharedWorkspace ? "Для городского сценария запустите расчёт." : "Для городского сценария запустите сервер расчёта.");
      return;
    }
    points = Object.fromEntries(Object.entries(bundle.nodes).map(([id, coords]) => [id, project(coords)]));
    tasks = new Map(bundle.tasks.map(task => [task.id, task]));
    eligible = bundle.tasks.filter(task => {
      const tags = city.ways[task.way]?.[1];
      return roadPoints[task.from] && roadPoints[task.to] && tags && canAssignRoad({ tags });
    });
    rewind(false);
    selectMethod(method);
    fit();
    setNotice((live ? "Расчёт ядра" : "Сохранённый расчёт ядра") + " · " + bundle.tasks.length +
      " направленных участков · машин в парке: " + bundle.fleet.length + " · OSM");
  }

  function updateControls() {
    const cannotPlay = !bundle || calculating || accepting;
    for (const id of ["demo-play", "demo-reset", "demo-b0", "demo-o1"]) get(id).disabled = cannotPlay;
    get("demo-play").textContent = running ? "Пауза" : time ? "Продолжить показ" : "Запустить показ";
    get("demo-clock").textContent = stamp(time);
    get("demo-event").disabled = cannotPlay || !eligible?.length;
    get("demo-case").disabled = accepting;
    get("demo-calculate").disabled = calculating || accepting || !window.sharedWorkspace;
    get("demo-accept").disabled = cannotPlay || !current()?.routes.some(route => route.steps.some(step => step[2] === "s"));
    get("demo-accept").textContent = current()?.status === "partial" ? "Принять частичный план" : "Принять в новую смену";
  }

  function renderEvents() {
    const available = reserves.filter(item => !item.event).length;
    get("demo-reserves").textContent = bundle ? "Свободный резерв · " + available + " из " + reserves.length : "";
    get("demo-events").replaceChildren(...incidents.slice(-10).reverse().map(event => {
      const row = document.createElement("li");
      const title = document.createElement("div");
      const kind = document.createElement("strong");
      kind.textContent = event.kind;
      const where = document.createElement("small");
      where.textContent = (event.task.name || "Проезд OSM " + event.task.way) + " · " + stamp(event.at);
      title.append(kind, where);
      const status = document.createElement("span");
      status.textContent = {
        queued: event.waiting ? "Ожидает машину" : "В очереди",
        driving: event.vehicle + " · направлена",
        working: event.vehicle + " · в работе",
        done: event.vehicle + " · выполнено",
        unreachable: "Маршрут недоступен",
      }[event.status];
      row.append(title, status);
      return row;
    }));
    updateControls();
  }

  function respond() {
    for (const event of incidents) {
      if (event.status !== "queued") continue;
      const idle = reserves.filter(reserve => !reserve.event);
      if (!idle.length) { event.waiting = true; continue; }
      links ??= createRoadLinks(city, project);
      let best;
      for (const reserve of idle) {
        const edges = links.planSegment({ node: reserve.node, previous: reserve.previous }, event.task);
        if (!edges) continue;
        const seconds = edges.reduce((sum, edge) => sum + edge.length / (edge.work ? 3 : 6), 0);
        if (!best || seconds < best.seconds) best = { reserve, edges, seconds };
      }
      if (!best) {
        if (idle.length === reserves.length) event.status = "unreachable";
        continue;
      }
      const { reserve, edges } = best;
      let cursor = time;
      reserve.steps = edges.map(edge => {
        const start = cursor;
        cursor += edge.length / (edge.work ? 3 : 6);
        return { start, end: cursor, mode: edge.work ? "s" : "d", a: roadPoints[edge.from], b: roadPoints[edge.to] };
      });
      reserve.destination = edges.at(-1);
      reserve.event = event;
      event.vehicle = reserve.id;
      event.status = reserve.steps[0].mode === "s" ? "working" : "driving";
    }
    renderEvents();
  }

  function createEvent() {
    const taken = new Set(incidents.filter(event => ["queued", "working", "driving"].includes(event.status)).map(event => event.task.id));
    const options = eligible.filter(task => !taken.has(task.id));
    if (!options.length) return;
    const task = options[Math.floor(Math.random() * options.length)];
    const kinds = ["Снежный занос", "Повторная уборка", "Срочный проезд"];
    incidents.push({ task, kind: kinds[Math.floor(Math.random() * kinds.length)], at: time, status: "queued" });
    const retained = new Set(incidents.slice(-10));
    incidents = incidents.filter(event => retained.has(event) || ["queued", "driving", "working"].includes(event.status));
    respond();
    draw();
  }

  function pause() {
    running = false;
    lastFrame = null;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    updateControls();
  }

  function play() {
    if (!bundle || running) return;
    running = true;
    frame = requestAnimationFrame(tick);
    updateControls();
  }

  function tick(now) {
    if (!running) return;
    if (lastFrame !== null) time += Math.min(.12, (now - lastFrame) / 1000) * Number(get("demo-speed").value);
    lastFrame = now;
    let changed = false;
    for (const reserve of reserves) {
      if (!reserve.event) continue;
      if (time >= reserve.steps.at(-1).end) {
        reserve.event.status = "done";
        reserve.previous = reserve.destination;
        reserve.node = reserve.destination.to;
        reserve.steps = [];
        reserve.event = null;
        changed = true;
      } else if (reserve.event.status === "driving" && time >= reserve.steps.at(-1).start) {
        reserve.event.status = "working";
        changed = true;
      }
    }
    if (changed) respond();
    if (get("demo-auto").checked && eligible.length && time >= nextEvent) {
      createEvent();
      nextEvent = time + 65 + Math.random() * 45;
    }
    if (!get("demo-auto").checked && time >= horizon && !reserves.some(reserve => reserve.event)) pause();
    get("demo-clock").textContent = stamp(time);
    draw();
    if (running) frame = requestAnimationFrame(tick);
  }

  get("show-dispatch").addEventListener("click", () => open(false));
  get("show-demo").addEventListener("click", () => open(true));
  get("demo-b0").addEventListener("click", () => selectMethod("B0"));
  get("demo-o1").addEventListener("click", () => selectMethod("O1"));
  get("demo-play").addEventListener("click", () => running ? pause() : play());
  get("demo-reset").addEventListener("click", rewind);
  get("demo-event").addEventListener("click", () => { createEvent(); play(); });
  get("demo-auto").addEventListener("change", () => { nextEvent = time + 40 + Math.random() * 25; });
  get("demo-case").addEventListener("change", event => {
    request++;
    calculating = false;
    load(source.get(event.target.value));
    updateControls();
  });
  get("demo-calculate").addEventListener("click", async () => {
    const caseID = get("demo-case").value;
    const calculation = ++request;
    pause();
    calculating = true;
    updateControls();
    setNotice("Ядро рассчитывает B0 и O1 и проверяет маршруты…");
    try {
      const response = await fetch("/api/plans?" + new URLSearchParams({ case: caseID, seed: "42" }));
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      if (calculation !== request) return;
      source.set(caseID, { data, live: true });
      load(source.get(caseID));
    } catch (error) {
      if (calculation === request) setNotice(error.message);
    } finally {
      if (calculation === request) {
        calculating = false;
        updateControls();
      }
    }
  });
  get("demo-accept").addEventListener("click", async () => {
    if (accepting || !current()) return;
    pause();
    accepting = true;
    updateControls();
    try {
      await window.acceptSnowOpsPlan(bundle, method);
      open(false);
    } catch (error) {
      setNotice(error.message);
    } finally {
      accepting = false;
      updateControls();
    }
  });
  function changeZoom(multiplier) {
    zoom = Math.min(15, Math.max(.015, zoom * multiplier));
    cameraDirty = true;
    draw();
  }
  get("demo-zoom-in").addEventListener("click", () => changeZoom(1.35));
  get("demo-zoom-out").addEventListener("click", () => changeZoom(1 / 1.35));
  get("demo-fit").addEventListener("click", fit);
  map.addEventListener("wheel", event => {
    event.preventDefault();
    changeZoom(event.deltaY < 0 ? 1.18 : 1 / 1.18);
  }, { passive: false });
  map.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    drag = [event.clientX, event.clientY, ...center];
    map.setPointerCapture(event.pointerId);
  });
  map.addEventListener("pointermove", event => {
    if (!drag) return;
    center = [drag[2] - (event.clientX - drag[0]) / zoom, drag[3] - (event.clientY - drag[1]) / zoom];
    cameraDirty = true;
    draw();
  });
  map.addEventListener("lostpointercapture", () => { drag = null; });
  new ResizeObserver(() => draw()).observe(map);
  load(source.get(get("demo-case").value));
})();
