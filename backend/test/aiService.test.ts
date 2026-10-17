import { afterEach, describe, expect, it, vi } from "vitest";
import { createAiServiceClient } from "../src/clients/aiService.js";

// Unit tests for the HTTP client: the Python service is faked at the fetch level.
const client = createAiServiceClient({ baseUrl: "http://ai.test", token: "t".repeat(40) });

function respondWith(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const PLANNED = {
  task_id: "task-1",
  status: "planned",
  plan: {
    summary: "Add pagination",
    subtasks: ["Find the endpoint"],
    acceptance_criteria: ["Tests pass"],
    constraints: [],
    needs_research: false,
  },
  model: "gemini-flash-latest",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startRun", () => {
  it("sends the task with the internal token and returns the plan", async () => {
    const fetchMock = respondWith(PLANNED);

    const run = await client.startRun("task-1", "Add pagination");

    expect(run.plan.summary).toBe("Add pagination");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(String(url)).toBe("http://ai.test/runs");
    expect((init.headers as Record<string, string>)["X-Internal-Token"]).toBe("t".repeat(40));
  });

  it("keeps the AI service's reason when it fails", async () => {
    respondWith({ detail: "Gemini rate limit reached; gave up after 3 attempts" }, 503);

    await expect(client.startRun("task-1", "x")).rejects.toThrow(
      "AI service returned 503: Gemini rate limit reached; gave up after 3 attempts",
    );
  });

  it("rejects a reply that is not a plan", async () => {
    respondWith({ ...PLANNED, plan: { summary: "missing fields" } });

    await expect(client.startRun("task-1", "x")).rejects.toThrow("unexpected response shape");
  });

  it("reports a connection failure as unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));

    await expect(client.startRun("task-1", "x")).rejects.toThrow("AI service unreachable: fetch failed");
  });
});
