import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Analysis, Changes, Plan, Task } from "./api/client";
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
    currentStep: null,
    result: null,
    diffHash: null,
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

const ANALYSIS: Analysis = {
  summary: "GET /products is handled by list_products in the products router.",
  relevant_files: [{ path: "app/routers/products.py", reason: "Defines GET /products." }],
  test_files: ["tests/test_products.py"],
  test_command: "pytest -q",
  conventions: [],
};

const DIFF = [
  "diff --git a/app/routers/products.py b/app/routers/products.py",
  "@@ -9,2 +9,3 @@",
  "-def list_products():",
  "+def list_products(limit: int = 20, offset: int = 0):",
].join("\n");

const CHANGES: Changes = {
  summary: "Added limit and offset query parameters with validation.",
  notes: ["Default page size is 20"],
  changed_files: [{ path: "app/routers/products.py", change: "modified" }],
  diff: DIFF,
  diff_truncated: false,
  diff_hash: "a".repeat(64),
};

describe("tasks", () => {
  it("submits a task and shows it as queued", async () => {
    const tasks: Task[] = [];
    const fetchMock = fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () => json(tasks),
      "POST /api/tasks": (init) => {
        const { request } = JSON.parse(String(init?.body)) as { request: string };
        const task = makeTask({ request, status: "QUEUED" });
        tasks.unshift(task);
        return json(task, 201);
      },
    });
    renderApp();
    await screen.findByText("No tasks yet.");

    await userEvent.type(screen.getByLabelText("What should Mantis do?"), "Add pagination");
    await userEvent.click(screen.getByRole("button", { name: "Submit task" }));

    expect(await screen.findByText("QUEUED")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Waiting to start…");
    expect(screen.getByLabelText("What should Mantis do?")).toHaveValue("");
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks", expect.objectContaining({ method: "POST" }));
  });

  it("shows which step a running task is on, and the plan as soon as it exists", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () => json([makeTask({ currentStep: "analyst", result: { plan: PLAN } })]),
    });
    renderApp();

    expect(await screen.findByRole("status")).toHaveTextContent("Step 2 of 3: Reading the code…");
    expect(screen.getByText(`Plan: ${PLAN.summary}`)).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps.map((li) => li.textContent)).toEqual(PLAN.subtasks);
  });

  it("shows the proposed change with its diff when the run finishes", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () =>
        json([
          makeTask({
            status: "AWAITING_APPROVAL",
            result: { plan: PLAN, analysis: ANALYSIS, changes: CHANGES, model: "gemini-flash-lite-latest" },
            diffHash: "a".repeat(64),
          }),
        ]),
    });
    renderApp();

    expect(await screen.findByText(CHANGES.summary)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument(); // nothing is running
    expect(within(screen.getByRole("list", { name: "Files changed" })).getByText("app/routers/products.py")).toBeInTheDocument();
    expect(screen.getByText("Default page size is 20")).toBeInTheDocument();
    const diff = screen.getByLabelText("Diff");
    expect(within(diff).getByText("+def list_products(limit: int = 20, offset: int = 0):")).toHaveClass("bg-emerald-50");
    expect(within(diff).getByText("-def list_products():")).toHaveClass("bg-red-50");
    expect(screen.getByText(/Not tested or applied yet/)).toHaveTextContent("Written by gemini-flash-lite-latest.");
    // What the analyst found is there too.
    expect(within(screen.getByRole("list", { name: "Relevant files" })).getByText("app/routers/products.py")).toBeInTheDocument();
  });

  it("loads the activity log when it is opened", async () => {
    const task = makeTask({ status: "AWAITING_APPROVAL", result: { plan: PLAN, analysis: ANALYSIS, changes: CHANGES } });
    const fetchMock = fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () => json([task]),
      [`GET /api/tasks/${task.id}/events`]: () =>
        json([
          { id: "1", type: "step_started", payload: { step: "analyst" }, createdAt: task.createdAt },
          {
            id: "2",
            type: "tool_called",
            payload: { role: "analyst", tool: "read_file", args: { path: "app/main.py" }, ok: true, summary: "lines 1-12 of 12" },
            createdAt: task.createdAt,
          },
          {
            id: "3",
            type: "tool_called",
            payload: { role: "analyst", tool: "write_file", args: { path: "app/main.py" }, ok: false, error: "not allowed" },
            createdAt: task.createdAt,
          },
        ]),
    });
    renderApp();
    await screen.findByText(CHANGES.summary);
    expect(fetchMock).not.toHaveBeenCalledWith(`/api/tasks/${task.id}/events`, undefined);

    await userEvent.click(screen.getByText("Activity"));

    const log = await screen.findByRole("list", { name: "Activity" });
    expect(within(log).getByText("Analyst started")).toBeInTheDocument();
    expect(within(log).getByText("analyst · read_file app/main.py: lines 1-12 of 12")).toBeInTheDocument();
    expect(within(log).getByText("analyst · write_file app/main.py: refused (not allowed)")).toHaveClass("text-red-600");
  });

  it("shows why a run failed", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () =>
        json([makeTask({ status: "FAILED", error: "Gemini's free daily quota for gemini-flash-latest is used up" })]),
    });
    renderApp();

    expect(await screen.findByText(/free daily quota/)).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });

  it("still shows tasks from earlier phases", async () => {
    fakeBackend({
      "GET /api/health": healthy,
      "GET /api/tasks": () =>
        json([
          makeTask({ status: "RUNNING", result: { message: "AI service received: Add pagination" } }),
          makeTask({ status: "RUNNING", request: "Plan only", result: { plan: PLAN } }),
        ]),
    });
    renderApp();

    expect(await screen.findByText("AI service received: Add pagination")).toBeInTheDocument();
    expect(screen.getByText(`Plan: ${PLAN.summary}`)).toBeInTheDocument();
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

