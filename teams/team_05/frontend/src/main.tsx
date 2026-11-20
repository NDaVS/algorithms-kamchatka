import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";
import App from "./App";

// Ship uncaught client errors to the backend (logs/frontend.log). Never throws.
function sendClientLog(kind: string, message: string, stack?: string) {
  try {
    const body = JSON.stringify({ kind, message, stack, url: location.href, ua: navigator.userAgent });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/client-log", new Blob([body], { type: "application/json" }));
    } else {
      void fetch("/api/client-log", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
    }
  } catch { /* ignore */ }
}
window.addEventListener("error", (e) => {
  sendClientLog("onerror", `${e.message} (${e.filename}:${e.lineno}:${e.colno})`, e.error?.stack);
});
window.addEventListener("unhandledrejection", (e) => {
  const r = e.reason;
  sendClientLog("unhandledrejection", String(r?.message ?? r), r?.stack);
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
