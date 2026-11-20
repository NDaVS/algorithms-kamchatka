import type { ClientMessage, ServerMessage } from "./types";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/** Connect to /ws with auto-reconnect. Returns a close function. */
export function connectWs(
  onMsg: (m: ServerMessage) => void,
  onStatus?: (connected: boolean) => void
): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let pingTimer: number | undefined;
  let retryTimer: number | undefined;
  let attempt = 0;
  const open = () => {
    if (closed) return;
    if (ws) { ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null; try { ws.close(); } catch { /* ignore */ } }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    ws = sock;
    sock.onopen = () => {
      attempt = 0;
      onStatus?.(true);
      window.clearInterval(pingTimer);
      pingTimer = window.setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" } satisfies ClientMessage));
      }, 20000);
    };
    sock.onmessage = (e) => {
      try { onMsg(JSON.parse(e.data) as ServerMessage); } catch { /* ignore malformed */ }
    };
    sock.onerror = () => { sock.close(); };
    sock.onclose = () => {
      if (ws !== sock) return;
      window.clearInterval(pingTimer);
      onStatus?.(false);
      if (!closed) {
        const delay = Math.min(500 * 2 ** attempt, 2000); // 500ms -> 1s -> 2s max
        attempt++;
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(open, delay);
      }
    };
  };
  open();
  return () => {
    closed = true;
    window.clearInterval(pingTimer);
    window.clearTimeout(retryTimer);
    if (ws) { const w = ws; ws = null; w.onopen = w.onmessage = w.onerror = w.onclose = null; w.close(); }
  };
}
export type { ClientMessage };
