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
      // Starts Planner -> Analyst -> Coder in the background and returns at once. The AI service
      // then reports progress to /internal/tasks/:id/events, which moves the task through its states.
      await aiService.startRun(task.id, task.request);
      // Read back rather than reuse `task`: the first events may already have arrived.
      const current = await prisma.task.findUniqueOrThrow({ where: { id: task.id } });
      res.status(201).json(current);
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

  // Everything the run did so far, oldest first: steps, tool calls, results.
  router.get("/api/tasks/:id/events", async (req, res) => {
    const { id } = TaskIdParams.parse(req.params);
    const task = await prisma.task.findUnique({ where: { id }, select: { id: true } });
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }
    const events = await prisma.agentEvent.findMany({
      where: { taskId: id },
      orderBy: { createdAt: "asc" },
      take: 500,
      select: { id: true, type: true, payload: true, createdAt: true },
    });
    res.json(events);
  });

  return router;
}
