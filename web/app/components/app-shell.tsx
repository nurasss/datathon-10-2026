import type { ReactNode } from "react";
import { Icon } from "./ui-icon";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="fv-app">
      <a className="fv-skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      <header className="fv-header">
        <div className="fv-header-inner">
          <div className="fv-brand" aria-label="ForteVision">
            <span className="fv-brand-mark">
              <Icon name="shield" />
            </span>
            <span>
              Forte<span className="fv-brand-accent">Vision</span>
            </span>
          </div>
          <span className="fv-header-divider" />
          <span className="fv-header-context">Проверка видеозаписей</span>
          <span className="fv-header-label">Кассовая дисциплина</span>
        </div>
      </header>
      <main id="main-content" className="fv-main">
        {children}
      </main>
      <footer className="fv-footer">
        <span>
          ForteVision <span className="fv-footer-separator">/</span> Проверка
          кассовых операций
        </span>
        <span>Результат проверяет специалист</span>
      </footer>
    </div>
  );
}
