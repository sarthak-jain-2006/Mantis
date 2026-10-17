import { z } from "zod";

// The Planner's output. Mirrors ImplementationPlan in ai-service/schemas.py.
const PlanSchema = z.object({
  summary: z.string(),
  subtasks: z.array(z.string()),
  acceptance_criteria: z.array(z.string()),
  constraints: z.array(z.string()),
  needs_research: z.boolean(),
});

// What the Python service returns from POST /runs. Validated, never trusted blindly.
const RunResponseSchema = z.object({
  task_id: z.string(),
  status: z.literal("planned"),
  plan: PlanSchema,
  model: z.string(),
});

// FastAPI puts the reason for an error in "detail".
const ErrorBodySchema = z.object({ detail: z.string() });

export type Plan = z.infer<typeof PlanSchema>;
export type RunResponse = z.infer<typeof RunResponseSchema>;

export class AiServiceError extends Error {
  constructor(
    message: string,
    readonly statusCode?: number,
  ) {
    super(message);
    this.name = "AiServiceError";
  }
}

export interface AiServiceClient {
  startRun(taskId: string, userRequest: string): Promise<RunResponse>;
  isHealthy(): Promise<boolean>;
}

export function createAiServiceClient(options: {
  baseUrl: string;
  token: string;
  timeoutMs?: number;
}): AiServiceClient {
  // A real model call takes seconds, and the AI service may retry a rate-limited or malformed
  // reply. 90s is longer than its worst case (two 30s calls plus back-off), so its own,
  // clearer error arrives before this timeout fires.
  const timeoutMs = options.timeoutMs ?? 90_000;

  return {
    async startRun(taskId, userRequest) {
      let response: Response;
      try {
        response = await fetch(new URL("/runs", options.baseUrl), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Internal-Token": options.token,
          },
          body: JSON.stringify({ task_id: taskId, user_request: userRequest }),
          signal: AbortSignal.timeout(timeoutMs),
        });
      } catch (err) {
        // Network error, connection refused, or timeout.
        throw new AiServiceError(`AI service unreachable: ${(err as Error).message}`);
      }

      const body: unknown = await response.json().catch(() => null);

      if (!response.ok) {
        // Pass on the AI service's reason (e.g. "Gemini rate limit reached") when it gave one.
        const detail = ErrorBodySchema.safeParse(body);
        const reason = detail.success ? `: ${detail.data.detail}` : "";
        throw new AiServiceError(`AI service returned ${response.status}${reason}`, response.status);
      }

      const parsed = RunResponseSchema.safeParse(body);
      if (!parsed.success) {
        throw new AiServiceError("AI service returned an unexpected response shape");
      }
      return parsed.data;
    },

    async isHealthy() {
      try {
        const response = await fetch(new URL("/health", options.baseUrl), {
          signal: AbortSignal.timeout(2_000),
        });
        return response.ok;
      } catch {
        return false;
      }
    },
  };
}
