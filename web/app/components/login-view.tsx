import { useState } from "react";
type Props = {
  configured: boolean;
  error: string;
  onLogin: (key: string) => Promise<void>;
};
export function LoginView({ configured, error, onLogin }: Props) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  async function submit(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      await onLogin(key);
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      setKey("");
      setBusy(false);
    }
  }
  return (
    <section className="fv-panel fv-login">
      <p className="fv-eyebrow">ДОСТУП К СЕРВИСУ</p>
      <h1>Проверка видеозаписей</h1>
      <p>
        Введите ключ доступа, выданный командой, чтобы начать проверку видео.
      </p>
      {!configured && (
        <p className="fv-inline-error">
          Сервис ещё не подключён. Обратитесь к участнику команды, который
          отвечает за настройку сервера.
        </p>
      )}
      <form onSubmit={submit}>
        <label htmlFor="access-key">Ключ доступа</label>
        <input
          id="access-key"
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoComplete="off"
          maxLength={512}
          required
          disabled={busy || !configured}
        />
        <button
          className="fv-primary-button"
          disabled={!configured || busy || !key.trim()}
        >
          {busy ? "Проверяем доступ…" : "Продолжить"}
        </button>
      </form>
      {(failure || error) && (
        <p role="alert" className="fv-inline-error">
          {failure || error}
        </p>
      )}
      <p className="fv-muted">
        Ключ не сохраняется в браузерном хранилище. После перезапуска фронтенда
        потребуется войти снова.
      </p>
    </section>
  );
}
