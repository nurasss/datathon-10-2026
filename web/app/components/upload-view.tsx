import type { ChangeEventHandler, ReactEventHandler } from "react";
import { Icon } from "./ui-icon";

type UploadViewProps = {
  file: File | null;
  onFileChange: ChangeEventHandler<HTMLInputElement>;
  onSubmit: ReactEventHandler<HTMLFormElement>;
};

export function UploadView({ file, onFileChange, onSubmit }: UploadViewProps) {
  return (
    <>
      <div className="fv-page-heading">
        <p className="fv-eyebrow">НОВАЯ ПРОВЕРКА</p>
        <h1>Проверка видео кассовой зоны</h1>
        <p className="fv-page-description">
          Загрузите видео кассовой зоны, чтобы проверить видимые признаки
          возможных нарушений.
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
              className={`fv-file-zone${file ? " fv-file-zone-selected" : ""}`}
              htmlFor="video-file"
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
                  : "Выберите файл на вашем устройстве"}
              </span>
              <span className="fv-file-picker">
                {file ? "Выбрать другой файл" : "Выбрать файл"}
              </span>
              <span className="fv-file-format">
                Поддерживаемый формат: .mov
              </span>
              <input
                id="video-file"
                name="video"
                className="fv-file-input"
                type="file"
                onChange={onFileChange}
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
                <Icon name="check" className="fv-file-check" />
              </div>
            )}
            <button type="submit" className="fv-primary-button">
              <span>Анализировать видео</span>
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
                <p>Проверьте найденные события, их время и описания.</p>
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
