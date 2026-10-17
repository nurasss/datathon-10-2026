import type { Violation } from "../api";
import { Icon } from "./ui-icon";
import { useRef, useState } from "react";
import { safeRuleUrl } from "../../lib/contracts";

type JobViewProps = {
  jobId: string;
  status: string | null;
  verdict: string | null;
  violations: Violation[] | null;
  errorMessage?: string;
  duration?: number;
  connectionLost?: boolean;
  preview?: boolean;
};

function displayTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const tenths = Math.round(seconds * 10);
  return `${Math.floor(tenths / 600)
    .toString()
    .padStart(2, "0")}:${Math.floor((tenths % 600) / 10)
    .toString()
    .padStart(2, "0")}.${tenths % 10}`;
}

export function JobView({
  jobId,
  status,
  verdict,
  violations,
  errorMessage,
  duration,
  connectionLost,
  preview = false,
}: JobViewProps) {
  const player = useRef<HTMLVideoElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const [videoError, setVideoError] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  function seek(time: number, index: number) {
    setActive(index);
    const video = player.current;
    if (!video || videoError) return;
    video.scrollIntoView({ behavior: "smooth", block: "center" });
    if (video.readyState < 1) {
      pendingSeek.current = time;
      return;
    }
    video.currentTime = Math.min(
      time,
      Number.isFinite(video.duration) ? video.duration : time,
    );
    void video.play().catch(() => {});
  }
  const done = status === "done";
  const failed = status === "failed";
  const found = done && verdict === "violation";
  const clear = done && verdict === "no_violation";
  const statusText = done
    ? "Завершено"
    : failed
      ? "Ошибка обработки"
      : status === null
        ? "Ожидание статуса"
        : status === "processing"
          ? "Обработка"
          : status;

  return (
    <>
      <div className="fv-page-heading">
        <p className="fv-eyebrow">ПРОВЕРКА ВИДЕО</p>
        <h1>
          {done
            ? "Результат проверки"
            : failed
              ? "Не удалось завершить проверку"
              : "Проверяем видеозапись"}
        </h1>
        <p className="fv-page-description">
          {done
            ? "Ознакомьтесь с результатом и проверьте наблюдения по исходной записи."
            : failed
              ? "Результат анализа не получен. Это не означает отсутствие нарушений."
              : "Дождитесь результата. Статус на этой странице обновляется автоматически."}
        </p>
      </div>
      <section className="fv-panel fv-job-panel" aria-label="Результат анализа">
        <div className="fv-job-meta">
          <span>
            Задача <code>{jobId}</code>
          </span>
          <span className={`fv-status-pill${failed ? " fv-status-error" : ""}`}>
            <span className="fv-status-dot" />
            {statusText}
          </span>
        </div>
        {!done && !failed && (
          <div className="fv-processing" role="status">
            <div className="fv-processing-symbol">
              <span className="fv-spinner" />
              <Icon name="video" />
            </div>
            <h2>
              {connectionLost
                ? "Не удаётся обновить статус"
                : "Идёт обработка видео"}
            </h2>
            <p>
              Это может занять несколько минут.
              <br />
              {connectionLost
                ? "Текущий результат неизвестен. Повторите запрос статуса."
                : "Результат появится здесь после завершения проверки."}
            </p>
            <div className="fv-wait-label">
              <Icon name="clock" />
              {connectionLost
                ? "Связь с сервисом прервана"
                : "Ожидаем ответ сервиса"}
            </div>
          </div>
        )}
        {done && (
          <>
            <div
              className={`fv-verdict ${found ? "fv-verdict-warning" : clear ? "fv-verdict-clear" : "fv-verdict-neutral"}`}
              role="status"
            >
              <span className="fv-verdict-icon">
                <Icon name={clear ? "check" : "alert"} />
              </span>
              <div>
                <p className="fv-eyebrow">РЕЗУЛЬТАТ АНАЛИЗА</p>
                <h2>
                  {found
                    ? "Обнаружены признаки возможных нарушений"
                    : clear
                      ? "Нарушение не обнаружено"
                      : "Проверка завершена"}
                </h2>
                <p>
                  {found
                    ? "Найденные события требуют проверки специалистом."
                    : clear
                      ? "По визуально доступным признакам. Это не подтверждает отсутствие нарушений, невидимых на записи."
                      : `Ответ сервиса: ${verdict ?? "—"}`}
                </p>
              </div>
            </div>
            <section className="fv-video-section" aria-label="Исходное видео">
              <div className="fv-events-heading">
                <h2>Исходная видеозапись</h2>
                {duration != null && (
                  <span className="fv-timecode">{displayTime(duration)}</span>
                )}
              </div>
              {preview ? (
                <div className="fv-preview-video">
                  <Icon name="video" />
                  <strong>Область видеоплеера</strong>
                  <span>Здесь будет исходная запись</span>
                  <span className="fv-timecode">00:00 / 01:34</span>
                </div>
              ) : (
                <>
                  <video
                    ref={player}
                    controls
                    playsInline
                    preload="metadata"
                    src={`/ui/api/jobs/${encodeURIComponent(jobId)}/video`}
                    onError={() => setVideoError(true)}
                    onLoadedMetadata={() => {
                      setVideoError(false);
                      if (pendingSeek.current != null && player.current) {
                        player.current.currentTime = pendingSeek.current;
                        pendingSeek.current = null;
                        void player.current.play().catch(() => {});
                      }
                    }}
                    aria-label="Видеозапись кассовой зоны"
                  />
                  {videoError ? (
                    <div className="fv-inline-error" role="alert">
                      <p>
                        Видео не удалось воспроизвести. Возможно, файл
                        недоступен, сессия истекла или браузер не поддерживает
                        кодек MOV. Кадры и описание доступны ниже.
                      </p>
                      <button
                        className="fv-secondary-button"
                        onClick={() => {
                          setVideoError(false);
                          player.current?.load();
                        }}
                      >
                        Повторить загрузку видео
                      </button>
                    </div>
                  ) : (
                    <p className="fv-muted">
                      Нажмите на время события или доказательный кадр, чтобы
                      перейти к этому моменту.
                    </p>
                  )}
                </>
              )}
            </section>
            {found && violations && (
              <div className="fv-events">
                <div className="fv-events-heading">
                  <h2>События для проверки</h2>
                  <span className="fv-event-count">{violations.length}</span>
                </div>
                <ol className="fv-event-list">
                  {violations.map((violation, index) => (
                    <li
                      className={`fv-event${active === index ? " fv-event-active" : ""}`}
                      key={index}
                    >
                      <span className="fv-event-number">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <div className="fv-event-content">
                        <div className="fv-event-heading">
                          <h3>{violation.type}</h3>
                          <button
                            type="button"
                            onClick={() => seek(violation.timecode_sec, index)}
                            disabled={videoError || preview}
                            className="fv-timecode"
                            aria-label={`Таймкод: ${violation.timecode_sec} секунды`}
                          >
                            <Icon name="clock" />
                            {displayTime(violation.timecode_sec)}
                          </button>
                        </div>
                        <p className="fv-event-label">Описание события</p>
                        <p className="fv-event-description">
                          {violation.description}
                        </p>
                        <div
                          className="fv-evidence"
                          aria-label="Доказательные кадры"
                        >
                          {preview &&
                            [1, 2, 3].map((number) => (
                              <div className="fv-preview-frame" key={number}>
                                <Icon name="video" />
                                <span>Кадр {number}</span>
                              </div>
                            ))}
                          {violation.evidence_frames.map(
                            (frame, frameIndex) => (
                              <figure key={frameIndex}>
                                <button
                                  type="button"
                                  onClick={() =>
                                    seek(frame.timestamp_sec, index)
                                  }
                                  disabled={videoError}
                                  aria-label={`Перейти к кадру на ${frame.timestamp_sec} секунде`}
                                >
                                  <EvidenceImage
                                    base64={frame.image_base64}
                                    time={frame.timestamp_sec}
                                  />
                                </button>
                                <figcaption>
                                  {displayTime(frame.timestamp_sec)}
                                </figcaption>
                              </figure>
                            ),
                          )}
                        </div>
                        <div className="fv-rule">
                          <h4>Нормативное основание</h4>
                          {violation.rule &&
                          (violation.rule.act_name ||
                            violation.rule.act_number) ? (
                            <>
                              <p>
                                {violation.rule.act_name}{" "}
                                {violation.rule.act_number}
                                {violation.rule.paragraph &&
                                  ` · Пункт ${violation.rule.paragraph}`}
                              </p>
                              {violation.rule.quote && (
                                <blockquote>{violation.rule.quote}</blockquote>
                              )}
                              {safeRuleUrl(violation.rule.source_url) ? (
                                <a
                                  href={safeRuleUrl(violation.rule.source_url)!}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  Открыть нормативный источник ↗
                                </a>
                              ) : (
                                <p className="fv-muted">
                                  Корректная ссылка на источник не передана.
                                </p>
                              )}
                            </>
                          ) : (
                            <p className="fv-muted">
                              Нормативный источник не передан сервером.
                              Основание требует проверки.
                            </p>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </>
        )}
        {failed && (
          <div className="fv-failure" role="alert">
            <span className="fv-verdict-icon">
              <Icon name="alert" />
            </span>
            <h2>Ошибка обработки</h2>
            <p>
              {errorMessage || "Не удалось получить результат анализа видео."}
              <br />
              Причину необходимо уточнить у оператора сервиса.
            </p>
          </div>
        )}
      </section>
      <p className="fv-result-note">
        <Icon name="shield" />
        Результат — подсказка для проверки человеком, а не юридическое или
        дисциплинарное заключение.
      </p>
    </>
  );
}

function EvidenceImage({ base64, time }: { base64: string; time: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <span className="fv-image-error">Кадр недоступен</span>;
  // Original JPEG evidence must not be sent to an image optimization service.
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`data:image/jpeg;base64,${base64}`}
      alt={`Кадр на ${time} секунде`}
      onError={() => setFailed(true)}
    />
  );
}
