"use client";
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  get,
  getConfig,
  getSession,
  login,
  logout,
  post,
} from "../api";
import type { CheckResult, JobMeta } from "../../lib/contracts";
import { validJobId } from "../../lib/contracts";
import { AppShell } from "./app-shell";
import { UploadView } from "./upload-view";
import { JobView } from "./job-view";
import { LoginView } from "./login-view";

export default function Home() {
  const [auth, setAuth] = useState<"loading" | "login" | "ready">("loading");
  const [configured, setConfigured] = useState(false);
  const [maxBytes, setMaxBytes] = useState(104857600);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [meta, setMeta] = useState<JobMeta | null>(null);
  const [deadline, setDeadline] = useState(300);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [retry, setRetry] = useState(0);
  const [pollError, setPollError] = useState("");
  const [uploadUncertain, setUploadUncertain] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getSession(controller.signal)
      .then(async (data) => {
        const config = data.configured
          ? await getConfig(controller.signal)
          : null;
        if (controller.signal.aborted) return;
        setConfigured(data.configured);
        setMaxBytes(config?.max_upload_bytes ?? 104857600);
        setDeadline(config?.deadline_sec ?? 300);
        setAuth(data.authenticated ? "ready" : "login");
        const id = new URLSearchParams(window.location.search).get("job");
        if (data.authenticated && validJobId(id)) setJobId(id);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(e.message);
          setAuth("login");
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!jobId || auth !== "ready") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function poll() {
      try {
        const response = await get(jobId!, controller.signal);
        if (controller.signal.aborted) return;
        failures = 0;
        setPollError("");
        setResult(response.job);
        setMeta(response.meta);
        setStartedAt(response.started_at);
        if (response.job.status === "processing")
          timer = setTimeout(poll, 2000);
      } catch (e) {
        if (controller.signal.aborted) return;
        const failure = e as ApiError;
        if (failure.status === 401) {
          setAuth("login");
          setError(failure.message);
          setResult(null);
          return;
        }
        setPollError(failure.message);
        if (![403, 404, 410].includes(failure.status) && ++failures < 3)
          timer = setTimeout(poll, 2000 * failures);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, auth, retry]);

  useEffect(() => {
    if (!startedAt || result?.status !== "processing") return;
    const update = () =>
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [startedAt, result?.status]);

  async function signIn(key: string) {
    await login(key);
    const config = await getConfig();
    setAuth("ready");
    setConfigured(true);
    setMaxBytes(config.max_upload_bytes);
    setDeadline(config.deadline_sec);
    setError("");
    setJobId(null);
    setResult(null);
    setMeta(null);
    window.history.replaceState(null, "", window.location.pathname);
  }
  async function signOut() {
    try {
      await logout();
      setAuth("login");
      setFile(null);
      setJobId(null);
      setResult(null);
      setError("");
      window.history.replaceState(null, "", window.location.pathname);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function newCheck() {
    setFile(null);
    setJobId(null);
    setResult(null);
    setMeta(null);
    setStartedAt(null);
    setPollError("");
    setError("");
    setElapsed(0);
    setUploadUncertain(false);
    window.history.replaceState(null, "", window.location.pathname);
  }
  async function upload(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    if (!file) {
      setError("Выберите видео в формате .mov.");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    setUploadUncertain(false);
    try {
      const accepted = await post(file);
      setStartedAt(accepted.started_at);
      setJobId(accepted.job_id);
      setResult({ job_id: accepted.job_id, status: "processing" });
      window.history.replaceState(
        null,
        "",
        `?job=${encodeURIComponent(accepted.job_id)}`,
      );
    } catch (e) {
      const failure = e as ApiError;
      setError(failure.message);
      if (failure.status === 401) setAuth("login");
      if ([0, 502, 503].includes(failure.status)) setUploadUncertain(true);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  function choose(selected: File | null) {
    setError("");
    setUploadUncertain(false);
    if (
      selected &&
      (!/\.mov$/i.test(selected.name) ||
        selected.size === 0 ||
        selected.size > maxBytes)
    ) {
      setError(
        !/\.mov$/i.test(selected.name)
          ? "Выберите файл .mov."
          : selected.size === 0
            ? "Файл пустой."
            : "Размер видео превышает допустимый лимит.",
      );

      setFile(null);
      return;
    }
    setFile(selected);
  }
  return (
    <AppShell
      actions={
        auth === "ready" ? (
          <button
            type="button"
            className="fv-header-button"
            onClick={signOut}
            disabled={busy}
          >
            Выйти
          </button>
        ) : undefined
      }
    >
      {auth === "loading" ? (
        <p role="status">Проверяем доступ…</p>
      ) : auth === "login" ? (
        <LoginView configured={configured} error={error} onLogin={signIn} />
      ) : (
        <>
          {jobId ? (
            <>
              <div className="fv-toolbar">
                <button className="fv-secondary-button" onClick={newCheck}>
                  Новая проверка
                </button>
                <span className="fv-muted">{meta?.filename ?? file?.name}</span>
              </div>
              {error && (
                <p className="fv-inline-error" role="alert">
                  {error}
                </p>
              )}
              {pollError && (
                <div className="fv-inline-error" role="alert">
                  <p>{pollError}</p>
                  <button
                    className="fv-secondary-button"
                    onClick={() => setRetry((x) => x + 1)}
                  >
                    Повторить запрос статуса
                  </button>
                </div>
              )}
              {elapsed >= deadline && result?.status === "processing" && (
                <p className="fv-inline-error" role="status">
                  Превышен лимит 5 минут. Продолжаем запрашивать фактический
                  результат; проверка не считается успешно завершённой.
                </p>
              )}
              <JobView
                key={jobId}
                jobId={jobId}
                status={result?.status ?? null}
                verdict={result?.status === "done" ? result.verdict : null}
                violations={
                  result?.status === "done" ? result.violations : null
                }
                errorMessage={
                  result?.status === "failed"
                    ? (meta?.error_message ?? result.error)
                    : undefined
                }
                duration={meta?.duration_sec ?? undefined}
                stage={meta?.stage}
                videoUrl={meta?.video_url}
                filename={meta?.filename ?? file?.name}
                deadline={deadline}
                connectionLost={!!pollError}
              />
            </>
          ) : (
            <UploadView
              file={file}
              onFileSelect={choose}
              onSubmit={upload}
              busy={busy}
              error={error}
              maxBytes={maxBytes}
              uploadUncertain={uploadUncertain}
            />
          )}
        </>
      )}
    </AppShell>
  );
}
