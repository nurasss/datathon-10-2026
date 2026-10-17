import { parseResult } from "../lib/contracts";
export type { Violation } from "../lib/contracts";
// Retained for the unused legacy mock module; real responses use lib/contracts.ts.
export type CheckResult = {
  job_id: string;
  status: "done" | "pending" | "error";
  verdict: "violation" | "no_violation";
  violations?: import("../lib/contracts").Violation[];
};
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
async function request(path: string, init: RequestInit = {}) {
  let response: Response;
  try {
    response = await fetch(`/ui/api/${path}`, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("Нет связи с сервером. Проверьте подключение.", 0);
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new ApiError("Сервер вернул некорректный ответ.", 502);
  }
  if (!response.ok)
    throw new ApiError(
      typeof data.error === "string" ? data.error : "Запрос не выполнен.",
      response.status,
    );
  return data;
}
export async function getSession(signal?: AbortSignal): Promise<{
  authenticated: boolean;
  configured: boolean;
  max_upload_bytes?: number;
}> {
  return request("session", { signal });
}
export async function login(
  apiKey: string,
): Promise<{ authenticated: boolean; max_upload_bytes: number }> {
  return request("session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
}
export async function logout() {
  return request("session", { method: "DELETE" });
}
export async function post(
  file: File,
  signal?: AbortSignal,
): Promise<{ job_id: string; started_at: number }> {
  const data = new FormData();
  data.append("file", file);
  return request("analyze", { method: "POST", body: data, signal });
}
export async function get(jobId: string, signal?: AbortSignal) {
  const data = await request(`jobs/${encodeURIComponent(jobId)}`, { signal });
  return {
    job: parseResult(data.job, jobId),
    started_at: typeof data.started_at === "number" ? data.started_at : null,
  };
}
