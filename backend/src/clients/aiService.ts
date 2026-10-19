import { z } from "zod";

// What the Python service returns from POST /runs: the run has started in the background.
// Progress and results arrive later as events (see routes/internal.ts).
const RunAcceptedSchema = z.object({
  task_id: z.string(),
  status: z.literal("accepted"),
});

// FastAPI puts the reason for an error in "detail".
const ErrorBodySchema = z.object({ detail: z.string() });

export type RunAccepted = z.infer<typeof RunAcceptedSchema>;

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
  startRun(taskId: string, userRequest: string): Promise<RunAccepted>;
  isHealthy(): Promise<boolean>;
}

export function createAiServiceClient(options: {
  baseUrl: string;
  token: string;
  timeoutMs?: number;
}): AiServiceClient {
  // Starting a run returns at once, so a short timeout is enough.
  const timeoutMs = options.timeoutMs ?? 10_000;

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
        // Pass on the AI service's reason when it gave one.
        const detail = ErrorBodySchema.safeParse(body);
        const reason = detail.success ? `: ${detail.data.detail}` : "";
        throw new AiServiceError(`AI service returned ${response.status}${reason}`, response.status);
      }

      const parsed = RunAcceptedSchema.safeParse(body);
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
