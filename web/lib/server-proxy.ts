import "server-only";
import { randomBytes, randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { parseResult, validJobId } from "./contracts";

type Session = { key: string; expires: number; jobs: Map<string, number> };
const state = globalThis as typeof globalThis & {
  fvSessions?: Map<string, Session>;
};
const sessions = (state.fvSessions ??= new Map<string, Session>());
const COOKIE = "fv_web_session";
const TTL = 8 * 60 * 60 * 1000;
const noStore = {
  "Cache-Control": "private, no-store",
  "X-Content-Type-Options": "nosniff",
};

function settings() {
  const raw = process.env.FV_BACKEND_URL;
  if (!raw) throw new Error("missing configuration");
  const url = new URL(raw);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new Error("invalid backend URL");
  const max = Number(process.env.FV_MAX_UPLOAD_BYTES ?? 104857600);
  if (!Number.isSafeInteger(max) || max < 1)
    throw new Error("invalid upload limit");
  return { base: url.origin, max };
}
function json(body: unknown, status = 200) {
  return NextResponse.json(body, { status, headers: noStore });
}
function error(status: number, message: string) {
  return json({ error: message }, status);
}
function session(request: NextRequest) {
  const now = Date.now();
  for (const [id, value] of sessions)
    if (value.expires <= now) sessions.delete(id);
  return sessions.get(request.cookies.get(COOKIE)?.value ?? "");
}
function sameOrigin(request: NextRequest) {
  const expected = process.env.FV_APP_ORIGIN || new URL(request.url).origin;
  return (
    request.headers.get("origin") === expected &&
    !["cross-site", "same-site"].includes(
      request.headers.get("sec-fetch-site") ?? "",
    )
  );
}
async function upstream(path: string, key: string, init: RequestInit = {}) {
  return fetch(settings().base + path, {
    ...init,
    headers: {
      ...Object.fromEntries(new Headers(init.headers)),
      "X-API-Key": key,
    },
    cache: "no-store",
    redirect: "manual",
    signal: init.signal ?? AbortSignal.timeout(20_000),
  });
}
function upstreamError(status: number) {
  const messages: Record<number, string> = {
    401: "Ключ недействителен. Войдите снова.",
    403: "Доступ запрещён.",
    404: "Задача или видео больше недоступны.",
    410: "Срок хранения результата истёк.",
    413: "Файл превышает допустимый размер.",
    415: "Сервис принимает видео в формате .mov.",
    422: "Видео не удалось прочитать. Выберите другой файл.",
    429: "Очередь заполнена. Повторите позже.",
  };
  return error(
    messages[status] ? status : 502,
    messages[status] ?? "Сервис временно недоступен. Повторите запрос позже.",
  );
}
export async function handle(request: NextRequest, parts: string[]) {
  try {
    const method = request.method;
    if (["POST", "DELETE"].includes(method) && !sameOrigin(request))
      return error(403, "Запрос должен быть отправлен с этого сайта.");
    if (parts.join("/") === "session") {
      if (method === "GET") {
        let config;
        try {
          config = settings();
        } catch {
          return json({ authenticated: false, configured: false });
        }
        return json({
          authenticated: !!session(request),
          configured: true,
          max_upload_bytes: config.max,
        });
      }
      if (method === "DELETE") {
        sessions.delete(request.cookies.get(COOKIE)?.value ?? "");
        const response = json({ ok: true });
        response.cookies.set(COOKIE, "", { path: "/", maxAge: 0 });
        return response;
      }
      if (method !== "POST") return error(405, "Метод не поддерживается.");
      try {
        settings();
      } catch {
        return error(
          503,
          "Адрес бэкенда ещё не настроен. Обратитесь к участнику команды.",
        );
      }
      if (Number(request.headers.get("content-length") ?? 0) > 2048)
        return error(413, "Слишком большой запрос.");
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.byteLength;
          if (length > 2048) {
            await reader.cancel();
            return error(413, "Слишком большой запрос.");
          }
          chunks.push(value);
        }
      }
      const text = Buffer.concat(chunks).toString("utf8");
      let body;
      try {
        body = JSON.parse(text);
      } catch {
        return error(400, "Некорректный запрос.");
      }
      if (
        typeof body?.api_key !== "string" ||
        !body.api_key.trim() ||
        body.api_key.length > 512 ||
        /[\r\n]/.test(body.api_key)
      )
        return error(400, "Введите корректный ключ доступа.");
      const key = body.api_key.trim();
      // A random nonexistent job validates the key without retrieving any video result.
      const probe = await upstream(
        `/api/v1/jobs/${randomUUID().replaceAll("-", "")}`,
        key,
      );
      await probe.body?.cancel();
      if (probe.status !== 404) return upstreamError(probe.status);
      session(request); // expire old entries before checking capacity
      if (sessions.size >= 1000)
        return error(503, "Слишком много сессий. Повторите позже.");
      sessions.delete(request.cookies.get(COOKIE)?.value ?? "");
      const token = randomBytes(32).toString("base64url");
      sessions.set(token, { key, expires: Date.now() + TTL, jobs: new Map() });
      const response = json({
        authenticated: true,
        max_upload_bytes: settings().max,
      });
      response.cookies.set(COOKIE, token, {
        httpOnly: true,
        secure: (process.env.FV_APP_ORIGIN || request.url).startsWith("https:"),
        sameSite: "strict",
        path: "/",
        maxAge: TTL / 1000,
      });
      return response;
    }
    const auth = session(request);
    if (!auth) return error(401, "Сессия истекла. Войдите снова.");
    if (parts.join("/") === "analyze" && method === "POST") {
      const type = request.headers.get("content-type") ?? "";
      if (
        !type.startsWith("multipart/form-data;") ||
        !type.includes("boundary=")
      )
        return error(415, "Ожидается загрузка файла.");
      const started = Date.now();
      const limit = settings().max + 64 * 1024; // backend multipart framing allowance
      if (Number(request.headers.get("content-length") ?? 0) > limit)
        return error(413, "Файл превышает допустимый размер.");
      if (!request.body) return error(400, "Выберите видео.");
      let bytes = 0;
      let tooLarge = false;
      const body = request.body.pipeThrough(
        new TransformStream<Uint8Array, Uint8Array>({
          transform(chunk, controller) {
            bytes += chunk.byteLength;
            if (bytes > limit) {
              tooLarge = true;
              controller.error(new Error("upload limit"));
            } else controller.enqueue(chunk);
          },
        }),
      );
      let response;
      try {
        response = await upstream("/api/v1/analyze", auth.key, {
          method: "POST",
          headers: { "Content-Type": type },
          body,
          duplex: "half",
          signal: AbortSignal.any([
            request.signal,
            AbortSignal.timeout(120_000),
          ]),
        } as RequestInit & { duplex: "half" });
      } catch {
        return error(
          tooLarge ? 413 : 502,
          tooLarge
            ? "Файл превышает допустимый размер."
            : "Связь прервалась при отправке. Задача могла быть создана. Не повторяйте загрузку автоматически.",
        );
      }
      if (!response.ok) {
        await response.body?.cancel();
        return upstreamError(response.status);
      }
      const data = await response.json();
      if (!validJobId(data.job_id))
        return error(502, "Сервис не вернул корректный идентификатор задачи.");
      if (auth.jobs.size >= 100)
        auth.jobs.delete(auth.jobs.keys().next().value!);
      auth.jobs.set(data.job_id, started);
      return json({ job_id: data.job_id, started_at: started }, 202);
    }
    if (
      parts[0] === "jobs" &&
      validJobId(parts[1]) &&
      (parts.length === 2 || (parts.length === 3 && parts[2] === "video")) &&
      ["GET", "HEAD"].includes(method)
    ) {
      const id = parts[1];
      if (!auth.jobs.has(id))
        return error(404, "Задача недоступна в этой сессии.");
      if (parts[2] === "video") {
        const headers: Record<string, string> = {};
        const range = request.headers.get("range");
        if (range) {
          if (!/^bytes=\d*-\d*$/.test(range) || range === "bytes=-")
            return error(416, "Некорректный диапазон видео.");
          headers.Range = range;
        }
        const response = await upstream(`/api/v1/jobs/${id}/video`, auth.key, {
          headers,
          signal: request.signal,
        });
        if (![200, 206, 416].includes(response.status)) {
          await response.body?.cancel();
          return upstreamError(response.status);
        }
        const outgoing = new Headers(noStore);
        for (const name of [
          "content-type",
          "content-length",
          "content-range",
          "accept-ranges",
        ]) {
          const value = response.headers.get(name);
          if (value) outgoing.set(name, value);
        }
        if (method === "HEAD") await response.body?.cancel();
        return new Response(method === "HEAD" ? null : response.body, {
          status: response.status,
          headers: outgoing,
        });
      }
      const response = await upstream(`/api/v1/jobs/${id}/details`, auth.key, {
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(20_000)]),
      });
      if (!response.ok) {
        await response.body?.cancel();
        return upstreamError(response.status);
      }
      try {
        return json({
          job: parseResult(await response.json(), id),
          started_at: auth.jobs.get(id),
        });
      } catch {
        return error(
          502,
          "Сервис вернул некорректный результат. Вердикт не показан.",
        );
      }
    }
    return error(404, "Адрес не найден.");
  } catch {
    return error(
      503,
      "Не удалось связаться с сервисом. Проверьте настройку бэкенда.",
    );
  }
}
