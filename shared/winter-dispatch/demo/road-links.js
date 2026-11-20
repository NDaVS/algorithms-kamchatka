"use strict";

function createRoadLinks(city, project) {
  const graph = new Map();
  const lines = new Map();
  const turns = new Map();
  const coords = new Map();

  function location(node) {
    if (!coords.has(node)) coords.set(node, project(city.nodes[node]));
    return coords.get(node);
  }

  function distance(a, b) {
    const first = location(a);
    const second = location(b);
    return Math.hypot(first[0] - second[0], first[1] - second[1]);
  }

  function edgesFor(way, refs) {
    return refs.slice(1).map((to, i) => ({
      from: refs[i], to, way, length: distance(refs[i], to),
    }));
  }

  for (const [id, [refs, tags]] of Object.entries(city.ways)) {
    if (!canAssignRoad({ tags }) || refs.length < 2) continue;
    const way = Number(id);
    const variants = [];
    const implicitOneWay = tags.oneway === undefined &&
      (tags.junction === "roundabout" || tags.junction === "circular" || tags.highway === "motorway");
    if (tags.oneway !== "-1") variants.push(edgesFor(way, refs));
    if (!implicitOneWay && !["yes", "1", "true"].includes(tags.oneway)) variants.push(edgesFor(way, [...refs].reverse()));
    lines.set(way, variants);
    for (const line of variants) {
      for (const edge of line) {
        if (!graph.has(edge.from)) graph.set(edge.from, []);
        graph.get(edge.from).push(edge);
      }
    }
  }

  for (const [members, tags] of Object.values(city.turn_restrictions)) {
    const via = members.find(member => member.role === "via" && member.type === "node");
    const from = members.find(member => member.role === "from" && member.type === "way");
    const to = members.find(member => member.role === "to" && member.type === "way");
    const rule = tags["restriction:motor_vehicle"] ?? tags.restriction;
    if (!via || !from || !to || !rule || tags.except?.split(";").some(value => ["vehicle", "motor_vehicle"].includes(value.trim()))) continue;
    const key = via.ref + ":" + from.ref;
    if (!turns.has(key)) turns.set(key, []);
    turns.get(key).push({ to: Number(to.ref), rule });
  }

  function permitted(previous, edge) {
    if (!previous) return true;
    const rules = turns.get(previous.to + ":" + previous.way) ?? [];
    return rules.every(({ to, rule }) => {
      const uTurn = previous.from === edge.to;
      if (rule === "no_u_turn" && to === previous.way) return !uTurn || edge.way !== to;
      if (rule === "only_straight_on" && to === previous.way) return !uTurn && edge.way === to;
      if (rule.startsWith("only_")) return edge.way === to;
      if (rule.startsWith("no_")) return edge.way !== to;
      return true;
    });
  }

  const stateKey = state => state.node + ":" + (state.previous ? state.previous.from + ":" + state.previous.way : "");

  function connector(origin, next) {
    const goal = next.from;
    const queue = [];
    const best = new Map();
    const root = { ...origin, cost: 0, score: distance(origin.node, goal), parent: null };
    push(queue, root);
    best.set(stateKey(root), 0);
    while (queue.length) {
      const state = pop(queue);
      if (state.cost !== best.get(stateKey(state))) continue;
      if (state.node === goal && permitted(state.previous, next)) {
        const approach = [];
        for (let item = state; item.parent; item = item.parent) approach.push(item.previous);
        return { edges: approach.reverse(), cost: state.cost };
      }
      for (const edge of graph.get(state.node) ?? []) {
        if (!permitted(state.previous, edge)) continue;
        const cost = state.cost + edge.length;
        const following = {
          node: edge.to, previous: edge, cost, score: cost + distance(edge.to, goal), parent: state,
        };
        const key = stateKey(following);
        if (cost >= (best.get(key) ?? Infinity)) continue;
        best.set(key, cost);
        push(queue, following);
      }
    }
    return null;
  }

  function plan(origin, ways) {
    let options = [{ ...origin, edges: [], cost: 0 }];
    for (const way of ways) {
      const next = [];
      for (const line of lines.get(way) ?? []) {
        if (!line.every((edge, i) => permitted(line[i - 1], edge))) continue;
        let winner = null;
        for (const option of options) {
          const approach = connector(option, line[0]);
          if (!approach) continue;
          const cost = option.cost + approach.cost;
          if (!winner || cost < winner.cost) {
            winner = {
              node: line.at(-1).to, previous: line.at(-1), cost,
              edges: [...option.edges, ...approach.edges.map(edge => ({ ...edge, work: false })),
                ...line.map(edge => ({ ...edge, work: true }))],
            };
          }
        }
        if (winner) next.push(winner);
      }
      if (!next.length) return null;
      options = next;
    }
    return options.reduce((best, option) => option.cost < best.cost ? option : best).edges
      .map(({ from, to, way, work }) => ({ from, to, way, work }));
  }

  function planSegment(origin, target) {
    const next = graph.get(target.from)?.find(edge => edge.to === target.to && edge.way === target.way);
    if (!next || !city.nodes[origin.node]) return null;
    const approach = connector(origin, next);
    if (!approach) return null;
    return [...approach.edges.map(edge => ({ ...edge, work: false })), { ...next, work: true }];
  }

  return { plan, planSegment };
}

function push(heap, item) {
  let index = heap.length;
  heap.push(item);
  while (index > 0) {
    const parent = (index - 1) >> 1;
    if (heap[parent].score <= item.score) break;
    heap[index] = heap[parent];
    index = parent;
  }
  heap[index] = item;
}

function pop(heap) {
  const head = heap[0];
  const tail = heap.pop();
  if (!heap.length) return head;
  let index = 0;
  while (index * 2 + 1 < heap.length) {
    const left = index * 2 + 1;
    const right = left + 1;
    const next = right < heap.length && heap[right].score < heap[left].score ? right : left;
    if (heap[next].score >= tail.score) break;
    heap[index] = heap[next];
    index = next;
  }
  heap[index] = tail;
  return head;
}
