import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api/client";

type State = "up" | "down" | "unknown";

const DOT: Record<State, string> = {
  up: "bg-emerald-500",
  down: "bg-red-500",
  unknown: "bg-slate-300",
};

export function HealthStatus() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 10_000,
    retry: false,
  });

  // If the backend is down we can't know about the services behind it.
  const backend: State = isError ? "down" : data ? "up" : "unknown";
  const behind = (up: boolean | undefined): State =>
    backend !== "up" || up === undefined ? "unknown" : up ? "up" : "down";

  const services: [string, State][] = [
    ["Backend", backend],
    ["Database", behind(data?.services.database)],
    ["AI service", behind(data?.services.aiService)],
  ];

  return (
    <ul aria-label="Service health" className="flex flex-wrap gap-2">
      {services.map(([name, state]) => (
        <li
          key={name}
          className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm text-slate-700"
        >
          <span aria-hidden className={`h-2 w-2 rounded-full ${DOT[state]}`} />
          {name}
          <span className="text-slate-400">{state}</span>
        </li>
      ))}
    </ul>
  );
}
