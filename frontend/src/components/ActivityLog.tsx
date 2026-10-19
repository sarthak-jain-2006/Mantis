import { useQuery } from "@tanstack/react-query";
import { listTaskEvents, type AgentEvent } from "../api/client";

const STEP_NAMES: Record<string, string> = { planner: "Planner", analyst: "Analyst", coder: "Coder" };

function describe(event: AgentEvent): { text: string; tone: "normal" | "muted" | "bad" } {
  const p = event.payload;
  switch (event.type) {
    case "run_started":
      return { text: "Run started", tone: "muted" };
    case "step_started":
      return { text: `${STEP_NAMES[String(p.step)] ?? p.step} started`, tone: "muted" };
    case "step_finished":
      return { text: `${STEP_NAMES[String(p.step)] ?? p.step} finished`, tone: "muted" };
    case "tool_called": {
      const args = (p.args ?? {}) as Record<string, unknown>;
      const target = args.path ?? args.pattern ?? "";
      const call = `${p.role} · ${p.tool}${target ? ` ${String(target)}` : ""}`;
      return p.ok
        ? { text: `${call}: ${p.summary ?? "ok"}`, tone: "normal" }
        : { text: `${call}: refused (${String(p.error)})`, tone: "bad" };
    }
    case "run_completed":
      return { text: "Run finished with a proposed change", tone: "muted" };
    case "run_failed":
      return { text: `Run failed: ${String(p.error)}`, tone: "bad" };
  }
}

const TONE = { normal: "text-slate-700", muted: "text-slate-400", bad: "text-red-600" };

// Every step and tool call of a run, fetched when the section is opened.
export function ActivityLog({ taskId, live }: { taskId: string; live: boolean }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["events", taskId],
    queryFn: () => listTaskEvents(taskId),
    refetchInterval: live ? 2_000 : false,
  });

  if (isPending) return <p className="text-sm text-slate-500">Loading activity…</p>;
  if (isError) return <p className="text-sm text-red-600">{error.message}</p>;
  if (data.length === 0) return <p className="text-sm text-slate-500">Nothing yet.</p>;
  return (
    <ol aria-label="Activity" className="space-y-0.5 font-mono text-xs">
      {data.map((event) => {
        const { text, tone } = describe(event);
        return (
          <li key={event.id} className={TONE[tone]}>
            {text}
          </li>
        );
      })}
    </ol>
  );
}
