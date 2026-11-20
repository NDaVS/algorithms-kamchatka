import { useSyncExternalStore } from "react";
import { connectWs } from "../api/client";
import { fetchBasemap, fetchEdges, emptyBasemap, type EdgeIndex } from "../api/data";
import type { Basemap, ReplanProposal, ServerMessage, SimEvent, SimState } from "../api/types";
import { isMock, startMock } from "./mock";

export interface Toast { id: number; text: string; kind: "info" | "danger" | "success" }
export interface AppState {
  connected: boolean;
  starting: boolean;           // server reachable but initial plan not ready
  sim: SimState | null;
  simAt: number;               // Date.now() when the last snapshot arrived
  proposal: ReplanProposal | null;
  proposalAt: number;          // Date.now() when `proposal` was last refreshed
  proposalTotal: number;       // initial auto-apply delay (s) of the current proposal
  events: SimEvent[];
  toast: Toast | null;
  basemap: Basemap | null;
  edges: EdgeIndex | null;
}

let state: AppState = {
  connected: false, starting: false, sim: null, simAt: 0, proposal: null, proposalAt: 0, proposalTotal: 0,
  events: [], toast: null, basemap: null, edges: null,
};
const listeners = new Set<() => void>();
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
export const getState = () => state;
export function setState(p: Partial<AppState>) {
  state = { ...state, ...p };
  listeners.forEach((l) => l());
}
export function useStore<T>(sel: (s: AppState) => T): T {
  return useSyncExternalStore(subscribe, () => sel(state));
}

let toastSeq = 0;
let toastTimer: number | undefined;
export function showToast(text: string, kind: Toast["kind"] = "info", ms = 6000) {
  setState({ toast: { id: ++toastSeq, text, kind } });
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => setState({ toast: null }), ms);
}

export function ingestSim(s: SimState) {
  setState({ sim: s, simAt: Date.now(), ...proposalPatch(s.pending_proposal) });
}
function proposalPatch(p: ReplanProposal | null | undefined): Partial<AppState> {
  const prev = state;
  const pp = p && p.status === "pending" ? p : null;
  if (!pp) return { proposal: null, proposalAt: Date.now(), proposalTotal: 0 };
  const isNew = !prev.proposal || prev.proposal.id !== pp.id;
  return { proposal: pp, proposalAt: Date.now(), proposalTotal: isNew ? pp.auto_apply_in_s ?? 0 : prev.proposalTotal };
}

function onEvent(ev: SimEvent) {
  if (state.events.some((e) => e.id === ev.id)) return;
  setState({ events: [...state.events, ev].slice(-50) });
  switch (ev.type) {
    case "breakdown": showToast(ev.text, "danger"); break;
    case "plan_applied": showToast(ev.text, "success"); break;
    case "new_request": case "proposal_rejected": case "info": showToast(ev.text, "info"); break;
    default: break;
  }
}

function boot(): () => void {
  let alive = true;
  let taskCount = -1;
  let edgeTimer: number | undefined;

  const loadEdges = () => {
    fetchEdges().then((e) => { if (alive) setState({ edges: e }); })
      .catch(() => { if (alive) edgeTimer = window.setTimeout(loadEdges, 1000); });
  };
  const scheduleEdges = () => { window.clearTimeout(edgeTimer); edgeTimer = window.setTimeout(loadEdges, 300); };

  let bmTimer: number | undefined;
  const loadBasemap = () => {
    fetchBasemap().then((b) => { if (alive) setState({ basemap: b }); })
      .catch(() => { if (alive) bmTimer = window.setTimeout(loadBasemap, 1000); });
  };
  loadBasemap();

  const onMsg = (msg: ServerMessage) => {
    switch (msg.type) {
      case "snapshot": {
        const s = msg.data;
        const n = Object.keys(s.task_statuses ?? {}).length;
        if (n !== taskCount) { taskCount = n; scheduleEdges(); }
        const patch: Partial<AppState> = { sim: s, simAt: Date.now(), connected: true, starting: false, ...proposalPatch(s.pending_proposal) };
        if (state.events.length === 0 && s.recent_events?.length) patch.events = s.recent_events;
        setState(patch);
        break;
      }
      case "status": setState({ connected: true, starting: msg.data?.status === "starting" }); break;
      case "event": onEvent(msg.data); break;
      case "proposal": setState(proposalPatch(msg.data)); break;
      default: break;
    }
  };

  let off: () => void;
  if (isMock) { off = startMock(onMsg); setState({ connected: true }); }
  else off = connectWs(onMsg, (c) => setState({ connected: c }));
  return () => { alive = false; window.clearTimeout(edgeTimer); window.clearTimeout(bmTimer); off(); };
}

let refs = 0;
let stop: (() => void) | null = null;
/** Start the data feed (WebSocket or mock). Returns a stop function; safe to call from several components. */
export function startData(): () => void {
  if (refs++ === 0) stop = boot();
  return () => { if (--refs === 0) { stop?.(); stop = null; } };
}
