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
      video_duration_sec: number;
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
    !seconds(duration) ||
    duration <= 0 ||
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
      event.timecode_sec > duration ||
      !Array.isArray(event.evidence_frames) ||
      event.evidence_frames.length < 1 ||
      event.evidence_frames.length > 3
    )
      return invalid();
    const evidence_frames = event.evidence_frames.map((frame: unknown) => {
      if (
        !object(frame) ||
        !seconds(frame.timestamp_sec) ||
        frame.timestamp_sec > duration ||
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
    video_duration_sec: duration,
  };
}
