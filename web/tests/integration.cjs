/* Synthetic data only. Run after next build; see docs/integration.md. */
/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node CommonJS runner with an optional external Playwright installation. */
const assert = require("node:assert/strict");
const http = require("node:http");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const origin = "http://127.0.0.1:3200";
const key = "synthetic-test-key";
let jpeg,
  video,
  uploads = 0;
const jobs = new Map();
const backend = http.createServer(async (req, res) => {
  const json = (code, value) => {
    res.writeHead(code, { "Content-Type": "application/json" });
    res.end(JSON.stringify(value));
  };
  if (req.headers["x-api-key"] !== key) return json(401, {});
  if (req.url === "/api/v1/analyze") {
    let body = "";
    for await (const chunk of req) body += chunk.toString();
    const rejection = [413, 415, 422, 429, 503].find((code) =>
      body.includes(`reject${code}.mov`),
    );
    if (rejection) return json(rejection, {});
    const id = "test_" + ++uploads;
    const mode =
      ["clear", "failed", "malformed"].find((x) => body.includes(x + ".mov")) ||
      "events";
    jobs.set(id, { mode, polls: 0 });
    return json(202, { job_id: id });
  }
  const match = req.url.match(
    /^\/api\/v1\/jobs\/([^/]+)(?:\/(details|video))?$/,
  );
  if (!match || !jobs.has(match[1])) return json(404, {});
  const id = match[1],
    job = jobs.get(id);
  if (match[2] === "video") {
    const range = req.headers.range;
    let start = 0,
      end = video.length - 1;
    if (range) {
      const m = range.match(/^bytes=(\d+)-(\d*)$/);
      if (!m || +m[1] >= video.length) {
        res.writeHead(416, { "Content-Range": `bytes */${video.length}` });
        return res.end();
      }
      start = +m[1];
      if (m[2]) end = Math.min(+m[2], end);
    }
    res.writeHead(range ? 206 : 200, {
      "Content-Type": "video/webm",
      "Accept-Ranges": "bytes",
      "Content-Length": end - start + 1,
      ...(range
        ? { "Content-Range": `bytes ${start}-${end}/${video.length}` }
        : {}),
    });
    return res.end(video.subarray(start, end + 1));
  }
  if (job.polls++ === 0) return json(200, { job_id: id, status: "processing" });
  if (job.mode === "failed")
    return json(200, {
      job_id: id,
      status: "failed",
      error: "synthetic internal detail",
    });
  json(200, {
    job_id: id,
    status: "done",
    video_duration_sec: 3,
    verdict: job.mode === "clear" ? "no_violation" : "violation",
    violations:
      job.mode === "clear" || job.mode === "malformed"
        ? []
        : [
            {
              type: "Тестовое событие",
              timecode_sec: 1,
              description: "Вымышленное описание для проверки интерфейса.",
              evidence_frames: [{ timestamp_sec: 1, image_base64: jpeg }],
              rule: {
                act_name: "Тестовый источник",
                act_number: "TEST",
                paragraph: "1",
                quote: "Вымышленная цитата для проверки отображения.",
                source_url: "javascript:alert(1)",
              },
            },
          ],
  });
});

