"use client";
import { useState } from "react";
import { AppShell } from "./components/app-shell";
import { UploadView } from "./components/upload-view";
import { JobView } from "./components/job-view";
import type { Violation } from "../lib/contracts";

type Screen = "upload" | "processing" | "result" | "clear" | "error";
const screens: [Screen, string][] = [
  ["upload", "Загрузка"],
  ["processing", "Ожидание"],
  ["result", "Результат"],
  ["clear", "Без событий"],
  ["error", "Ошибка"],
];
const examples: Violation[] = [
  {
    type: "Пример события для проверки",
    timecode_sec: 12,
    description:
      "Здесь будет описание наблюдения, полученное от сервиса анализа. Это вымышленный пример для просмотра оформления.",
    evidence_frames: [],
  },
  {
    type: "Пример второго события",
    timecode_sec: 63,
    description:
      "Каждое событие содержит время, описание, кадры и нормативное основание, если оно передано сервером.",
    evidence_frames: [],
  },
];

export default function DesignPreview() {
  const [screen, setScreen] = useState<Screen>("upload");
  const [file, setFile] = useState<File | null>(null);
  return (
    <AppShell>
      <section className="fv-preview-bar" aria-label="Просмотр дизайна">
        <div>
          <strong>Просмотр дизайна</strong>
          <p>Вымышленные примеры. Видео не отправляются и не анализируются.</p>
        </div>
        <nav aria-label="Экраны интерфейса">
          {screens.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className="fv-secondary-button"
              aria-pressed={screen === value}
              onClick={() => setScreen(value)}
            >
              {label}
            </button>
          ))}
        </nav>
      </section>
      {screen === "upload" ? (
        <UploadView
          file={file}
          maxBytes={104857600}
          onFileChange={(event) => setFile(event.target.files?.[0] ?? null)}
          onSubmit={(event) => {
            event.preventDefault();
            setScreen("processing");
          }}
        />
      ) : (
        <>
          <div className="fv-toolbar">
            <button
              type="button"
              className="fv-secondary-button"
              onClick={() => setScreen("upload")}
            >
              К загрузке
            </button>
            <span className="fv-muted">Пример интерфейса · demo.mov</span>
          </div>
          <JobView
            preview
            key={screen}
            jobId="DEMO"
            status={
              screen === "processing"
                ? "processing"
                : screen === "error"
                  ? "failed"
                  : "done"
            }
            verdict={screen === "clear" ? "no_violation" : "violation"}
            violations={screen === "result" ? examples : []}
            duration={94}
          />
          {screen === "processing" && (
            <button
              type="button"
              className="fv-primary-button fv-preview-next"
              onClick={() => setScreen("result")}
            >
              Показать пример результата
            </button>
          )}
        </>
      )}
    </AppShell>
  );
}
