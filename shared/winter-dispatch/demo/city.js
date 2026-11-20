"use strict";

(() => {
  const city = window.petropavlovsk;
  const canvas = el("city-map");
  const foot = new Set(["footway", "steps", "pedestrian", "path", "cycleway"]);
  const main = new Set(["motorway", "trunk", "primary", "secondary", "tertiary", "primary_link", "secondary_link"]);
  const roadTypes = {
    primary: "основная магистраль", secondary: "городская магистраль", tertiary: "улица районного значения",
    residential: "жилая улица", service: "местный проезд", unclassified: "местная дорога",
    living_street: "жилая зона", footway: "пешеходная дорожка", steps: "лестница",
    path: "тропа", pedestrian: "пешеходная улица", track: "грунтовый проезд", cycleway: "велодорожка",
  };
  const surfaces = { asphalt: "асфальт", concrete: "бетон", gravel: "гравий", ground: "грунт", paved: "твёрдое покрытие", unpaved: "без твёрдого покрытия", paving_stones: "плитка" };
  const project = ([lon, lat]) => [(lon - 158.62) * 66900, (53.05 - lat) * 111200];
  const coordinates = Object.fromEntries(Object.entries(city.nodes).map(([id, coord]) => [id, project(coord)]));

  function bounds(points) {
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const [x, y] of points) {
      left = Math.min(left, x); top = Math.min(top, y);
      right = Math.max(right, x); bottom = Math.max(bottom, y);
    }
    return [left, top, right, bottom];
  }

  const coverage = city.bounds.map(([west, south, east, north]) => bounds([project([west, south]), project([east, north])]));
  const list = Object.entries(city.ways).map(([id, [refs, tags]]) => {
    const points = refs.map(ref => coordinates[ref]);
    return { id, refs, points, box: bounds(points), tags, name: tags["name:ru"] ?? tags.name ?? "" };
  });
  const roadsByNode = new Map();
  const streetSelection = new Map();
  for (const road of list) {
    road.streetKey = road.name.trim().toLocaleLowerCase("ru-RU");
    if (!canAssignRoad(road)) continue;
    for (const node of new Set(road.refs)) {
      if (!roadsByNode.has(node)) roadsByNode.set(node, []);
      roadsByNode.get(node).push(road);
    }
  }
  const landmarks = Object.entries(city.land).map(([id, [kind, refs, name]]) => {
    const points = refs.map(ref => coordinates[ref]);
    return { id, kind, points, box: bounds(points), name };
  });
  const pois = Object.entries(city.points).map(([id, tags]) => ({ point: coordinates[id], tags }));
  const names = [...new Set(list.map(road => road.name).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
  const streets = document.createDocumentFragment();
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    streets.append(option);
  }
  el("street-suggestions").append(streets);
  let position = null;
  let scale = 1;
  let selected = null;
  let selectedName = "";
  let showPed = false;
  let showPoi = true;
  let pointer = null;
  let scheduled = false;
  let shift;
  let pickingAssignment = false;
  const pickedRoads = [];
  let hoveredRoad = null;

  function fit(box, maxScale = Infinity) {
    const [left, top, right, bottom] = box;
    position = [(left + right) / 2, (top + bottom) / 2];
    scale = Math.max(.035, Math.min(maxScale, canvas.clientWidth * .88 / Math.max(right - left, 500),
      canvas.clientHeight * .88 / Math.max(bottom - top, 500)));
    queueDraw();
  }

  function queueDraw() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; draw(); drawFleet(); });
  }

  function pixel(x, y) {
    return [(x - position[0]) * scale + canvas.clientWidth / 2, (y - position[1]) * scale + canvas.clientHeight / 2];
  }

  function world(x, y) {
    return [(x - canvas.clientWidth / 2) / scale + position[0], (y - canvas.clientHeight / 2) / scale + position[1]];
  }

  function draw() {
    if (!position) return;
    const { ctx, width, height } = setupCanvas("city-map");
    const [left, top] = world(-24, -24);
    const [right, bottom] = world(width + 24, height + 24);
    const visible = ([x1, y1, x2, y2]) => x1 <= right && x2 >= left && y1 <= bottom && y2 >= top;
    function path(points) {
      ctx.beginPath();
      points.forEach(([x, y], index) => {
        const [px, py] = pixel(x, y);
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
    }
    function line(points, color, width) {
      path(points);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.stroke();
    }
    function text(label, x, y, color, weight = 600) {
      ctx.font = weight + " 12px system-ui";
      ctx.lineJoin = "round";
      ctx.lineWidth = 4;
      ctx.strokeStyle = "#172630";
      ctx.strokeText(label, x, y);
      ctx.fillStyle = color;
      ctx.fillText(label, x, y);
    }
    ctx.fillStyle = "#101c27";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#182832";
    for (const [x1, y1, x2, y2] of coverage) {
      const [x, y] = pixel(x1, y1);
      ctx.fillRect(x, y, (x2 - x1) * scale, (y2 - y1) * scale);
    }

    const landColors = { wood: "#1d3837", park: "#24443c", grassland: "#233a3a", scrub: "#233b39", water: "#153948", building: "#31424d" };
    for (const item of landmarks) {
      if (!visible(item.box)) continue;
      if (item.kind === "coastline") {
        line(item.points, "#407081", 1.4);
      } else if (item.kind !== "building" || scale > .155) {
        path(item.points);
        ctx.fillStyle = landColors[item.kind];
        ctx.fill();
      }
    }

    ctx.lineCap = "round";
    const visibleRoads = list.filter(road => visible(road.box));
    for (const road of visibleRoads) {
      const type = road.tags.highway;
      const pedestrian = foot.has(type);
      if (main.has(type) || (pedestrian && !showPed) || (type === "service" && scale < .09)) continue;
      line(road.points, pedestrian ? "#78b59d" : "#72909e", pedestrian ? 1 : type === "service" ? .8 : 1.4);
    }
    for (const road of visibleRoads) {
      if (main.has(road.tags.highway)) line(road.points, "#e2bb82", scale > .14 ? 2.6 : 1.8);
    }
    if (selected) {
      const selectionColor = canAssignRoad(selected) ? "#b9f472" : "#ffbf82";
      for (const road of visibleRoads) {
        if (road.id === selected.id || (selectedName && selectedName === road.name)) {
          line(road.points, "#163128", 8);
          line(road.points, selectionColor, road.id === selected.id ? 4 : 2.5);
        }
      }
    }
    if (showPoi) {
      for (const { point, tags } of pois) {
        const [x, y] = pixel(...point);
        if (x < -8 || x > width + 8 || y < -8 || y > height + 8) continue;
        const bus = tags.highway === "bus_stop";
        if (!bus && !tags.amenity) continue;
        ctx.fillStyle = bus ? "#70d9ef" : "#b9f472";
        ctx.fillRect(x - 2, y - 2, 4, 4);
      }
    }
    const used = new Set();
    const labelCells = new Set();
    for (const road of visibleRoads) {
      if (!road.name || used.has(road.name) || !main.has(road.tags.highway)) continue;
      const [x,y] = pixel(...road.points[Math.floor(road.points.length / 2)]);
      const cell = Math.floor(x / 155) + ":" + Math.floor(y / 46);
      if (x < 30 || x > width - 130 || y < 35 || y > height - 25 || labelCells.has(cell)) continue;
      used.add(road.name);
      labelCells.add(cell);
      text(road.name,x + 5,y - 6,"#e9d5bb");
    }
    for (const item of landmarks) {
      if (item.kind === "water" && item.name && visible(item.box)) {
        const [x1,y1,x2,y2] = item.box;
        const [x,y] = pixel((x1+x2)/2,(y1+y2)/2);
        text(item.name,x-22,y,"#9ac4d7");
      }
    }
    const lengthMeters = scale > .45 ? 100 : scale > .18 ? 250 : scale > .07 ? 500 : 1000;
    line([world(17,height-20),world(17 + lengthMeters*scale,height-20)],"#c2d4de",2);
    text(lengthMeters >= 1000 ? "1 км" : lengthMeters + " м",17,height-27,"#c2d4de");
  }

  function routePosition(vehicle) {
    const { points, cumulative, seen } = vehicle;
    let index = cumulative.findIndex((distance, i) => i > 0 && distance >= seen);
    if (index < 1) index = points.length - 1;
    const a = points[index - 1];
    const b = points[index];
    const length = cumulative[index] - cumulative[index - 1];
    const ratio = length ? (seen - cumulative[index - 1]) / length : 0;
    return {
      point: [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio],
      angle: Math.atan2(b[1] - a[1], b[0] - a[0]),
      index,
    };
  }

  function pickVehicle(x, y) {
    const nearest = [...shift.fleet].reverse().find(vehicle => {
      const [px, py] = pixel(...routePosition(vehicle).point);
      return Math.hypot(x - px, y - py) <= 18;
    });
    if (!nearest) return false;
    shift.select(nearest, false);
    return true;
  }

  function drawFleet() {
    if (!shift || !position) return;
    const { ctx, width, height } = setupCanvas("fleet-map");
    ctx.clearRect(0, 0, width, height);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    function trace(points, color, lineWidth, dashed = false) {
      ctx.beginPath();
      points.forEach((point, i) => {
        const [x, y] = pixel(...point);
        if (i) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
      });
      ctx.lineWidth = lineWidth;
      ctx.strokeStyle = color;
      ctx.setLineDash(dashed ? [6,5] : []);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const snapshots = shift.fleet.map(vehicle => ({ vehicle, ...routePosition(vehicle) }));
    const routes = !shift.selected || shift.fleet.includes(shift.selected) ? snapshots :
      [...snapshots, { vehicle: shift.selected, ...routePosition(shift.selected) }];
    const remainingTransfers = [];
    for (const { vehicle, point, index } of routes) {
      if (vehicle.state === "canceled" || vehicle.state === "transferred") continue;
      const current = vehicle === shift.selected;
      let start = 0;
      while (start < vehicle.edges.length) {
        const work = vehicle.edges[start].work;
        let end = start + 1;
        while (end < vehicle.edges.length && vehicle.edges[end].work === work) end++;
        const line = vehicle.points.slice(start, end + 1);
        if (work) {
          trace(line, "#142c36", current ? 8 : 5.5);
          trace(line, current ? "#70d9ef" : "#518596", current ? 4.5 : 2.75);
        } else if (vehicle.seen < vehicle.cumulative[end]) {
          const remaining = vehicle.seen > vehicle.cumulative[start] ?
            [point, ...vehicle.points.slice(index, end + 1)] : line;
          remainingTransfers.push({ line: remaining, width: current ? 4 : 2.5 });
        }
        start = end;
      }
    }
    for (const line of shift.coverage.lines) trace(line, "#b9f472", 4.5);
    for (const { line, width } of remainingTransfers) trace(line, "#b2a89a", width, true);
    if (pickingAssignment) {
      if (hoveredRoad) {
        if (canAssignRoad(hoveredRoad)) {
          for (const road of roadsToPick(hoveredRoad)) {
            if (!pickedRoads.includes(road)) trace(road.points, "#e6eaf0", 4);
          }
        } else {
          trace(hoveredRoad.points, "#ffbf82", 4, true);
        }
      }
      for (const [i, road] of pickedRoads.entries()) {
        trace(road.points, "#152630", 8);
        trace(road.points, "#f4ce8b", 4.5);
        const [x,y] = pixel(...road.points[Math.floor(road.points.length / 2)]);
        ctx.fillStyle = "#f4ce8b";
        ctx.fillRect(x-9,y-9,18,18);
        ctx.font = "700 11px system-ui";
        ctx.textAlign = "center";
        ctx.fillStyle = "#182832";
        ctx.fillText(i + 1,x,y+4);
      }
    }
    if (showPoi && shift.selected?.landmarkPoint) {
      const [lx, ly] = pixel(...shift.selected.landmarkPoint);
      if (lx >= 0 && lx < width && ly >= 0 && ly < height) {
        ctx.strokeStyle = "#d4f5ff";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(lx - 5, ly - 5, 10, 10);
      }
    }

    for (const { vehicle, point, angle } of snapshots) {
      const [x, y] = pixel(...point);
      const retired = vehicle.state === "canceled" || vehicle.state === "transferred";
      const color = retired ? "#a5bac5" : vehicle.blocked ? "#ff8c80" : vehicle.seen >= vehicle.length ? "#b9f472" : "#80e6f8";
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(angle);
      ctx.fillStyle = "#132531";
      ctx.strokeStyle = color;
      ctx.lineWidth = vehicle === shift.selected ? 2.3 : 1.4;
      ctx.fillRect(-10, -6, 19, 12);
      ctx.strokeRect(-10, -6, 19, 12);
      ctx.fillStyle = color;
      ctx.fillRect(3, -4, 4, 8);
      ctx.beginPath();
      ctx.moveTo(11, -6);
      ctx.lineTo(14, 0);
      ctx.lineTo(11, 6);
      ctx.stroke();
      ctx.restore();
      ctx.font = "700 11px system-ui";
      ctx.textAlign = "center";
      ctx.strokeStyle = "#152630";
      ctx.lineWidth = 4;
      ctx.strokeText(vehicle.vehicleId, x, y - 13);
      ctx.fillStyle = color;
      ctx.fillText(vehicle.vehicleId, x, y - 13);
    }
    for (const vehicle of shift.unassignedVehicles) {
      const [x,y] = pixel(...vehicle.point);
      ctx.fillStyle = "#142630";
      ctx.strokeStyle = "#a5bac5";
      ctx.lineWidth = 1.5;
      ctx.fillRect(x-9,y-5,18,10);
      ctx.strokeRect(x-9,y-5,18,10);
      ctx.font = "700 11px system-ui";
      ctx.textAlign = "center";
      ctx.lineWidth = 4;
      ctx.strokeStyle = "#182832";
      ctx.strokeText(vehicle.id + " · резерв",x,y-11);
      ctx.fillStyle = "#a5bac5";
      ctx.fillText(vehicle.id + " · резерв",x,y-11);
    }
  }

  function showRoad(road, streetName = "") {
    selected = road;
    selectedName = streetName;
    const tags = road.tags;
    const direction = { yes: "одностороннее по линии OSM", "-1": "одностороннее против линии OSM", no: "двустороннее" };
    el("segment-card").hidden = false;
    const blocked = roadAssignmentBlock(road);
    el("segment-assignment").hidden = Boolean(blocked);
    el("segment-availability").hidden = !blocked;
    el("segment-availability").textContent = blocked;
    updateRoadAction();
    el("segment-name").textContent = road.name || "Участок без названия";
    const detail = fieldList([
      ["Класс", roadTypes[tags.highway] ?? tags.highway],
      ["Направление", direction[tags.oneway] ?? tags.oneway],
      ["Покрытие", surfaces[tags.surface] ?? tags.surface],
      ["Ширина, м", tags.width],
      ["Уклон", tags.incline],
    ]);
    const source = document.createElement("a");
    source.href = "https://www.openstreetmap.org/way/" + road.id;
    source.textContent = "Участок OSM #" + road.id;
    source.target = "_blank";
    source.rel = "noreferrer";
    el("segment-details").replaceChildren(detail,source);
    queueDraw();
  }

  function distance(point, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const length = dx*dx + dy*dy;
    const t = length ? Math.max(0, Math.min(1, ((point[0]-a[0])*dx + (point[1]-a[1])*dy)/length)) : 0;
    return Math.hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dy);
  }

  function roadAt(x, y) {
    const point = world(x, y);
    let nearest = null;
    let limit = 12 / scale;
    for (const road of list) {
      const [x1,y1,x2,y2] = road.box;
      if (point[0] < x1-limit || point[0] > x2+limit || point[1] < y1-limit || point[1] > y2+limit) continue;
      if ((!showPed && foot.has(road.tags.highway)) || (road.tags.highway === "service" && scale < .09)) continue;
      for (let i = 1; i < road.points.length; i++) {
        const dist = distance(point,road.points[i-1],road.points[i]);
        if (dist >= limit) continue;
        nearest = road;
        limit = dist;
      }
    }
    return nearest;
  }

  function setPicking(active) {
    if (active && !shift.editable) return;
    pickingAssignment = active;
    el("pick-assignment").setAttribute("aria-pressed", active);
    el("pick-assignment").textContent = active ? "Отменить выбор" : "Создать задание";
    canvas.classList.toggle("picking-road", active);
    el("assignment-selection").hidden = !active;
    if (!active) {
      hoveredRoad = null;
      pickedRoads.length = 0;
    }
    if (selected) updateRoadAction();
    renderRoadSelection();
  }

  function selectRoad(road, streetName = "") {
    if (!streetName) el("segment-options").hidden = true;
    showRoad(road, streetName);
    if (pickingAssignment && !streetName) {
      const blocked = roadAssignmentBlock(road);
      if (blocked) el("assignment-hint").textContent = blocked;
      else toggleRoadSelection(road);
    }
  }

  function roadsToPick(road) {
    if (el("selection-mode").value !== "street") return [road];
    if (streetSelection.has(road)) return streetSelection.get(road);
    function neighbors(item) {
      if (item.streetKey) {
        return [...new Set(item.refs.flatMap(node => roadsByNode.get(node)))].filter(other =>
          other !== item && other.streetKey === item.streetKey);
      }
      return [...new Set([item.refs[0], item.refs.at(-1)].flatMap(node => {
        const meeting = roadsByNode.get(node) ?? [];
        if (meeting.length !== 2) return [];
        return meeting.filter(other => other !== item && !other.streetKey &&
          (other.refs[0] === node || other.refs.at(-1) === node) &&
          other.tags.highway === item.tags.highway && other.tags.ref === item.tags.ref);
      }))];
    }
    const links = new Map();
    const component = [road];
    for (const item of component) {
      const connected = neighbors(item);
      links.set(item, connected);
      for (const next of connected) {
        if (!component.includes(next)) component.push(next);
      }
    }
    const ordered = [];
    const visited = new Set();
    const pending = [component.find(item => links.get(item).length <= 1) ?? road];
    while (pending.length) {
      const current = pending.pop();
      if (visited.has(current)) continue;
      visited.add(current);
      ordered.push(current);
      pending.push(...links.get(current));
    }
    const reversed = [...ordered].reverse();
    const directionOrder = streetContinuity(reversed) > streetContinuity(ordered) ? reversed : ordered;
    for (const item of component) streetSelection.set(item, directionOrder);
    return directionOrder;
  }

  function streetContinuity(roads) {
    function directions(road) {
      const first = road.refs[0];
      const last = road.refs.at(-1);
      if (road.tags.oneway === "-1") return [[last, first]];
      if (["yes", "1", "true"].includes(road.tags.oneway) ||
          (road.tags.oneway === undefined && ["roundabout", "circular"].includes(road.tags.junction))) {
        return [[first, last]];
      }
      return [[first, last], [last, first]];
    }
    let positions = new Map();
    for (const road of roads) {
      const fallback = positions.size ? Math.max(...positions.values()) : 0;
      const next = new Map();
      for (const [start, end] of directions(road)) {
        const score = Math.max(fallback, (positions.get(start) ?? -1) + 1);
        next.set(end, Math.max(next.get(end) ?? 0, score));
      }
      positions = next;
    }
    return positions.size ? Math.max(...positions.values()) : 0;
  }

  function updateRoadAction() {
    const street = canAssignRoad(selected) && roadsToPick(selected).length > 1;
    el("segment-assignment").textContent = pickingAssignment ?
      (street ? "Добавить или убрать улицу" : "Добавить или убрать участок") : "Создать задание здесь";
  }

  function toggleRoadSelection(road) {
    const group = roadsToPick(road);
    if (group.every(item => pickedRoads.includes(item))) {
      for (const item of group) pickedRoads.splice(pickedRoads.indexOf(item), 1);
    } else {
      pickedRoads.push(...group.filter(item => !pickedRoads.includes(item)));
    }
    renderRoadSelection();
  }

  function renderRoadSelection() {
    el("assignment-hint").textContent = pickedRoads.length ? "Выбрано участков: " + pickedRoads.length + "." :
      el("selection-mode").value === "street" ? "Клик добавляет связную улицу. На развилках без названий выбирайте участки отдельно." :
        "Клик добавляет отрезок. Повторный клик снимает выбор.";
    const entries = pickedRoads.map((road, i) => {
      const item = document.createElement("li");
      const label = road.name || "Участок без названия";
      const remove = document.createElement("button");
      remove.textContent = "Убрать";
      remove.setAttribute("aria-label", "Убрать участок " + (i + 1) + " · " + label);
      remove.addEventListener("click", () => {
        pickedRoads.splice(pickedRoads.indexOf(road), 1);
        renderRoadSelection();
      });
      item.append(label + " · OSM " + road.id,remove);
      return item;
    });
    el("picked-roads").replaceChildren(...entries);
    el("assignment-next").disabled = !pickedRoads.length;
    el("assignment-next").textContent = pickedRoads.length ? "Далее · " + pickedRoads.length : "Далее";
    queueDraw();
  }

  function zoom(factor, x = canvas.clientWidth / 2, y = canvas.clientHeight / 2) {
    const before = world(x,y);
    scale = Math.max(.035,Math.min(3,scale*factor));
    const after = world(x,y);
    position[0] += before[0] - after[0];
    position[1] += before[1] - after[1];
    queueDraw();
  }

  canvas.tabIndex = 0;
  canvas.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
    pointer = { x: event.offsetX, y: event.offsetY, original: [...position], moved: false };
  });
  canvas.addEventListener("pointermove", event => {
    if (!pointer) {
      if (pickingAssignment) {
        const road = roadAt(event.offsetX,event.offsetY);
        if (road !== hoveredRoad) {
          hoveredRoad = road;
          drawFleet();
        }
      }
      return;
    }
    const dx = event.offsetX - pointer.x, dy = event.offsetY - pointer.y;
    if (Math.hypot(dx,dy) > 4) pointer.moved = true;
    if (pointer.moved) {
      position = [pointer.original[0] - dx/scale, pointer.original[1] - dy/scale];
      queueDraw();
    }
  });
  canvas.addEventListener("pointerup", event => {
    if (pointer && !pointer.moved && (pickingAssignment || !pickVehicle(event.offsetX, event.offsetY))) {
      const road = roadAt(event.offsetX,event.offsetY);
      if (road) selectRoad(road);
      else if (pickingAssignment) el("assignment-hint").textContent = "Линия не найдена рядом с кликом. Приблизьте карту и нажмите на проезд.";
    }
    pointer = null;
  });
  canvas.addEventListener("pointerleave", () => {
    if (!hoveredRoad) return;
    hoveredRoad = null;
    drawFleet();
  });
  canvas.addEventListener("pointercancel", () => { pointer = null; });
  canvas.addEventListener("wheel", event => {
    event.preventDefault();
    zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY))*.005),event.offsetX,event.offsetY);
  }, { passive: false });
  canvas.addEventListener("keydown", event => {
    const moves = { ArrowUp: [0,-60], ArrowDown: [0,60], ArrowLeft: [-60,0], ArrowRight: [60,0] };
    if (!moves[event.key]) return;
    event.preventDefault();
    position[0] += moves[event.key][0] / scale;
    position[1] += moves[event.key][1] / scale;
    queueDraw();
  });
  el("zoom-in").addEventListener("click", () => zoom(1.45));
  el("zoom-out").addEventListener("click", () => zoom(1/1.45));
  el("city-overview").addEventListener("click", fitShift);
  el("pick-assignment").addEventListener("click", () => setPicking(!pickingAssignment));
  el("selection-mode").addEventListener("change", () => {
    if (selected) updateRoadAction();
    renderRoadSelection();
  });
  el("segment-assignment").addEventListener("click", () => {
    if (!pickingAssignment) setPicking(true);
    toggleRoadSelection(selected);
  });
  el("segment-way").addEventListener("change", event => {
    const road = list.find(item => item.id === event.target.value);
    if (road) {
      showRoad(road);
      fit(road.box, .65);
    }
  });
  el("assignment-next").addEventListener("click", () => shift.openAssignment(pickedRoads));
  window.addEventListener("assignment-created", () => setPicking(false));
  window.addEventListener("shift-changed", () => {
    selected = null;
    el("segment-card").hidden = true;
    setPicking(false);
    fitShift();
  });
  el("ped-layer").addEventListener("click", () => {
    showPed = !showPed;
    el("ped-layer").setAttribute("aria-pressed",showPed);
    queueDraw();
  });
  el("poi-layer").addEventListener("click", () => {
    showPoi = !showPoi;
    el("poi-layer").setAttribute("aria-pressed",showPoi);
    queueDraw();
  });
  el("street-search").addEventListener("submit", event => {
    event.preventDefault();
    const query = el("street-name").value.trim().toLocaleLowerCase("ru");
    if (!query) return;
    const exact = names.find(name => name.toLocaleLowerCase("ru") === query);
    const match = exact ?? names.find(name => name.toLocaleLowerCase("ru").includes(query));
    if (!match) {
      el("search-status").textContent = "В этой выгрузке улица не найдена.";
      return;
    }
    const candidates = list.filter(road => road.name === match);
    const representative = candidates.find(road => main.has(road.tags.highway)) ?? candidates[0];
    const area = bounds(candidates.flatMap(road => road.points));
    if (foot.has(representative.tags.highway) && !showPed) {
      showPed = true;
      el("ped-layer").setAttribute("aria-pressed", "true");
    }
    const chooser = el("segment-way");
    chooser.replaceChildren(...candidates.map((road, i) => {
      const length = road.points.slice(1).reduce((sum, point, j) =>
        sum + Math.hypot(point[0] - road.points[j][0], point[1] - road.points[j][1]), 0);
      return new Option((i + 1) + " · " + Math.round(length) + " м · OSM " + road.id, road.id);
    }));
    chooser.value = representative.id;
    el("segment-options").hidden = candidates.length < 2;
    selectRoad(representative, match);
    fit(area, .7);
    el("street-name").value = match;
    el("search-status").textContent = "Найдено участков: " + candidates.length + ".";
  });

  function fitShift() {
    const paths = shift.fleet.flatMap(vehicle => vehicle.landmarkPoint ? [...vehicle.points, vehicle.landmarkPoint] : vehicle.points);
    const positions = [...paths, ...shift.unassignedVehicles.map(vehicle => vehicle.point)];
    fit(positions.length ? bounds(positions) : coverage[0], .5);
  }

  window.addEventListener("resize", queueDraw);
  shift = createShift(city, project, drawFleet, points => fit(bounds(points), .55));
  window.acceptSnowOpsPlan = (bundle, method) => shift.acceptPlan(bundle, method);
  fitShift();
  shift.render();
})();
