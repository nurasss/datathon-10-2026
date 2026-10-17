export type EvidenceFrame = { timestamp_sec: number; image_base64: string };
export type RuleDetails = {
  act_name: string;
  act_number: string;
  paragraph: string;
  quote: string;
  source_url: string;
};
export type Violation = {
  type: string;
  timecode_sec: number;
  description: string;
  evidence_frames: EvidenceFrame[];
  rule?: RuleDetails;
  observation?: string | null;
};
export type JobMeta = {
  filename: string | null;
  duration_sec: number | null;
  stage:
    | "queued"
    | "probe"
    | "decode"
    | "reasoning"
    | "evidence"
    | "finalize"
    | "finished"
    | null;
  progress: number | null;
  video_url: string | null;
  event_details: {
    violation_index: number;
    observation: string | null;
    rule_reference: {
      title: string;
      paragraph: string | null;
      url: string | null;
    } | null;
  }[];
  error_code: string | null;
  error_message: string | null;
};
export type CheckResult =
  | { job_id: string; status: "processing"; video_duration_sec?: number | null }
  | {
      job_id: string;
      status: "failed";
      error?: string;
      video_duration_sec?: number | null;
    }
  | {
      job_id: string;
      status: "done";
      verdict: "violation" | "no_violation";
      violations: Violation[];
      video_duration_sec?: number | null;
    };

const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const seconds = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v) && v >= 0;
export function validJobId(value: unknown): value is string {
  return typeof value === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value);
}
export function safeRuleUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

/** Validate the actual /details contract; never turn malformed data into no_violation. */
export function parseResult(value: unknown, expectedId: string): CheckResult {
  const invalid = () => {
    throw new Error("Некорректный ответ сервиса. Результат нельзя отобразить.");
  };
  if (!object(value) || value.job_id !== expectedId) return invalid();
  const duration = value.video_duration_sec;
  if (duration != null && !seconds(duration)) return invalid();
  if (value.status === "processing")
    return {
      job_id: expectedId,
      status: "processing",
      video_duration_sec: duration as number | null | undefined,
    };
  if (value.status === "failed")
    return {
      job_id: expectedId,
      status: "failed",
      error:
        "Сервис не смог завершить анализ. Это не означает отсутствие нарушений.",
      video_duration_sec: duration as number | null | undefined,
    };
  if (
    value.status !== "done" ||
    !Array.isArray(value.violations) ||
    !["violation", "no_violation"].includes(String(value.verdict))
  )
    return invalid();
  if ((value.verdict === "no_violation") !== (value.violations.length === 0))
    return invalid();
  const violations: Violation[] = value.violations.map((event: unknown) => {
    if (
      !object(event) ||
      typeof event.type !== "string" ||
      !event.type.trim() ||
      typeof event.description !== "string" ||
      !event.description.trim() ||
      !seconds(event.timecode_sec) ||
      (duration != null && event.timecode_sec > (duration as number)) ||
      !Array.isArray(event.evidence_frames) ||
      event.evidence_frames.length < 1 ||
      event.evidence_frames.length > 3
    )
      return invalid();
    const evidence_frames = event.evidence_frames.map((frame: unknown) => {
      if (
        !object(frame) ||
        !seconds(frame.timestamp_sec) ||
        (duration != null && frame.timestamp_sec > (duration as number)) ||
        typeof frame.image_base64 !== "string" ||
        !frame.image_base64.startsWith("/9j/") ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(frame.image_base64)
      )
        return invalid();
      return {
        timestamp_sec: frame.timestamp_sec,
        image_base64: frame.image_base64,
      };
    });
    let rule: RuleDetails | undefined;
    if (event.rule != null) {
      if (
        !object(event.rule) ||
        !["act_name", "act_number", "paragraph", "quote", "source_url"].every(
          (key) =>
            typeof (event.rule as Record<string, unknown>)[key] === "string",
        )
      )
        return invalid();
      rule = event.rule as RuleDetails;
    }
    return {
      type: event.type,
      description: event.description,
      timecode_sec: event.timecode_sec,
      evidence_frames,
      rule,
    };
  });
  return {
    job_id: expectedId,
    status: "done",
    verdict: value.verdict as "violation" | "no_violation",
    violations,
    video_duration_sec: duration as number | null | undefined,
  };
}

/** Browser envelope from TZ §6.2. Metadata is display data, never an inferred verdict. */
export function parseBrowserResult(value: unknown, id: string) {
  if (!object(value) || !object(value.meta))
    throw new Error("Некорректные метаданные результата.");
  const meta = value.meta;
  if (
    !(meta.filename === null || typeof meta.filename === "string") ||
    !(meta.duration_sec === null || seconds(meta.duration_sec)) ||
    ![
      null,
      "queued",
      "probe",
      "decode",
      "reasoning",
      "evidence",
      "finalize",
      "finished",
    ].includes(meta.stage as string | null) ||
    !(
      meta.progress === null ||
      (seconds(meta.progress) && meta.progress <= 1)
    ) ||
    !(
      meta.video_url === null ||
      meta.video_url === `/ui/api/jobs/${encodeURIComponent(id)}/video`
    ) ||
    !Array.isArray(meta.event_details) ||
    !(meta.error_code === null || typeof meta.error_code === "string") ||
    !(meta.error_message === null || typeof meta.error_message === "string")
  )
    throw new Error("Некорректные метаданные результата.");
  const job = parseResult(value.job, id);
  const seen = new Set<number>();
  for (const item of meta.event_details) {
    if (
      !object(item) ||
      !Number.isInteger(item.violation_index) ||
      (item.violation_index as number) < 0 ||
      job.status !== "done" ||
      (item.violation_index as number) >= job.violations.length ||
      seen.has(item.violation_index as number) ||
      !(item.observation === null || typeof item.observation === "string")
    )
      throw new Error("Некорректные сведения о событии.");
    seen.add(item.violation_index as number);
    const ref = item.rule_reference;
    if (
      ref !== null &&
      (!object(ref) ||
        typeof ref.title !== "string" ||
        !(ref.paragraph === null || typeof ref.paragraph === "string") ||
        !(ref.url === null || typeof ref.url === "string"))
    )
      throw new Error("Некорректный нормативный источник.");
    const event = job.violations[item.violation_index as number];
    event.observation = item.observation as string | null;
    if (object(ref))
      event.rule = {
        act_name: ref.title as string,
        act_number: "",
        paragraph: (ref.paragraph as string) || "",
        quote: "",
        source_url: (ref.url as string) || "",
      };
  }
  return {
    job,
    meta: meta as JobMeta,
    started_at: seconds(value.started_at) ? value.started_at : null,
  };
}
