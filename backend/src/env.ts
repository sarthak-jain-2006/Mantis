import { fileURLToPath } from "node:url";
import { config } from "dotenv";
import { z } from "zod";

// All services share the .env at the repository root.
config({ path: fileURLToPath(new URL("../../.env", import.meta.url)), quiet: true });

const EnvSchema = z.object({
  DATABASE_URL: z.url(),
  BACKEND_PORT: z.coerce.number().int().positive().default(3000),
  AI_SERVICE_URL: z.url().default("http://localhost:8000"),
  // Must match the AI service; min length rejects placeholders like "change-me".
  INTERNAL_TOKEN: z.string().min(32),
  FRONTEND_ORIGIN: z.url().default("http://localhost:5173"),
});

export type Env = z.infer<typeof EnvSchema>;

// Fail fast at startup with a readable message instead of failing on the first request.
const parsed = EnvSchema.safeParse(process.env);
if (!parsed.success) {
  console.error("Invalid environment configuration:\n" + z.prettifyError(parsed.error));
  process.exit(1);
}

export const env: Env = parsed.data;
