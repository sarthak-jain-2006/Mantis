import { randomUUID } from "node:crypto";
import type { Express } from "express";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { buildApp } from "../src/app.js";
import { AiServiceError, type AiServiceClient } from "../src/clients/aiService.js";
import { prisma } from "../src/db.js";

// Integration tests: real Postgres (docker compose), fake AI service.
type FakeAiService = AiServiceClient & { healthy: boolean; fail: boolean };

const fakeAi: FakeAiService = {
  healthy: true,
  fail: false,
  async startRun(taskId: string, userRequest: string) {
    if (this.fail) throw new AiServiceError("AI service unreachable: connection refused");
    return { task_id: taskId, status: "received" as const, message: `AI service received: ${userRequest}` };
  },
  async isHealthy() {
    return this.healthy;
  },
};

let app: Express;
const createdIds: string[] = [];

beforeAll(() => {
  app = buildApp({
    deps: { prisma, aiService: fakeAi },
    frontendOrigin: "http://localhost:5173",
    logger: false,
  });
});

afterAll(async () => {
  await prisma.task.deleteMany({ where: { id: { in: createdIds } } });
  await prisma.$disconnect();
});

describe("POST /api/tasks", () => {
  it("saves the task and records the AI service reply", async () => {
    fakeAi.fail = false;
    const res = await request(app).post("/api/tasks").send({ request: "Add pagination" });
    createdIds.push(res.body.id);

    expect(res.status).toBe(201);
    expect(res.body.status).toBe("RUNNING");
    expect(res.body.result.message).toBe("AI service received: Add pagination");

    const fromDb = await prisma.task.findUnique({ where: { id: res.body.id } });
    expect(fromDb?.request).toBe("Add pagination");
  });

  it("marks the task FAILED when the AI service is down", async () => {
    fakeAi.fail = true;
    const res = await request(app).post("/api/tasks").send({ request: "Fix bug" });
    createdIds.push(res.body.id);
    fakeAi.fail = false;

    expect(res.status).toBe(502);
    expect(res.body.status).toBe("FAILED");
    expect(res.body.error).toContain("unreachable");
  });

  it("rejects an empty request with 400", async () => {
    const res = await request(app).post("/api/tasks").send({ request: "   " });
    expect(res.status).toBe(400);
    expect(res.body.issues[0].path).toBe("request");
  });

  it("rejects malformed JSON with 400", async () => {
    const res = await request(app)
      .post("/api/tasks")
      .set("Content-Type", "application/json")
      .send("{not json");
    expect(res.status).toBe(400);
  });
});

describe("GET /api/tasks/:id", () => {
  it("returns 404 for an unknown task", async () => {
    const res = await request(app).get(`/api/tasks/${randomUUID()}`);
    expect(res.status).toBe(404);
  });

  it("returns 400 for an id that is not a UUID", async () => {
    const res = await request(app).get("/api/tasks/not-a-uuid");
    expect(res.status).toBe(400);
  });
});

describe("GET /api/health", () => {
  it("reports 503 and which service is down", async () => {
    fakeAi.healthy = false;
    const res = await request(app).get("/api/health");
    fakeAi.healthy = true;

    expect(res.status).toBe(503);
    expect(res.body.services).toEqual({ backend: true, database: true, aiService: false });
  });
});

describe("unknown routes", () => {
  it("return 404 JSON", async () => {
    const res = await request(app).get("/api/nope");
    expect(res.status).toBe(404);
    expect(res.body).toEqual({ error: "Not found" });
  });
});
