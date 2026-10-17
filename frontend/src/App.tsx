import { HealthStatus } from "./components/HealthStatus";
import { TaskForm } from "./components/TaskForm";
import { TaskList } from "./components/TaskList";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3 px-4 py-4">
          <h1 className="text-lg font-semibold text-slate-900">Mantis</h1>
          <HealthStatus />
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-6">
        <TaskForm />
        <TaskList />
      </main>
    </div>
  );
}
