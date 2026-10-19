import cors from "cors";
import express, { type ErrorRequestHandler, type Express } from "express";
import { pino } from "pino";
import { pinoHttp } from "pino-http";
import { ZodError } from "zod";
import type { PrismaClient } from "../generated/prisma/client.js";
import type { AiServiceClient } from "./clients/aiService.js";
import { healthRouter } from "./routes/health.js";
import { internalRouter } from "./routes/internal.js";
import { taskRouter } from "./routes/tasks.js";

export interface AppDeps {
  prisma: PrismaClient;
  aiService: AiServiceClient;
  // Shared secret the AI service sends when it reports run events.
  internalToken: string;
}

export interface BuildAppOptions {
  deps: AppDeps;
  frontendOrigin: string;
  logger?: boolean;
}

// Turns any error thrown in a route into a JSON response.
// Express recognises error handlers by their four arguments.
const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
  if (err instanceof ZodError) {
    res.status(400).json({
      error: "Invalid request",
      issues: err.issues.map((i) => ({ path: i.path.join("."), message: i.message })),
    });
    return;
  }
  // Errors raised by Express itself (e.g. malformed JSON body) carry a 4xx status.
  const status = typeof err?.status === "number" ? err.status : 500;
  if (status < 500) {
    res.status(status).json({ error: err.message });
    return;
  }
  req.log.error(err);
  res.status(500).json({ error: "Internal server error" });
};

// Dependencies are passed in rather than imported, so tests can swap the
// AI service for a fake without starting Python.
export function buildApp({ deps, frontendOrigin, logger = true }: BuildAppOptions): Express {
  const app = express();

  app.disable("x-powered-by"); // Don't advertise the framework in response headers.

  // Structured JSON request logs with a request id. Disabled (but still defined) in tests.
  app.use(pinoHttp({ logger: pino({ enabled: logger }) }));

  // Only the dashboard may call this API from a browser.
  app.use(cors({ origin: frontendOrigin }));

  // Events from the AI service. A finished run carries its whole diff, so these get a larger
  // body limit. Mounted before the general parser below, which then leaves the body alone.
  app.use("/internal", express.json({ limit: "2mb" }), internalRouter(deps));

  // Parse JSON bodies; reject anything over 100 KB.
  app.use(express.json({ limit: "100kb" }));

  app.use(healthRouter(deps));
  app.use(taskRouter(deps));

  app.use((_req, res) => {
    res.status(404).json({ error: "Not found" });
  });

  app.use(errorHandler);

  return app;
}
