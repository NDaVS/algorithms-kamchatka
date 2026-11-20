"use strict";

const { fixture, runs } = window.shiftData;
const el = id => document.getElementById(id);
const colors = { green: "#b9f472", blue: "#70d9ef", closed: "#ff8c80", road: "#6a8796" };
const names = {
  "base-access": "Проезд от базы", "clinic-access": "Подъезд к больнице",
  "clinic-exit": "Выезд от больницы", "narrow-descent": "Узкий спуск",
  "plow-1": "Плужная машина", "plow-2": "Компактный плуг",
};
const tags = { "plow-1": "01", "plow-2": "02" };
const nodes = { base: [.19, .71], junction: [.39, .57], upper: [.53, .25], clinic: [.75, .51] };
const bends = {
  "base-out": [[.27,.69],[.31,.61]], "base-in": [[.32,.65],[.25,.74]],
  direct: [[.49,.54],[.57,.57],[.65,.52]], "hill-up": [[.40,.41],[.46,.36]],
  "hill-down": [[.49,.37],[.44,.43]], "clinic-approach": [[.64,.30],[.70,.40]],
  "clinic-return": [[.66,.61],[.57,.64],[.48,.59]],
};
const arcs = Object.fromEntries(fixture.arcs.map(arc => [arc.id, arc]));
const roads = Object.fromEntries(fixture.arcs.map(arc => [arc.id, [nodes[arc.from], ...bends[arc.id], nodes[arc.to]]]));

let variant = "road-closed";
let time = 9;
let playing = true;
let showLayers = true;
let comparison = false;
let lastTextUpdate = "";
let previousFrame = 0;

const narratives = {
  normal: [],
  "road-closed": [],
  "vehicle-unavailable": [],
};

function row(label, status, tone = "") {
  const entry = document.createElement("li");
  const name = document.createElement("b");
  const badge = document.createElement("span");
  name.textContent = label;
  badge.textContent = status;
  badge.className = tone;
  entry.append(name, badge);
  return entry;
}

function setNarratives() {
  const normalTrip = runs.normal.assignments.find(job => job.job_id === "clinic-exit");
  const closedTrip = runs["road-closed"].assignments.find(job => job.job_id === "clinic-exit");
  const travel = trip => trip.approach_arcs.reduce((total, id) => total + arcs[id].travel_min, 0);
  const normalTime = travel(normalTrip);
  const closedTime = travel(closedTrip);
  const unavailable = runs["vehicle-unavailable"];
  narratives.normal = [
    "Сохранить штатный план", "ШТАТНО",
    "Критический подъезд назначен компактному плугу: машина уже находится у начала рабочего участка.",
    "Подъезд к больнице очищен к " + runs.normal.metrics.critical_completion_min["clinic-access"] + "-й минуте. Все четыре работы укладываются в сроки.",
    15,
  ];
  narratives["road-closed"] = [
    "Отправить технику в объезд", "ПЕРЕКРЫТИЕ",
    "Прямой проезд закрывается на 10-й минуте. Исходный маршрут больше недоступен. Плужная машина подъедет к заданию через верхнюю улицу.",
    "Подъезд " + normalTime + " → " + closedTime + " мин. Все работы по-прежнему укладываются в сроки.",
    10,
  ];
  narratives["vehicle-unavailable"] = [
    "Сохранить приоритет больницы", "ОТКАЗ",
    "Компактный плуг выбывает на 12-й минуте. Основная машина берёт подъезд к больнице. Для узкого спуска нужна другая техника.",
    "Подъезд очищен к " + unavailable.metrics.critical_completion_min["clinic-access"] + "-й минуте. Узкий спуск остаётся без назначения.",
    12,
  ];
}

