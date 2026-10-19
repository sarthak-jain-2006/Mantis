import { afterEach, describe, expect, it, vi } from "vitest";
import { createAiServiceClient } from "../src/clients/aiService.js";

// Unit tests for the HTTP client: the Python service is faked at the fetch level.
const client = createAiServiceClient({ baseUrl: "http://ai.test", token: "t".repeat(40) });

function respondWith(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const ACCEPTED = { task_id: "task-1", status: "accepted" };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startRun", () => {
  it("sends the task with the internal token and returns once the run is accepted", async () => {
    const fetchMock = respondWith(ACCEPTED, 202);

    const run = await client.startRun("task-1", "Add pagination");

    expect(run).toEqual(ACCEPTED);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(String(url)).toBe("http://ai.test/runs");
    expect((init.headers as Record<string, string>)["X-Internal-Token"]).toBe("t".repeat(40));
  });

  it("keeps the AI service's reason when it fails", async () => {
    respondWith({ detail: "This task is already running" }, 409);

    await expect(client.startRun("task-1", "x")).rejects.toThrow(
      "AI service returned 409: This task is already running",
    );
  });

  it("rejects a reply with an unexpected shape", async () => {
    respondWith({ task_id: "task-1", status: "planned" }, 202);

    await expect(client.startRun("task-1", "x")).rejects.toThrow("unexpected response shape");
  });

  it("reports a connection failure as unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));

    await expect(client.startRun("task-1", "x")).rejects.toThrow("AI service unreachable: fetch failed");
  });
});
