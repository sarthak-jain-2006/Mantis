import { Router } from "express";
import { z } from "zod";
import type { AppDeps } from "../app.js";
import { AiServiceError } from "../clients/aiService.js";

const CreateTaskBody = z.object({
  request: z.string().trim().min(1, "Task description is required").max(4000),
});

const TaskIdParams = z.object({
  id: z.uuid(),
});

export function taskRouter({ prisma, aiService }: AppDeps): Router {
  const router = Router();

  // Create a task, hand it to the AI service, and record what happened.
  // Express 5 forwards errors thrown in async handlers to the error handler in app.ts.
  router.post("/api/tasks", async (req, res) => {
    const body = CreateTaskBody.parse(req.body);

    // Saved first, so the task exists even if the AI service is down.
    const task = await prisma.task.create({
      data: { request: body.request, status: "QUEUED" },
    });

    try {
      const run = await aiService.startRun(task.id, task.request);
      // Phase 1: the AI service only acknowledges the task. From Phase 4 the
      // agent graph reports real progress and moves the task through its states.
      const updated = await prisma.task.update({
        where: { id: task.id },
        data: { status: "RUNNING", result: run },
      });
      res.status(201).json(updated);
    } catch (err) {
      if (!(err instanceof AiServiceError)) throw err;
      req.log.warn({ taskId: task.id, err: err.message }, "AI service call failed");
      const failed = await prisma.task.update({
        where: { id: task.id },
        data: { status: "FAILED", error: err.message, finishedAt: new Date() },
      });
      // 502: our server is fine, but the service behind it failed.
      res.status(502).json(failed);
    }
  });

  router.get("/api/tasks", async (_req, res) => {
    const tasks = await prisma.task.findMany({ orderBy: { createdAt: "desc" }, take: 20 });
    res.json(tasks);
  });

  router.get("/api/tasks/:id", async (req, res) => {
    const { id } = TaskIdParams.parse(req.params);
    const task = await prisma.task.findUnique({ where: { id } });
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }
    res.json(task);
  });

  return router;
}
