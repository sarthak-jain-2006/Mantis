import { buildApp } from "./app.js";
import { createAiServiceClient } from "./clients/aiService.js";
import { prisma } from "./db.js";
import { env } from "./env.js";

const app = buildApp({
  deps: {
    prisma,
    aiService: createAiServiceClient({ baseUrl: env.AI_SERVICE_URL, token: env.INTERNAL_TOKEN }),
    internalToken: env.INTERNAL_TOKEN,
  },
  frontendOrigin: env.FRONTEND_ORIGIN,
});

// 127.0.0.1: reachable from this machine only.
const server = app.listen(env.BACKEND_PORT, "127.0.0.1", (err) => {
  if (err) {
    console.error("Failed to start backend:", err);
    process.exit(1);
  }
  console.log(`Backend listening on http://127.0.0.1:${env.BACKEND_PORT}`);
});

// Stop accepting requests and close the database pool cleanly on Ctrl+C.
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => {
    server.close(async () => {
      await prisma.$disconnect();
      process.exit(0);
    });
  });
}
