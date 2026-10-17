import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ForteVision — проверка видеозаписей",
  description:
    "Проверка видимых признаков возможных нарушений кассовой дисциплины.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