(async () => {
  let browser, app;
  const out = process.env.QA_OUTPUT_DIR;
  const errors = [];
  try {
    browser = await chromium.launch({ channel: "msedge", headless: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    page.on("pageerror", (e) => errors.push(e.message));
    // Create a purely synthetic JPEG and a short canvas recording. No dataset media.
    const media = await page.evaluate(async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 180;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#173253";
      ctx.fillRect(0, 0, 320, 180);
      ctx.fillStyle = "white";
      ctx.font = "24px Arial";
      ctx.fillText("SYNTHETIC TEST", 40, 90);
      const jpeg = canvas.toDataURL("image/jpeg").split(",")[1];
      const stream = canvas.captureStream(10),
        recorder = new MediaRecorder(stream, { mimeType: "video/webm" }),
        chunks = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      const stopped = new Promise((resolve) => (recorder.onstop = resolve));
      recorder.start();
      const interval = setInterval(() => ctx.fillRect(0, 0, 2, 2), 100);
      await new Promise((r) => setTimeout(r, 2500));
      recorder.stop();
      await stopped;
      clearInterval(interval);
      stream.getTracks().forEach((t) => t.stop());
      return {
        jpeg,
        bytes: Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer())),
      };
    });
    jpeg = media.jpeg;
    video = Buffer.from(media.bytes);
    await new Promise((resolve) => backend.listen(3201, "127.0.0.1", resolve));
    app = spawn(
      process.execPath,
      [
        require.resolve("next/dist/bin/next"),
        "start",
        "--hostname",
        "127.0.0.1",
        "--port",
        "3200",
      ],
      {
        cwd: path.resolve(__dirname, ".."),
        env: {
          ...process.env,
          FV_BACKEND_URL: "http://127.0.0.1:3201",
          FV_APP_ORIGIN: origin,
          FV_MAX_UPLOAD_BYTES: "1048576",
        },
        stdio: "ignore",
        windowsHide: true,
      },
    );
    for (let n = 0; n < 80; n++) {
      try {
        if ((await fetch(origin + "/ui/api/session")).ok) break;
      } catch {}
      await new Promise((r) => setTimeout(r, 250));
    }
    const api = page.request;
    const config = await (await api.get(origin + "/ui/api/config")).json();
    assert.deepEqual(config, {
      allowed_extensions: [".mov"],
      max_upload_bytes: 1048576,
      deadline_sec: 300,
    });
    assert.equal((await api.get(origin + "/ui/api/jobs/test_1")).status(), 401);
    assert.equal(
      (
        await api.post(origin + "/ui/api/session", {
          headers: { Origin: "https://other.invalid" },
          data: { api_key: key },
        })
      ).status(),
      403,
    );
    await page.goto(origin + "/connected");
    await page.getByLabel("Ключ доступа").waitFor();
    await page.locator(".fv-skip-link").focus();
    assert.equal(
      await page
        .locator(".fv-skip-link")
        .evaluate((el) => getComputedStyle(el).opacity),
      "1",
    );
    await page.getByLabel("Ключ доступа").focus();
    assert.equal(
      await page
        .locator(".fv-skip-link")
        .evaluate((el) => getComputedStyle(el).opacity),
      "0",
    );
    if (out) {
      fs.mkdirSync(out, { recursive: true });
      await page.screenshot({
        path: path.join(out, "integration-login.png"),
        fullPage: true,
      });
    }
    await page.getByLabel("Ключ доступа").fill("wrong-key");
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await page
      .getByRole("alert")
      .filter({ hasText: "Ключ недействителен" })
      .waitFor();
    await page.getByLabel("Ключ доступа").fill(key);
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await page.locator("#video-file").waitFor({ state: "attached" });
    if (out)
      await page.screenshot({
        path: path.join(out, "integration-upload.png"),
        fullPage: true,
      });
    const cookie = (await page.context().cookies()).find(
      (c) => c.name === "fv_web_session",
    );
    assert(cookie.httpOnly);
    assert.notEqual(cookie.value, key);
    assert.equal(await page.evaluate(() => localStorage.length), 0);
    await page.locator("#video-file").setInputFiles({
      name: "wrong.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.from("test"),
    });
    await page
      .getByRole("alert")
      .filter({ hasText: "Выберите файл .mov" })
      .waitFor();
    assert.equal(uploads, 0);
    for (const [name, buffer, message] of [
      ["empty.mov", Buffer.alloc(0), "Файл пустой"],
      ["large.mov", Buffer.alloc(1048577), "превышает допустимый"],
    ]) {
      await page
        .locator("#video-file")
        .setInputFiles({ name, mimeType: "video/quicktime", buffer });
      await page.getByRole("alert").filter({ hasText: message }).waitFor();
      assert(
        await page
          .getByRole("button", { name: "Анализировать видео", exact: true })
          .isDisabled(),
      );
    }
    // Drop, replace, remove, then select the same file again.
    await page.locator(".fv-file-zone").evaluate((el) => {
      const data = new DataTransfer();
      data.items.add(
        new File(["test"], "dropped.mov", { type: "video/quicktime" }),
      );
      el.dispatchEvent(
        new DragEvent("drop", { bubbles: true, dataTransfer: data }),
      );
    });
    await page.getByText("dropped.mov", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Удалить выбранный файл" }).click();
    assert(
      await page
        .getByRole("button", { name: "Анализировать видео", exact: true })
        .isDisabled(),
    );
    const upload = async (name) => {
      await page
        .locator("#video-file")
        .setInputFiles({ name, mimeType: "video/quicktime", buffer: video });
      if (name === "events.mov") {
        await page.locator(".fv-upload-panel form").evaluate((form) => {
          form.requestSubmit();
          form.requestSubmit();
        });
      } else
        await page
          .getByRole("button", { name: "Анализировать видео", exact: true })
          .click();
    };
    for (const [code, message] of [
      [413, "превышает"],
      [415, "формате .mov"],
      [422, "не удалось прочитать"],
      [429, "Очередь заполнена"],
      [503, "временно недоступен"],
    ]) {
      await upload(`reject${code}.mov`);
      await page.getByRole("alert").filter({ hasText: message }).waitFor();
      assert.equal(uploads, 0);
    }
    await upload("events.mov");
    await page.getByRole("heading", { name: "Идёт обработка видео" }).waitFor();
    await page
      .getByRole("heading", { name: "Результат проверки", exact: true })
      .waitFor();
    await page.waitForFunction(
      () => document.querySelector("video")?.readyState >= 1,
    );
    await page
      .getByRole("button", { name: "Таймкод: 1 секунды", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector("video").currentTime >= 0.95,
    );
    await page.locator("video").evaluate((video) => {
      video.currentTime = 0;
    });
    await page.locator(".fv-event").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(
      () => document.querySelector("video").currentTime >= 0.95,
    );
    const envelope = await (
      await api.get(origin + "/ui/api/jobs/test_1")
    ).json();
    assert.equal(envelope.meta.filename, "events.mov");
    assert.equal(envelope.meta.stage, "finished");
    assert.equal(envelope.meta.event_details.length, 1);
    assert.equal("video_duration_sec" in envelope.job, false);
    const boxes = await page.evaluate(() => {
      const a = document
          .querySelector(".fv-video-section")
          .getBoundingClientRect(),
        b = document.querySelector(".fv-events").getBoundingClientRect();
      return { a: a.right, b: b.left, delta: Math.abs(a.top - b.top) };
    });
    assert(boxes.a <= boxes.b && boxes.delta < 30, "desktop side-by-side");
    assert.equal(await page.locator(".fv-rule a").count(), 0);
    assert(
      await page
        .locator(".fv-evidence img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    );
    assert.equal(
      (
        await api.get(origin + "/ui/api/jobs/test_1/video", {
          headers: { Range: "bytes=0-9" },
        })
      ).status(),
      206,
    );
    assert.equal(
      (
        await api.get(origin + "/ui/api/jobs/test_1/video", {
          headers: { Range: "bytes=999999999-" },
        })
      ).status(),
      416,
    );
    assert.equal(
      (await api.get(origin + "/ui/api/jobs/foreign_job")).status(),
      404,
    );
    if (out) {
      fs.mkdirSync(out, { recursive: true });
      await page.screenshot({
        path: path.join(out, "integration-desktop.png"),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 375, height: 812 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    if (out)
      await page.screenshot({
        path: path.join(out, "integration-mobile.png"),
        fullPage: true,
      });
    await page.reload();
    await page
      .getByRole("heading", { name: "Результат проверки", exact: true })
      .waitFor();
    assert.equal(uploads, 1);
    await page.getByText("events.mov", { exact: true }).waitFor();
    for (const [name, text] of [
      ["clear", "Признаки возможных нарушений не обнаружены"],
      ["failed", "Не удалось завершить проверку"],
    ]) {
      await page
        .getByRole("button", { name: "Новая проверка", exact: true })
        .click();
      await upload(name + ".mov");
      await page.getByRole("heading", { name: text, exact: true }).waitFor();
    }
    await page
      .getByRole("button", { name: "Новая проверка", exact: true })
      .click();
    await upload("malformed.mov");
    await page
      .getByRole("alert")
      .filter({ hasText: "некорректный результат" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("heading", {
          name: "Признаки возможных нарушений не обнаружены",
          exact: true,
        })
        .count(),
      0,
    );
    // Browser contract fixtures: unavailable jobs, bounded network retries, deadline,
    // multiple events, broken evidence and invalid JSON. Never inspect real media.
    const jobUrl = origin + "/ui/api/jobs/test_1";
    const openJob = () => page.goto(origin + "/connected?job=test_1");
    for (const code of [404, 410]) {
      await page.route(jobUrl, (route) =>
        route.fulfill({
          status: code,
          json: { error: `Тест: задача недоступна (${code})` },
        }),
      );
      await openJob();
      await page
        .getByRole("alert")
        .filter({ hasText: `(${code})` })
        .waitFor();
      assert.equal(
        await page
          .getByRole("heading", { name: "Результат проверки", exact: true })
          .count(),
        0,
      );
      await page.unroute(jobUrl);
    }
    let attempts = 0;
    await page.route(jobUrl, (route) => {
      attempts++;
      return route.abort("failed");
    });
    await openJob();
    await page.getByRole("alert").filter({ hasText: "Нет связи" }).waitFor();
    await new Promise((r) => setTimeout(r, 7500));
    assert.equal(attempts, 3, "bounded network retries");
    const beforeRetry = uploads;
    await page.unroute(jobUrl);
    await page
      .getByRole("button", { name: "Повторить запрос статуса" })
      .click();
    await page
      .getByRole("heading", { name: "Результат проверки", exact: true })
      .waitFor();
    assert.equal(uploads, beforeRetry);
    const fixture = structuredClone(envelope);
    fixture.job = { job_id: "test_1", status: "processing" };
    fixture.meta = {
      ...fixture.meta,
      stage: "evidence",
      progress: null,
      video_url: null,
      event_details: [],
    };
    fixture.started_at = Date.now() - 301000;
    await page.route(jobUrl, (route) => route.fulfill({ json: fixture }));
    await openJob();
    await page
      .getByText("Стадия: Подготовка кадров", { exact: true })
      .waitFor();
    await page
      .getByRole("status")
      .filter({ hasText: "Превышен лимит 5 минут" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("heading", { name: "Результат проверки", exact: true })
        .count(),
      0,
    );
    await page.unroute(jobUrl);
    const multi = structuredClone(envelope);
    multi.job.violations[0].description =
      '<img src=x onerror="window.fixtureXss=true">';
    multi.job.violations.push({
      ...structuredClone(multi.job.violations[0]),
      type: "Второй тестовый пример",
      timecode_sec: 2,
    });
    multi.meta.event_details.push({
      violation_index: 1,
      observation: "Тестовое наблюдение",
      rule_reference: {
        title: "Тестовый источник",
        paragraph: "2",
        url: "https://example.invalid/source",
      },
    });
    await page.route(jobUrl, (route) => route.fulfill({ json: multi }));
    await openJob();
    await page
      .getByRole("heading", { name: "Второй тестовый пример" })
      .waitFor();
    assert.equal(await page.locator(".fv-event").count(), 2);
    assert.equal(await page.locator(".fv-event-description img").count(), 0);
    assert.equal(await page.evaluate(() => window.fixtureXss), undefined);
    await page.setViewportSize({ width: 1280, height: 900 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert(
      await page.evaluate(
        () =>
          document.querySelector(".fv-video-section").getBoundingClientRect()
            .right <=
          document.querySelector(".fv-events").getBoundingClientRect().left,
      ),
    );
    const link = page.getByRole("link", {
      name: "Открыть нормативный источник",
    });
    assert.equal(await link.getAttribute("rel"), "noopener noreferrer");
    await page.unroute(jobUrl);
    const broken = structuredClone(envelope);
    broken.job.violations[0].evidence_frames[0].image_base64 = "/9j/AAAA";
    await page.route(jobUrl, (route) => route.fulfill({ json: broken }));
    await page.route(origin + "/ui/api/jobs/test_1/video", (route) =>
      route.fulfill({ status: 404, body: "" }),
    );
    await openJob();
    await page
      .getByRole("alert")
      .filter({ hasText: "Кадр недоступен" })
      .waitFor();
    await page
      .getByRole("alert")
      .filter({ hasText: "Видео не удалось воспроизвести" })
      .waitFor();
    assert.equal(await page.locator(".fv-event").count(), 1);
    await page.unroute(jobUrl);
    await page.unroute(origin + "/ui/api/jobs/test_1/video");
    await page.route(jobUrl, (route) =>
      route.fulfill({ contentType: "application/json", body: "{broken" }),
    );
    await openJob();
    await page
      .getByRole("alert")
      .filter({ hasText: "некорректный ответ" })
      .waitFor();
    await page.unroute(jobUrl);
    await page.route(jobUrl, (route) =>
      route.fulfill({
        status: 401,
        json: { error: "Сессия истекла. Войдите снова." },
      }),
    );
    await openJob();
    await page.getByLabel("Ключ доступа").waitFor();
    await page.unroute(jobUrl);
    await page.getByLabel("Ключ доступа").fill(key);
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await page.locator("#video-file").waitFor({ state: "attached" });
    await page.getByRole("button", { name: "Выйти", exact: true }).click();
    await page.getByLabel("Ключ доступа").waitFor();
    assert.equal((await api.get(origin + "/ui/api/jobs/test_1")).status(), 401);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: synthetic FE scenarios: login/expiry/logout, config/meta, drop/remove/validation, 413/415/422/429/503, processing/done/failed, 404/410, bounded network retries, 300s warning, multiple events, broken image/video, malformed JSON/result, card keyboard seek, Range 206/416, restored filename, desktop columns/mobile width, no browser exceptions. Real backend, Chrome/Safari and deployment remain unverified.",
    );
  } finally {
    await browser?.close();
    app?.kill();
    backend.closeAllConnections();
    await new Promise((r) => backend.close(r));
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
