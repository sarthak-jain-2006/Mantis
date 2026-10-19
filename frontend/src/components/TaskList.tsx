import { useQuery } from "@tanstack/react-query";
import { Fragment, useState, type ReactNode } from "react";
import { listTasks, type Analysis, type Changes, type Plan, type Step, type Task, type TaskStatus } from "../api/client";
import { ActivityLog } from "./ActivityLog";
import { DiffView } from "./DiffView";

const STATUS_STYLE: Partial<Record<TaskStatus, string>> = {
  QUEUED: "bg-slate-100 text-slate-700",
  RUNNING: "bg-blue-100 text-blue-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-800",
  AWAITING_APPROVAL: "bg-amber-100 text-amber-800",
};

const STEPS: { step: Step; label: string }[] = [
  { step: "planner", label: "Planning" },
  { step: "analyst", label: "Reading the code" },
  { step: "coder", label: "Writing the change" },
];

const isActive = (task: Task) => task.status === "QUEUED" || task.status === "RUNNING";

function Section({ title, open = false, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details open={open} className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <summary className="cursor-pointer text-sm font-medium text-slate-800">{title}</summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  );
}

function List({ title, items, ordered = false }: { title: string; items: ReactNode[]; ordered?: boolean }) {
  if (items.length === 0) return null;
  const Tag = ordered ? "ol" : "ul";
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      <Tag aria-label={title} className={`mt-1 space-y-0.5 pl-5 text-sm text-slate-700 ${ordered ? "list-decimal" : "list-disc"}`}>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </Tag>
    </div>
  );
}

function Progress({ task }: { task: Task }) {
  const index = STEPS.findIndex((s) => s.step === task.currentStep);
  const text =
    index === -1 ? "Waiting to start…" : `Step ${index + 1} of ${STEPS.length}: ${STEPS[index]?.label}…`;
  return (
    <p role="status" className="text-sm text-blue-700">
      {text}
    </p>
  );
}

function PlanSection({ plan, open }: { plan: Plan; open: boolean }) {
  return (
    <Section title={`Plan: ${plan.summary}`} open={open}>
      <List title="Steps" items={plan.subtasks} ordered />
      <List title="Done when" items={plan.acceptance_criteria} />
      <List title="Must not break" items={plan.constraints} />
    </Section>
  );
}

function AnalysisSection({ analysis }: { analysis: Analysis }) {
  return (
    <Section title="What Mantis found in the code">
      <p className="text-sm text-slate-700">{analysis.summary}</p>
      <List
        title="Relevant files"
        items={analysis.relevant_files.map((f) => (
          <Fragment key={f.path}>
            <code className="text-xs">{f.path}</code>: {f.reason}
          </Fragment>
        ))}
      />
      <List title="Tests" items={analysis.test_files.map((p) => (
          <code key={p} className="text-xs">
            {p}
          </code>
        ))} />
    </Section>
  );
}

function ChangesSection({ changes, model }: { changes: Changes; model?: string }) {
  return (
    <Section title="Proposed change" open>
      <p className="text-sm text-slate-700">{changes.summary}</p>
      <List
        title="Files changed"
        items={changes.changed_files.map((f) => (
          <Fragment key={f.path}>
            <code className="text-xs">{f.path}</code> ({f.change})
          </Fragment>
        ))}
      />
      <List title="Notes for the reviewer" items={changes.notes} />
      {changes.diff && <DiffView diff={changes.diff} truncated={changes.diff_truncated} />}
      <p className="text-xs text-slate-500">
        Not tested or applied yet: running the tests and approving the change come in later phases.
        {model && ` Written by ${model}.`}
      </p>
    </Section>
  );
}

function ActivitySection({ task }: { task: Task }) {
  // The log is fetched only once someone opens the section.
  const [opened, setOpened] = useState(false);
  return (
    <details onToggle={(e) => setOpened(e.currentTarget.open)} className="rounded-md border border-slate-200 p-3">
      <summary className="cursor-pointer text-sm font-medium text-slate-800">Activity</summary>
      <div className="mt-3">{opened && <ActivityLog taskId={task.id} live={isActive(task)} />}</div>
    </details>
  );
}

function TaskRow({ task }: { task: Task }) {
  const { plan, analysis, changes, model, message } = task.result ?? {};
  // Tasks from Phase 1 have a plain message instead of a plan.
  const legacyMessage = !plan && !task.error ? message : undefined;
  return (
    <li className="space-y-2 p-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm font-medium text-slate-900">{task.request}</p>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[task.status] ?? "bg-slate-100 text-slate-700"}`}
        >
          {task.status}
        </span>
      </div>
      {isActive(task) && <Progress task={task} />}
      {task.error && <p className="text-sm text-red-600">{task.error}</p>}
      {legacyMessage && <p className="text-sm text-slate-600">{legacyMessage}</p>}
      {plan && <PlanSection plan={plan} open={!changes} />}
      {analysis && <AnalysisSection analysis={analysis} />}
      {changes && task.status !== "FAILED" && <ChangesSection changes={changes} model={model} />}
      {(isActive(task) || analysis || task.status === "FAILED") && <ActivitySection task={task} />}
      <p className="text-xs text-slate-400">{new Date(task.createdAt).toLocaleString()}</p>
    </li>
  );
}

export function TaskList() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["tasks"],
    queryFn: listTasks,
    // While any task is running, check for progress every 2 seconds. Live push arrives in Phase 8.
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 2_000 : false),
  });

  let body;
  if (isPending) body = <p className="p-4 text-sm text-slate-500">Loading tasks…</p>;
  else if (isError) body = <p className="p-4 text-sm text-red-600">{error.message}</p>;
  else if (data.length === 0) body = <p className="p-4 text-sm text-slate-500">No tasks yet.</p>;
  else
    body = (
      <ul className="divide-y divide-slate-100">
        {data.map((task) => (
          <TaskRow key={task.id} task={task} />
        ))}
      </ul>
    );

  return (
    <section aria-labelledby="recent-tasks" className="rounded-lg border border-slate-200 bg-white">
      <h2 id="recent-tasks" className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-700">
        Recent tasks
      </h2>
      {body}
    </section>
  );
}
