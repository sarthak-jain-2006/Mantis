// A unified diff, coloured line by line. Kept simple on purpose; a richer viewer comes with Phase 8.
function lineStyle(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")) {
    return "text-slate-500";
  }
  if (line.startsWith("+")) return "bg-emerald-50 text-emerald-800";
  if (line.startsWith("-")) return "bg-red-50 text-red-800";
  if (line.startsWith("@@")) return "text-sky-700";
  return "text-slate-700";
}

export function DiffView({ diff, truncated }: { diff: string; truncated?: boolean }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  return (
    <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
      <pre aria-label="Diff" className="min-w-max p-2 font-mono text-xs leading-5">
        {lines.map((line, i) => (
          <div key={i} className={lineStyle(line)}>
            {line || " "}
          </div>
        ))}
      </pre>
      {truncated && <p className="border-t border-slate-200 p-2 text-xs text-amber-700">Diff cut short: it is very large.</p>}
    </div>
  );
}
