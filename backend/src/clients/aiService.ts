import { z } from "zod";

// What the Python service returns from POST /runs. Validated, never trusted blindly.
const RunResponseSchema = z.object({
  task_id: z.string(),
  status: z.literal("received"),
  message: z.string(),
});

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

      if (!response.ok) {
        throw new AiServiceError(`AI service returned ${response.status}`, response.status);
      }

      const parsed = RunResponseSchema.safeParse(await response.json());
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
