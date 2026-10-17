import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { createTask } from "../api/client";

export function TaskForm() {
  const [text, setText] = useState("");
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: createTask,
    onSuccess: () => {
      setText("");
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (text.trim()) mutation.mutate(text);
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <label htmlFor="task-request" className="block text-sm font-medium text-slate-700">
        What should Mantis do?
      </label>
      <textarea
        id="task-request"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        maxLength={4000}
        placeholder="Add pagination to the products API and write tests"
        className="w-full rounded-md border border-slate-300 p-2 text-sm focus:border-slate-500 focus:outline-none"
      />
      <div className="flex items-center justify-between gap-4">
        <p role="alert" className="text-sm text-red-600">
          {mutation.isError ? mutation.error.message : null}
        </p>
        <button
          type="submit"
          disabled={!text.trim() || mutation.isPending}
          className="shrink-0 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {mutation.isPending ? "Planning…" : "Submit task"}
        </button>
      </div>
    </form>
  );
}
