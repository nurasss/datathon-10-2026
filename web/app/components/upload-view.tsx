import { useRef, useState, type ReactEventHandler } from "react";
import { Icon } from "./ui-icon";

type UploadViewProps = {
  file: File | null;
  onFileSelect: (file: File | null) => void;
  onSubmit: ReactEventHandler<HTMLFormElement>;
  busy?: boolean;
  error?: string;
  maxBytes?: number;
  uploadUncertain?: boolean;
};

export function UploadView({
  file,
  onFileSelect,
  onSubmit,
  busy,
  error,
  maxBytes,
  uploadUncertain,
}: UploadViewProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  function select(files: FileList | null) {
    if (busy) return;
    const selected = files?.[0] ?? null;
    const message =
      (files?.length ?? 0) > 1
        ? "Выберите только одно видео."
        : selected && !/\.mov$/i.test(selected.name)
          ? "Выберите файл .mov."
          : selected?.size === 0
            ? "Файл пустой."
            : selected && maxBytes && selected.size > maxBytes
              ? "Размер видео превышает допустимый лимит."
              : "";
    setSelectionError(message);
    onFileSelect(message ? null : selected);
    if (input.current) input.current.value = "";
  }
  return (
    <>
      <div className="fv-page-heading">
        <p className="fv-eyebrow">НОВАЯ ПРОВЕРКА</p>
        <h1>Проверка видео кассовой зоны</h1>
        <p className="fv-page-description">
          Анализ видимых признаков возможных нарушений. Не является юридическим
          заключением.
        </p>
      </div>
      <div className="fv-workspace">
        <section
          className="fv-panel fv-upload-panel"
          aria-labelledby="upload-title"
        >
          <div className="fv-panel-heading">
            <span className="fv-section-number">01</span>
            <div>
              <h2 id="upload-title">Загрузка видео</h2>
              <p>Выберите одну запись для проверки</p>
            </div>
            <span className="fv-format-tag">.MOV</span>
          </div>
          <form onSubmit={onSubmit}>
            <label
              className={`fv-file-zone${file ? " fv-file-zone-selected" : ""}${dragging ? " fv-file-zone-dragging" : ""}`}
              htmlFor="video-file"
              onDragOver={(event) => {
                event.preventDefault();
                if (!busy) setDragging(true);
              }}
              onDragLeave={(event) => {
                if (
                  !event.currentTarget.contains(
                    event.relatedTarget as Node | null,
                  )
                )
                  setDragging(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                select(event.dataTransfer.files);
              }}
            >
              <span className="fv-upload-icon">
                <Icon name={file ? "video" : "upload"} />
              </span>
              <span className="fv-file-zone-title">
                {file ? "Видео выбрано" : "Добавьте видеозапись"}
              </span>
              <span className="fv-file-zone-description">
                {file
                  ? "Можно выбрать другой файл перед отправкой"
                  : "Перетащите файл сюда или выберите на устройстве"}
              </span>
              <span className="fv-file-picker">
                {file ? "Выбрать другой файл" : "Выбрать файл"}
              </span>
              <span className="fv-file-format">
                Поддерживаемый формат: .mov
              </span>
              <input
                id="video-file"
                ref={input}
                name="video"
                className="fv-file-input"
                type="file"
                accept=".mov"
                disabled={busy}
                onChange={(event) => select(event.target.files)}
              />
            </label>
            {file && (
              <div className="fv-selected-file">
                <span className="fv-selected-file-icon">
                  <Icon name="video" />
                </span>
                <div>
                  <p className="fv-file-name">{file.name}</p>
                  <p className="fv-file-size">
                    {(file.size / (1024 * 1024)).toLocaleString("ru-RU", {
                      maximumFractionDigits: 1,
                    })}{" "}
                    МБ
                  </p>
                </div>
                <button
                  type="button"
                  className="fv-secondary-button"
                  disabled={busy}
                  onClick={() => select(null)}
                  aria-label="Удалить выбранный файл"
                >
                  Удалить
                </button>
              </div>
            )}
            {maxBytes && (
              <p className="fv-upload-note">
                Максимальный размер: {Math.round(maxBytes / (1024 * 1024))} МБ.
              </p>
            )}
            {(selectionError || error) && (
              <p className="fv-inline-error" role="alert">
                {selectionError || error}
              </p>
            )}
            {uploadUncertain && (
              <p className="fv-inline-error">
                Неизвестно, принял ли сервер файл. Уточните состояние у
                оператора перед повторной загрузкой.
              </p>
            )}
            <button
              type="submit"
              className="fv-primary-button"
              disabled={busy || !file || uploadUncertain}
            >
              <span>{busy ? "Отправляем видео…" : "Анализировать видео"}</span>
              <Icon name="arrow" />
            </button>
          </form>
          <p className="fv-upload-note">
            <Icon name="clock" />
            Для конкурсной проверки результат должен быть получен в течение 5
            минут.
          </p>
        </section>
        <aside className="fv-guide" aria-labelledby="guide-title">
          <p className="fv-eyebrow">КАК ЭТО РАБОТАЕТ</p>
          <h2 id="guide-title">От записи к наблюдению</h2>
          <ol className="fv-guide-steps">
            <li>
              <span className="fv-guide-number">1</span>
              <div>
                <h3>Добавьте видео</h3>
                <p>Одна запись кассовой зоны в формате .mov.</p>
              </div>
            </li>
            <li>
              <span className="fv-guide-number">2</span>
              <div>
                <h3>Дождитесь обработки</h3>
                <p>Статус проверки обновится автоматически.</p>
              </div>
            </li>
            <li>
              <span className="fv-guide-number">3</span>
              <div>
                <h3>Изучите результат</h3>
                <p>
                  Проверьте события, кадры и источники. Перейдите к нужному
                  моменту в видео.
                </p>
              </div>
            </li>
          </ol>
          <div className="fv-guidance-note">
            <Icon name="shield" />
            <div>
              <h3>Решение остаётся за человеком</h3>
              <p>
                Сервис помогает обратить внимание на возможные нарушения.
                Результат не является юридическим заключением.
              </p>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
