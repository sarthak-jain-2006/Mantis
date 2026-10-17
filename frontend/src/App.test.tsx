import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Plan, Task } from "./api/client";
import App from "./App";

// The backend is faked at the fetch level, so these tests cover the UI and the API client together.
type Route = (init?: RequestInit) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function fakeBackend(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const route = routes[`${init?.method ?? "GET"} ${url}`];
    if (!route) throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    return route(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: crypto.randomUUID(),
    projectId: null,
    request: "Add pagination",
    status: "RUNNING",
    result: null,
    error: null,
    createdAt: "2026-10-17T10:00:00.000Z",
    updatedAt: "2026-10-17T10:00:00.000Z",
    finishedAt: null,
    ...overrides,
  };
}

const healthy = () => json({ status: "ok", services: { backend: true, database: true, aiService: true } });

// Health loads asynchronously and starts as "unknown", so wait for the expected state.
async function expectService(name: string, state: "up" | "down" | "unknown") {
  const list = screen.getByRole("list", { name: "Service health" });
  await waitFor(() => expect(within(list).getByText(name).closest("li")).toHaveTextContent(new RegExp(`${state}$`)));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("health", () => {
  it("shows which service is down", async () => {
    fakeBackend({
      "GET /api/health": () =>
        json({ status: "degraded", services: { backend: true, database: true, aiService: false } }, 503),
      "GET /api/tasks": () => json([]),
    });
    renderApp();

    await expectService("AI service", "down");
    await expectService("Database", "up");
  });

  it("marks everything behind an unreachable backend as unknown", async () => {
    fakeBackend({
      "GET /api/health": () => Promise.reject(new TypeError("Failed to fetch")),
      "GET /api/tasks": () => Promise.reject(new TypeError("Failed to fetch")),
    });
    renderApp();

    await screen.findByText(/Backend is not reachable/);
    await expectService("Backend", "down");
    await expectService("AI service", "unknown");
  });
});

const PLAN: Plan = {
  summary: "Add limit/offset pagination to the products endpoint.",
  subtasks: ["Find the products endpoint", "Add limit and offset parameters", "Write tests for the bounds"],
  acceptance_criteria: ["Existing tests still pass"],
  constraints: [],
  needs_research: false,
};

describe("tasks", () => {
  it("submits a task and shows the plan", async () => {
    const tasks: Task[] = [];
    const fetchMock = fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () => json(tasks),
      "POST /api/tasks": (init) => {
        const { request } = JSON.parse(String(init?.body)) as { request: string };
        const task = makeTask({ request, result: { plan: PLAN, model: "gemini-flash-latest" } });
        tasks.unshift(task);
        return json(task, 201);
      },
    });
    renderApp();
    await screen.findByText("No tasks yet.");

    await userEvent.type(screen.getByLabelText("What should Mantis do?"), "Add pagination");
    await userEvent.click(screen.getByRole("button", { name: "Submit task" }));

    expect(await screen.findByText(`Plan: ${PLAN.summary}`)).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps.map((li) => li.textContent)).toEqual(PLAN.subtasks);
    expect(within(screen.getByRole("list", { name: "Done when" })).getByText("Existing tests still pass")).toBeInTheDocument();
    // An empty list is left out rather than shown as an empty heading.
    expect(screen.queryByRole("list", { name: "Must not break" })).not.toBeInTheDocument();
    expect(screen.getByText(/planned by gemini-flash-latest/)).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByLabelText("What should Mantis do?")).toHaveValue("");
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks", expect.objectContaining({ method: "POST" }));
  });

  it("still shows tasks saved before plans existed", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () => json([makeTask({ result: { message: "AI service received: Add pagination" } })]),
    });
    renderApp();

    expect(await screen.findByText("AI service received: Add pagination")).toBeInTheDocument();
  });

  it("shows why planning failed", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () =>
        json([
          makeTask({
            status: "FAILED",
            error: "AI service returned 503: Gemini rate limit reached; gave up after 3 attempts",
          }),
        ]),
    });
    renderApp();

    expect(await screen.findByText(/Gemini rate limit reached/)).toBeInTheDocument();
  });

  it("lists a task the AI service could not take as FAILED", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () =>
        json([makeTask({ request: "Fix bug", status: "FAILED", error: "AI service unreachable" })]),
    });
    renderApp();

    expect(await screen.findByText("AI service unreachable")).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });

  it("shows the backend's validation message", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () => json([]),
      "POST /api/tasks": () =>
        json(
          { error: "Invalid request", issues: [{ path: "request", message: "Task description is required" }] },
          400,
        ),
    });
    renderApp();

    await userEvent.type(screen.getByLabelText("What should Mantis do?"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Submit task" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Task description is required");
  });

  it("keeps submit disabled until there is text", async () => {
    fakeBackend({ "GET /api/health": healthy, "GET /api/tasks": () => json([]) });
    renderApp();

    const button = screen.getByRole("button", { name: "Submit task" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What should Mantis do?"), "   ");
    expect(button).toBeDisabled();
  });
});
