import { useQuery } from "@tanstack/react-query";
import { listTasks, type Task, type TaskStatus } from "../api/client";

const STATUS_STYLE: Partial<Record<TaskStatus, string>> = {
  RUNNING: "bg-blue-100 text-blue-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-800",
  AWAITING_APPROVAL: "bg-amber-100 text-amber-800",
};

function TaskRow({ task }: { task: Task }) {
  const detail = task.error ?? task.result?.message;
  return (
    <li className="space-y-1 p-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm font-medium text-slate-900">{task.request}</p>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[task.status] ?? "bg-slate-100 text-slate-700"}`}
        >
          {task.status}
        </span>
      </div>
      {detail && <p className={`text-sm ${task.error ? "text-red-600" : "text-slate-600"}`}>{detail}</p>}
      <p className="text-xs text-slate-400">{new Date(task.createdAt).toLocaleString()}</p>
    </li>
  );
}

export function TaskList() {
  const { data, isPending, isError, error } = useQuery({ queryKey: ["tasks"], queryFn: listTasks });

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
