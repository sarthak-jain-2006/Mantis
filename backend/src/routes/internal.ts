import { createHash, timingSafeEqual } from "node:crypto";
import { type RequestHandler, Router } from "express";
import { z } from "zod";
import type { Prisma, TaskStatus } from "../../generated/prisma/client.js";
import type { AppDeps } from "../app.js";

// Endpoints only the Python AI service calls. Not proxied to the browser, and every request
// must carry the shared internal token.

const Step = z.enum(["planner", "analyst", "coder"]);

// Where each step's output is kept on the task while the run is still going.
const RESULT_KEY: Record<z.infer<typeof Step>, string> = { planner: "plan", analyst: "analysis", coder: "changes" };

// looseObject: extra fields are kept (and stored in the event), so Python can add detail
// without a backend change; the fields the backend acts on are checked.
const EventBody = z.discriminatedUnion("type", [
  z.object({ type: z.literal("run_started"), payload: z.looseObject({}) }),
  z.object({ type: z.literal("step_started"), payload: z.looseObject({ step: Step }) }),
  z.object({
    type: z.literal("step_finished"),
    payload: z.looseObject({ step: Step, output: z.record(z.string(), z.unknown()) }),
  }),
  z.object({
    type: z.literal("tool_called"),
    payload: z.looseObject({ role: z.string(), tool: z.string(), ok: z.boolean() }),
  }),
  z.object({
    type: z.literal("run_completed"),
    payload: z.looseObject({
      plan: z.record(z.string(), z.unknown()),
      analysis: z.record(z.string(), z.unknown()),
      changes: z.looseObject({ diff: z.string(), diff_hash: z.string().regex(/^[0-9a-f]{64}$/) }),
      model: z.string(),
    }),
  }),
  z.object({
    type: z.literal("run_failed"),
    payload: z.looseObject({ error: z.string().max(2_000), step: Step.nullable() }),
  }),
]);

type EventBody = z.infer<typeof EventBody>;

const TaskIdParams = z.object({ id: z.uuid() });

// Once a task has ended, late events are still recorded but no longer change it.
const FINAL: TaskStatus[] = ["COMPLETED", "FAILED", "REJECTED", "CANCELLED"];

function requireInternalToken(expected: string): RequestHandler {
  // Hashing first gives both sides the same length, which timingSafeEqual requires;
  // comparing in constant time means the token can't be guessed from response times.
  const expectedHash = createHash("sha256").update(expected).digest();
  return (req, res, next) => {
    const given = req.get("X-Internal-Token") ?? "";
    const givenHash = createHash("sha256").update(given).digest();
    if (!timingSafeEqual(givenHash, expectedHash)) {
      res.status(401).json({ error: "Invalid internal token" });
      return;
    }
    next();
  };
}

function taskUpdate(event: EventBody, currentResult: Prisma.JsonValue | null): Prisma.TaskUpdateInput {
  switch (event.type) {
    case "run_started":
      return { status: "RUNNING" };
    case "step_started":
      return { status: "RUNNING", currentStep: event.payload.step };
    case "step_finished": {
      // Show each step's output as soon as it is ready, e.g. the plan while the analyst works.
      const result = (currentResult ?? {}) as Prisma.JsonObject;
      return { result: { ...result, [RESULT_KEY[event.payload.step]]: event.payload.output as Prisma.InputJsonValue } };
    }
    case "tool_called":
      return {};
    case "run_completed": {
      const { plan, analysis, changes, model } = event.payload;
      // Not COMPLETED: a task is complete only after its tests pass and a person approves the diff.
      // Until those phases exist, the run stops here with a diff for a person to look at.
      return {
        status: "AWAITING_APPROVAL",
        currentStep: null,
        result: { plan, analysis, changes, model } as Prisma.InputJsonValue,
        diffHash: changes.diff_hash,
      };
    }
    case "run_failed":
      return { status: "FAILED", currentStep: null, error: event.payload.error, finishedAt: new Date() };
  }
}

// Mounted at /internal in app.ts, so the token check below covers only these routes.
export function internalRouter({ prisma, internalToken }: AppDeps): Router {
  const router = Router();
  router.use(requireInternalToken(internalToken));

  router.post("/tasks/:id/events", async (req, res) => {
    const { id } = TaskIdParams.parse(req.params);
    const event = EventBody.parse(req.body);

    const task = await prisma.task.findUnique({ where: { id }, select: { status: true, result: true } });
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }

    const update = FINAL.includes(task.status) ? {} : taskUpdate(event, task.result);
    // The event and the task change are saved together or not at all.
    await prisma.$transaction([
      prisma.agentEvent.create({
        data: { taskId: id, type: event.type, payload: event.payload as Prisma.InputJsonValue },
      }),
      prisma.task.update({ where: { id }, data: update }),
    ]);
    res.status(204).end();
  });

  // Called by the AI service when it starts. Runs live inside that process, so any task still
  // QUEUED or RUNNING belonged to a process that has stopped (a restart, a crash, a code reload)
  // and will never finish. Each is marked FAILED with the reason, so none is left "running" forever.
  // Assumes one AI service per database, which is how Mantis runs.
  router.post("/runs/interrupted", async (_req, res) => {
    const stuck = await prisma.task.findMany({
      where: { status: { in: ["QUEUED", "RUNNING"] } },
      select: { id: true, currentStep: true },
    });
    const error = "Interrupted: the AI service restarted during the run. Submit the task again.";
    await prisma.$transaction(
      stuck.flatMap((task) => [
        prisma.agentEvent.create({
          data: { taskId: task.id, type: "run_failed", payload: { error, step: task.currentStep } },
        }),
        prisma.task.update({
          where: { id: task.id },
          data: { status: "FAILED", currentStep: null, error, finishedAt: new Date() },
        }),
      ]),
    );
    res.json({ interrupted: stuck.length });
  });

  return router;
}
