import { Router } from "express";
import type { AppDeps } from "../app.js";

export function healthRouter({ prisma, aiService }: AppDeps): Router {
  const router = Router();

  // Reports each dependency separately so the UI can show which one is down.
  router.get("/api/health", async (_req, res) => {
    const [database, aiServiceUp] = await Promise.all([
      prisma.$queryRaw`SELECT 1`.then(() => true).catch(() => false),
      aiService.isHealthy(),
    ]);

    const ok = database && aiServiceUp;
    res.status(ok ? 200 : 503).json({
      status: ok ? "ok" : "degraded",
      services: { backend: true, database, aiService: aiServiceUp },
    });
  });

  return router;
}
