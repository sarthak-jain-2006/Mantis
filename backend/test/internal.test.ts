import { randomUUID } from "node:crypto";
import type { Express } from "express";
import request from "supertest";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { buildApp } from "../src/app.js";
import type { AiServiceClient } from "../src/clients/aiService.js";
import { prisma } from "../src/db.js";

// The endpoint the AI service reports run progress to. Real Postgres, no AI service needed.
const TOKEN = "i".repeat(40);
const unusedAi: AiServiceClient = {
  async startRun() {
    throw new Error("not used here");
  },
  async isHealthy() {
    return true;
  },
};

let app: Express;
let taskId: string;
const createdIds: string[] = [];

const DIFF = "diff --git a/app/routers/products.py b/app/routers/products.py\n+def list_products(limit: int = 20):\n";
const HASH = "a".repeat(64);

function send(type: string, payload: object = {}, token = TOKEN, id = taskId) {
  return request(app).post(`/internal/tasks/${id}/events`).set("X-Internal-Token", token).send({ type, payload });
}

beforeAll(() => {
  app = buildApp({ deps: { prisma, aiService: unusedAi, internalToken: TOKEN }, frontendOrigin: "http://localhost:5173", logger: false });
});

beforeEach(async () => {
  const task = await prisma.task.create({ data: { request: "Add pagination" } });
  taskId = task.id;
  createdIds.push(task.id);
});

afterAll(async () => {
  await prisma.task.deleteMany({ where: { id: { in: createdIds } } });
  await prisma.$disconnect();
});

const task = () => prisma.task.findUniqueOrThrow({ where: { id: taskId } });

describe("authentication", () => {
  it("rejects a missing or wrong token", async () => {
    expect((await request(app).post(`/internal/tasks/${taskId}/events`).send({ type: "run_started", payload: {} })).status).toBe(401);
    expect((await send("run_started", {}, "wrong")).status).toBe(401);
    expect((await task()).status).toBe("QUEUED");
  });

  it("does not affect the public API", async () => {
    // The token check is mounted under /internal only.
    expect((await request(app).get("/api/tasks")).status).toBe(200);
  });
});

describe("validation", () => {
  it("rejects an unknown event type", async () => {
    expect((await send("delete_everything")).status).toBe(400);
  });

  it("rejects a step the backend doesn't know", async () => {
    expect((await send("step_started", { step: "hacker" })).status).toBe(400);
  });

  it("rejects a completed run without a valid diff hash", async () => {
    const res = await send("run_completed", { plan: {}, analysis: {}, changes: { diff: DIFF, diff_hash: "nope" }, model: "m" });
    expect(res.status).toBe(400);
  });

  it("returns 404 for an unknown task", async () => {
    expect((await send("run_started", {}, TOKEN, randomUUID())).status).toBe(404);
  });
});

describe("a run's life", () => {
  it("moves the task through its states and records every event", async () => {
    expect((await send("run_started")).status).toBe(204);
    expect((await task()).status).toBe("RUNNING");

    await send("step_started", { step: "planner" });
    expect((await task()).currentStep).toBe("planner");

    await send("step_finished", { step: "planner", output: { summary: "Add pagination" } });
    // The plan is visible while later steps are still running.
    expect((await task()).result).toEqual({ plan: { summary: "Add pagination" } });

    await send("step_started", { step: "analyst" });
    await send("tool_called", { role: "analyst", tool: "read_file", ok: true, summary: "lines 1-26 of 26" });
    expect((await task()).currentStep).toBe("analyst");

    await send("run_completed", {
      plan: { summary: "Add pagination" },
      analysis: { summary: "products router" },
      changes: { summary: "Added limit", diff: DIFF, diff_hash: HASH },
      model: "gemini-test",
    });
    const done = await task();
    // Not COMPLETED: tests and a person's approval come first (later phases).
    expect(done.status).toBe("AWAITING_APPROVAL");
    expect(done.currentStep).toBeNull();
    expect(done.diffHash).toBe(HASH);
    expect(done.result).toMatchObject({ changes: { diff: DIFF }, model: "gemini-test" });

    const events = await prisma.agentEvent.findMany({ where: { taskId }, orderBy: { createdAt: "asc" } });
    expect(events.map((e) => e.type)).toEqual([
      "run_started",
      "step_started",
      "step_finished",
      "step_started",
      "tool_called",
      "run_completed",
    ]);
    expect(events[4]?.payload).toMatchObject({ tool: "read_file", summary: "lines 1-26 of 26" });
  });

  it("records a failure with its reason", async () => {
    await send("run_started");
    await send("run_failed", { error: "Gemini's free daily quota is used up", step: "analyst" });

    const failed = await task();
    expect(failed.status).toBe("FAILED");
    expect(failed.error).toBe("Gemini's free daily quota is used up");
    expect(failed.finishedAt).not.toBeNull();
  });

  it("keeps late events but no longer changes a finished task", async () => {
    await send("run_failed", { error: "stopped", step: null });
    await send("step_started", { step: "coder" });

    const t = await task();
    expect(t.status).toBe("FAILED");
    expect(t.currentStep).toBeNull();
    expect(await prisma.agentEvent.count({ where: { taskId } })).toBe(2);
  });

  it("fails tasks left running when the AI service restarts, and only those", async () => {
    // Note: like the other backend tests this uses the dev database, so any task really
    // running there while the tests run would be marked interrupted too.
    await send("run_started");
    await send("step_started", { step: "coder" });
    const queued = await prisma.task.create({ data: { request: "Waiting in line" } });
    const finished = await prisma.task.create({ data: { request: "Done", status: "AWAITING_APPROVAL" } });
    createdIds.push(queued.id, finished.id);

    const res = await request(app).post("/internal/runs/interrupted").set("X-Internal-Token", TOKEN);
    expect(res.status).toBe(200);
    expect(res.body.interrupted).toBeGreaterThanOrEqual(2);

    const running = await task();
    expect(running.status).toBe("FAILED");
    expect(running.error).toBe("Interrupted: the AI service restarted during the run. Submit the task again.");
    expect(running.currentStep).toBeNull();
    const lastEvent = await prisma.agentEvent.findFirst({ where: { taskId }, orderBy: { createdAt: "desc" } });
    expect(lastEvent).toMatchObject({ type: "run_failed", payload: { step: "coder" } });

    expect((await prisma.task.findUniqueOrThrow({ where: { id: queued.id } })).status).toBe("FAILED");
    expect((await prisma.task.findUniqueOrThrow({ where: { id: finished.id } })).status).toBe("AWAITING_APPROVAL");
  });

  it("needs the token to interrupt runs", async () => {
    expect((await request(app).post("/internal/runs/interrupted")).status).toBe(401);
  });

  it("accepts a large diff", async () => {
    const big = "+x\n".repeat(40_000); // ~120 KB, over the public API's 100 KB limit
    const res = await send("run_completed", { plan: {}, analysis: {}, changes: { diff: big, diff_hash: HASH }, model: "m" });
    expect(res.status).toBe(204);
  });
});
