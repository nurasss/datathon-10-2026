import type { Violation } from "../api";
import { Icon } from "./ui-icon";

type JobViewProps = {
  jobId: string;
  status: string | null;
  verdict: string | null;
  violations: Violation[] | null;
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

export function JobView({ jobId, status, verdict, violations }: JobViewProps) {
  const done = status === "done";
  const failed = status === "error";
  const found = done && verdict === "violation";
  const clear = done && verdict === "no_violation";
  const statusText = done
    ? "Завершено"
    : failed
      ? "Ошибка обработки"
      : status === null
        ? "Ожидание статуса"
        : status === "pending"
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
            <h2>Идёт обработка видео</h2>
            <p>
              Это может занять несколько минут.
              <br />
              Результат появится здесь после завершения проверки.
            </p>
            <div className="fv-wait-label">
              <Icon name="clock" />
              Ожидаем ответ сервиса
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
            {found && violations && (
              <div className="fv-events">
                <div className="fv-events-heading">
                  <h2>События для проверки</h2>
                  <span className="fv-event-count">{violations.length}</span>
                </div>
                <ol className="fv-event-list">
                  {violations.map((violation, index) => (
                    <li className="fv-event" key={index}>
                      <span className="fv-event-number">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <div className="fv-event-content">
                        <div className="fv-event-heading">
                          <h3>{violation.type}</h3>
                          <span
                            className="fv-timecode"
                            aria-label={`Таймкод: ${violation.timecode_sec} секунды`}
                          >
                            <Icon name="clock" />
                            {displayTime(violation.timecode_sec)}
                          </span>
                        </div>
                        <p className="fv-event-label">Описание события</p>
                        <p className="fv-event-description">
                          {violation.description}
                        </p>
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
              Не удалось получить результат анализа видео.
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
