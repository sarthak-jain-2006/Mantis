// Typed calls to the Node backend. Shapes mirror backend/src/routes.

export type TaskStatus =
  | "QUEUED"
  | "RUNNING"
  | "TESTING"
  | "REVIEWING"
  | "AWAITING_APPROVAL"
  | "WAITING_FOR_INPUT"
  | "COMPLETED"
  | "FAILED"
  | "REJECTED"
  | "CANCELLED";

// The Planner's output. Mirrors ImplementationPlan in ai-service/schemas.py.
export interface Plan {
  summary: string;
  subtasks: string[];
  acceptance_criteria: string[];
  constraints: string[];
  needs_research: boolean;
}

// The Repository Analyst's findings. Mirrors AnalysisReport in ai-service/agents/analyst.py.
export interface Analysis {
  summary: string;
  relevant_files: { path: string; reason: string }[];
  test_files: string[];
  test_command: string;
  conventions: string[];
}

// What the Coder changed. The diff arrives when the run completes.
export interface Changes {
  summary: string;
  notes: string[];
  changed_files: { path: string; change: string }[];
  diff?: string;
  diff_truncated?: boolean;
  diff_hash?: string;
}

export type Step = "planner" | "analyst" | "coder";

export interface Task {
  id: string;
  projectId: string | null;
  request: string;
  status: TaskStatus;
  // Which agent is working now; null when nothing is running.
  currentStep: Step | null;
  // Each step's output appears as soon as it is ready. Older tasks may hold only
  // { plan } (Phase 2) or { message } (Phase 1).
  result: { plan?: Plan; analysis?: Analysis; changes?: Changes; model?: string; message?: string } | null;
  diffHash: string | null;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
}

// One thing a run did: a step starting or finishing, a tool call, the end of the run.
export interface AgentEvent {
  id: string;
  type: "run_started" | "step_started" | "step_finished" | "tool_called" | "run_completed" | "run_failed";
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface Health {
  status: "ok" | "degraded";
  services: { backend: boolean; database: boolean; aiService: boolean };
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const BACKEND_UNREACHABLE = "Backend is not reachable. Is it running on port 3000?";

async function request(path: string, init?: RequestInit): Promise<{ res: Response; body: unknown }> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(BACKEND_UNREACHABLE, 0);
  }
  // Error responses from the Vite proxy (backend down) have no JSON body.
  const body: unknown = await res.json().catch(() => null);
  return { res, body };
}

function errorMessage(body: unknown, status: number): string {
  const b = body as { error?: string; issues?: { message: string }[] } | null;
  return b?.issues?.[0]?.message ?? b?.error ?? (status >= 500 ? BACKEND_UNREACHABLE : `Request failed (${status})`);
}

function isTask(body: unknown): body is Task {
  return typeof body === "object" && body !== null && "id" in body && "status" in body;
}

export async function getHealth(): Promise<Health> {
  const { res, body } = await request("/api/health");
  // 503 still carries a per-service report; only a missing report means the backend itself is down.
  if (typeof body === "object" && body !== null && "services" in body) return body as Health;
  throw new ApiError(BACKEND_UNREACHABLE, res.status);
}

export async function listTasks(): Promise<Task[]> {
  const { res, body } = await request("/api/tasks");
  if (!res.ok) throw new ApiError(errorMessage(body, res.status), res.status);
  return body as Task[];
}

export async function listTaskEvents(taskId: string): Promise<AgentEvent[]> {
  const { res, body } = await request(`/api/tasks/${taskId}/events`);
  if (!res.ok) throw new ApiError(errorMessage(body, res.status), res.status);
  return body as AgentEvent[];
}

export async function createTask(text: string): Promise<Task> {
  const { res, body } = await request("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request: text }),
  });
  // A 502 with a task body means the task was saved but the AI service failed; it comes back as FAILED.
  if (isTask(body)) return body;
  throw new ApiError(errorMessage(body, res.status), res.status);
}
