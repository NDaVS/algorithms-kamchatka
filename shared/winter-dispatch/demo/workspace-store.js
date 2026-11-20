"use strict";

const workspaceKey = "kamchatka.workspace.v2";
const currentShiftKey = "kamchatka.workspace.current";

function shiftSummary(snapshot, revision) {
  const { id, title, status, createdAt, closedAt } = snapshot;
  return { id, title, status, createdAt, closedAt, revision };
}

function shiftStore(shared) {
  if (shared) {
    async function request(path, options) {
      const response = await fetch("/api/shifts" + path, options);
      if (response.status === 409) throw new Error("conflict");
      if (!response.ok) throw new Error("Сервер не сохранил данные (" + response.status + ")");
      return response.json();
    }
    return {
      shared,
      list: () => request(""),
      read: id => request("/" + encodeURIComponent(id)),
      write: (snapshot, revision) => request("/" + encodeURIComponent(snapshot.id), {
        method: "PUT",
        headers: { "Content-Type": "application/json", "If-Match": String(revision) },
        body: JSON.stringify(snapshot),
      }),
    };
  }

  const readAll = () => JSON.parse(localStorage.getItem(workspaceKey) || "{}");
  return {
    shared,
    async list() {
      return Object.values(readAll()).map(row => shiftSummary(row.snapshot, row.revision))
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    },
    async read(id) {
      const row = readAll()[id];
      if (!row) throw new Error("Смена не найдена");
      return row;
    },
    async write(snapshot, revision) {
      const all = readAll();
      const current = all[snapshot.id];
      if ((current?.revision ?? 0) !== revision || current?.snapshot.status === "closed") throw new Error("conflict");
      const row = { snapshot, revision: revision + 1 };
      all[snapshot.id] = row;
      localStorage.setItem(workspaceKey, JSON.stringify(all));
      return row;
    },
  };
}

function newShiftId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
}