function updateText(run) {
  const minute = Math.floor(time);
  const cacheKey = variant + ":" + minute + ":" + playing;
  el("time").value = time;
  el("clock").textContent = "00:" + String(minute).padStart(2, "0");
  if (minute === 60) el("clock").textContent = "01:00";
  if (lastTextUpdate === cacheKey) return;
  lastTextUpdate = cacheKey;

  const occurred = run.events.filter(event => event.at_min <= time);
  const complete = occurred.filter(event => event.kind === "job_completed");
  const lost = occurred.filter(event => event.kind === "vehicle_unavailable");
  const clinic = complete.find(event => event.job_id === "clinic-access");
  const spent = occurred.filter(event => event.kind === "deadhead_started")
    .reduce((total, event) => total + Math.min(time, event.end_min) - event.at_min, 0);
  el("fleet-count").textContent = (fixture.vehicles.length - lost.length) + " / " + fixture.vehicles.length;
  el("work-count").textContent = complete.length + " / " + fixture.jobs.length;
  el("work-caption").textContent = Math.round(100 * complete.length / fixture.jobs.length) + "% заданий смены";
  el("critical").textContent = clinic ? "Очищен" : "В плане";
  el("critical-caption").textContent = clinic ? "Работа завершена на " + clinic.at_min + "-й минуте" : "Срок по сценарию — 35-я минута";
  el("deadhead").textContent = Math.floor(spent) + " мин";
  el("play").textContent = playing ? "Пауза" : "Пуск";

  el("fleet-list").replaceChildren(...fixture.vehicles.map(vehicle => {
    const active = activeMovement(run, vehicle.id);
    const out = lost.some(event => event.vehicle_id === vehicle.id);
    const status = out ? "Недоступна" : active?.kind === "service_started" ? "Очищает" : active ? "В пути" : "Ожидает";
    const entry = row(names[vehicle.id], status, out ? "status-warn" : "");
    const tag = document.createElement("span");
    tag.className = "fleet-id" + (vehicle.id === "plow-2" ? " second" : "");
    tag.textContent = tags[vehicle.id];
    entry.firstChild.prepend(tag);
    return entry;
  }));

  el("queue-size").textContent = complete.length + " закрыто";
  el("jobs-list").replaceChildren(...[...fixture.jobs].sort((a, b) => a.priority - b.priority).map(job => {
    const inProgress = occurred.some(event => event.job_id === job.id && event.kind === "service_started" && time < event.end_min);
    const noMachine = job.id === "narrow-descent" && lost.length && time >= job.release_min;
    const done = complete.some(event => event.job_id === job.id);
    const status = done ? "Выполнено" : noMachine ? "Нужен компактный плуг" : inProgress ? "В работе" : time < job.release_min ? "По плану" : "Ожидает";
    return row(names[job.id], status, done ? "status-good" : noMachine ? "status-warn" : "");
  }));

  const informative = occurred.filter(event => ["job_completed", "arc_closed", "vehicle_unavailable", "assignment_released"].includes(event.kind)).slice(-4).reverse();
  el("event-list").replaceChildren(...(informative.length ? informative.map(event => row(
    event.kind === "arc_closed" ? "Прямой проезд перекрыт" : event.kind === "vehicle_unavailable" ?
      "Компактный плуг недоступен" : event.kind === "assignment_released" ?
        "Назначение возвращено в очередь" : names[event.job_id] + " · выполнено", event.at_min + " мин",
  )) : [row("Смена началась", "0 мин")]));
}

function activeMovement(run, vehicle) {
  return run.events.find(event => event.vehicle_id === vehicle && event.end_min &&
    event.at_min <= time && time < event.end_min);
}

function pointOn(points, progress) {
  const lengths = points.slice(1).map((point, index) => Math.hypot(point[0] - points[index][0], point[1] - points[index][1]));
  let distance = lengths.reduce((a, b) => a + b, 0) * progress;
  for (let i = 0; i < lengths.length; i++) {
    if (distance <= lengths[i]) {
      const t = distance / lengths[i];
      return [points[i][0] + (points[i+1][0] - points[i][0]) * t, points[i][1] + (points[i+1][1] - points[i][1]) * t];
    }
    distance -= lengths[i];
  }
  return points.at(-1);
}

