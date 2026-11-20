import { post, api } from "./client";
import type { DriverView, NewRequestBody, Plan, ProblemKind, SimEvent, SimState, Task } from "./types";
import { ingestSim } from "../state/store";
import { isMock, mockCommands } from "../state/mock";

export async function simControl(action: "start" | "pause" | "reset", speed?: number): Promise<void> {
  const s = isMock ? mockCommands.control(action, speed) : await post<SimState>("/sim/control", { action, speed: speed ?? null });
  ingestSim(s);
}
export const breakdown = (vehicle_id: string): Promise<SimEvent> =>
  isMock ? Promise.resolve().then(() => mockCommands.breakdown(vehicle_id)) : post<SimEvent>("/events/breakdown", { vehicle_id });
export const newRequest = (body: NewRequestBody): Promise<SimEvent> =>
  isMock ? Promise.resolve().then(() => mockCommands.request(body)) : post<SimEvent>("/events/request", body);
export const applyProposal = (id: string): Promise<Plan> =>
  isMock ? Promise.resolve().then(() => mockCommands.apply(id)) : post<Plan>(`/proposal/${encodeURIComponent(id)}/apply`);
export const rejectProposal = (id: string): Promise<{ ok: boolean }> =>
  isMock ? Promise.resolve().then(() => mockCommands.reject(id)) : post<{ ok: boolean }>(`/proposal/${encodeURIComponent(id)}/reject`);
export const completeTask = (task_id: string, vehicle_id: string): Promise<SimEvent> =>
  isMock ? Promise.resolve().then(() => mockCommands.complete(task_id, vehicle_id))
    : post<SimEvent>(`/tasks/${encodeURIComponent(task_id)}/complete`, { vehicle_id });
export const fetchDriver = (vid: string): Promise<DriverView> =>
  isMock ? Promise.resolve(mockCommands.driver(vid)) : api<DriverView>(`/driver/${encodeURIComponent(vid)}`);
export const fetchTasks = (): Promise<Task[]> =>
  isMock ? Promise.resolve(mockCommands.tasks()) : api<Task[]>("/tasks");
export const reportProblem = (vehicle_id: string, kind: ProblemKind, comment?: string): Promise<SimEvent> =>
  isMock ? Promise.resolve().then(() => mockCommands.problem(vehicle_id, kind, comment))
    : post<SimEvent>("/events/problem", { vehicle_id, kind, comment: comment ?? null });