function setupCanvas(id) {
  const canvas = el(id);
  const { width, height } = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function drawMap(run) {
  const { ctx, width: w, height: h } = setupCanvas("map");
  ctx.fillStyle = "#182c36";
  ctx.fillRect(0, 0, w, h);

  function line(points, color, stroke, dash = []) {
    ctx.beginPath();
    points.forEach(([x, y], index) => index ? ctx.lineTo(x*w, y*h) : ctx.moveTo(x*w, y*h));
    ctx.strokeStyle = color;
    ctx.lineWidth = stroke;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.setLineDash(dash);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.beginPath();
  ctx.moveTo(0,h*.94);
  ctx.bezierCurveTo(w*.23,h*.87,w*.34,h*.88,w*.49,h*.80);
  ctx.bezierCurveTo(w*.64,h*.73,w*.82,h*.90,w,h*.67);
  ctx.lineTo(w,h);
  ctx.lineTo(0,h);
  ctx.closePath();
  ctx.fillStyle = "#14242f";
  ctx.fill();
  ctx.strokeStyle = "#3b5562";
  ctx.lineWidth = 2;
  ctx.stroke();

  for (let offset = 0; offset < 9; offset++) {
    const contour = Array.from({length: 36}, (_, i) => {
      const x = i / 35;
      return [x, .06 + offset * .08 + Math.sin(x * 10 + offset * .65) * .045 + x * .045];
    });
    line(contour, offset % 3 === 0 ? "#2c4650" : "#243d48", 1);
  }

  const localStreets = [
    [[.06,.56],[.16,.51],[.27,.55],[.38,.43],[.48,.48],[.59,.39],[.64,.43],[.71,.29],[.88,.32],[.96,.24]],
    [[.09,.77],[.26,.80],[.36,.72],[.49,.71],[.56,.73],[.72,.68],[.82,.66],[.94,.54]],
    [[.05,.33],[.19,.38],[.28,.27],[.35,.30],[.45,.21],[.63,.18],[.73,.24],[.87,.14],[.97,.20]],
    [[.13,.34],[.15,.44],[.18,.50],[.15,.63],[.19,.71]], [[.25,.27],[.25,.41],[.27,.55],[.30,.66],[.28,.77]],
    [[.35,.30],[.38,.43],[.39,.57],[.36,.72]], [[.64,.43],[.65,.52],[.64,.61],[.72,.68]],
    [[.79,.22],[.81,.33],[.78,.43],[.75,.51],[.82,.66]], [[.88,.32],[.89,.47],[.85,.55],[.82,.66]],
    [[.42,.15],[.45,.21],[.47,.32],[.54,.40]], [[.05,.67],[.15,.63],[.24,.60],[.27,.55]],
  ];
  localStreets.forEach(street => line(street, "#3c5865", 1.5));

  const active = fixture.vehicles.map(vehicle => activeMovement(run, vehicle.id)).filter(Boolean);
  const closed = new Set(run.events.filter(event => event.kind === "arc_closed" && event.at_min <= time).map(event => event.arc_id));
  const cleaned = new Set(run.events.filter(event => event.kind === "service_started" && event.end_min <= time).map(event => event.arc_id));
  Object.entries(roads).forEach(([id, road]) => {
    const working = active.some(event => event.kind === "service_started" && event.arc_id === id);
    line(road, "#101e27", 9);
    line(road, closed.has(id) ? colors.closed : working ? colors.blue : cleaned.has(id) ? colors.green : colors.road, 4, closed.has(id) ? [8,7] : []);
  });

  function label(x, y, title, fill, align = "left") {
    ctx.font = "600 " + (w < 520 ? 11 : 13) + "px system-ui";
    ctx.textAlign = align;
    ctx.strokeStyle = "#182c36";
    ctx.lineWidth = 4;
    ctx.strokeText(title, x*w, y*h);
    ctx.fillStyle = fill;
    ctx.fillText(title, x*w, y*h);
  }
  if (showLayers) {
    const pins = [
      [.19,.71,"База",.17,.81,"#acc6d3","left"],
      [.75,.51,"Больница",.79,.47,"#b9f472","left"],
      [.57,.14,"Снегоплощадка",.59,.14,"#ffc486","left"],
      [.85,.65,"Остановка",.87,.69,"#70d9ef","left"],
    ];
    for (const [x,y,title,tx,ty,fill,align] of pins) {
      ctx.fillStyle = fill;
      ctx.fillRect(x*w-4,y*h-4,8,8);
      label(tx,ty,title,fill,align);
    }
    label(.53,.20,"Верхняя улица","#a0b7c3","center");
    label(.71,.91,"Авачинская бухта · схематично","#789aab","center");
  }

  fixture.vehicles.forEach((vehicle, index) => {
    const motion = activeMovement(run, vehicle.id);
    const at = run.events.filter(event => event.vehicle_id === vehicle.id && event.end_min && event.end_min <= time).at(-1);
    const unavailable = run.events.some(event => event.kind === "vehicle_unavailable" && event.vehicle_id === vehicle.id && event.at_min <= time);
    const point = motion ? pointOn(roads[motion.arc_id], (time - motion.at_min) / (motion.end_min - motion.at_min)) :
      nodes[at ? arcs[at.arc_id].to : vehicle.start_node];
    const x = point[0]*w, y = point[1]*h;
    ctx.beginPath();
    ctx.arc(x,y,16,0,Math.PI*2);
    ctx.fillStyle = unavailable ? "#8a654e" : index ? colors.blue : colors.green;
    ctx.fill();
    ctx.strokeStyle = "#14212a";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.fillStyle = "#152634";
    ctx.font = "800 12px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(tags[vehicle.id],x,y+4);
  });
}

function drawChart(run) {
  const { ctx, width: w, height: h } = setupCanvas("chart");
  ctx.clearRect(0, 0, w, h);
  const x = minute => 29 + (w - 45) * minute / fixture.duration_min;
  const y = count => h - 24 - (h - 36) * count / fixture.jobs.length;
  ctx.font = "11px system-ui";
  ctx.lineWidth = 1;
  ctx.textAlign = "right";
  for (let count = 0; count <= fixture.jobs.length; count++) {
    ctx.strokeStyle = "#314451";
    ctx.beginPath();
    ctx.moveTo(x(0),y(count));
    ctx.lineTo(x(fixture.duration_min),y(count));
    ctx.stroke();
    ctx.fillStyle = "#9db3c2";
    ctx.fillText(count,x(0)-8,y(count)+3);
  }
  ctx.textAlign = "center";
  for (let minute = 0; minute <= fixture.duration_min; minute += 15) {
    ctx.fillText(minute,x(minute),h-7);
  }

  function drawSteps(selectedRun, shade, dash, until) {
    const work = selectedRun.events.filter(event => event.kind === "job_completed" && event.at_min <= until);
    ctx.beginPath();
    ctx.moveTo(x(0), y(0));
    work.forEach((event, index) => {
      ctx.lineTo(x(event.at_min),y(index));
      ctx.lineTo(x(event.at_min),y(index + 1));
    });
    ctx.lineTo(x(until),y(work.length));
    ctx.strokeStyle = shade;
    ctx.lineWidth = 2.5;
    ctx.setLineDash(dash);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  if (comparison) {
    const comparisonRun = variant === "normal" ? runs["vehicle-unavailable"] : runs.normal;
    drawSteps(comparisonRun, colors.blue, [5,5], fixture.duration_min);
    drawSteps(run, colors.green, [], fixture.duration_min);
  } else {
    drawSteps(run, colors.green, [], time);
  }
  ctx.beginPath();
  ctx.moveTo(x(time), y(0));
  ctx.lineTo(x(time), y(fixture.jobs.length));
  ctx.strokeStyle = "#b1c2cd77";
  ctx.lineWidth = 1;
  ctx.stroke();
}

function showNarrative() {
  const [title, badge, explanation, impact] = narratives[variant];
  el("decision-title").textContent = title;
  el("decision-badge").textContent = badge;
  el("decision-body").textContent = explanation;
  el("decision-impact").textContent = "Итог прогона · " + impact;
  el("chart-caption").textContent = comparison ?
    "Салатовый — выбранный сценарий · Голубой — " + (variant === "normal" ? "отказ машины" : "штатная смена") :
    "Выполненные задания к текущей минуте";
}

function render() {
  if (el("simulation-view").hidden) return;
  const run = runs[variant];
  updateText(run);
  drawMap(run);
  drawChart(run);
}

function tick(timestamp) {
  if (playing && previousFrame && !el("simulation-view").hidden) {
    time = Math.min(fixture.duration_min, time + Math.min(timestamp - previousFrame, 50) * .006 * Number(el("speed").value));
    if (time === fixture.duration_min) playing = false;
    render();
  }
  previousFrame = timestamp;
  requestAnimationFrame(tick);
}

function seek(minute) {
  time = minute;
  playing = false;
  render();
}

el("variant").addEventListener("change", event => {
  variant = event.target.value;
  showNarrative();
  seek(0);
});
el("play").addEventListener("click", () => {
  if (time >= fixture.duration_min) time = 0;
  playing = !playing;
  render();
});
el("reset").addEventListener("click", () => seek(0));
el("time").addEventListener("input", event => seek(Number(event.target.value)));
el("focus-event").addEventListener("click", () => seek(narratives[variant][4]));
el("compare").addEventListener("click", () => {
  comparison = !comparison;
  el("compare").setAttribute("aria-pressed", comparison);
  showNarrative();
  render();
});
el("layers").addEventListener("click", () => {
  showLayers = !showLayers;
  el("layers").setAttribute("aria-pressed", showLayers);
  render();
});
el("fullscreen").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (error) {
    el("fullscreen").title = "Полноэкранный режим доступен через меню браузера";
  }
});
el("export").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(runs[variant], null, 2)], { type: "application/json" }));
  const download = document.createElement("a");
  download.href = url;
  download.download = "kamchatka-" + variant + ".json";
  download.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
for (const id of ["method","proof"]) el(id).addEventListener("click", () => el("details").showModal());
el("close").addEventListener("click", () => el("details").close());
window.addEventListener("simulation-shown", render);
window.addEventListener("resize", render);
if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) playing = false;
el("time").max = fixture.duration_min;
setNarratives();
showNarrative();
render();
requestAnimationFrame(tick);
