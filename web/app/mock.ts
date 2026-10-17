import { CheckResult } from "./api";

let mockJobs: { [jobId: string]: CheckResult } = {};

export async function post(file: File) : Promise<string> {
    const jobId = Math.random().toString(36).substring(2, 10);
    // Simulate a delay for the job processing
    setTimeout(() => {
        // Simulate a random result for the job
        const hasViolation = Math.random() < 0.5;
        const result: CheckResult = {
            job_id: jobId,
            status: "done",
            verdict: hasViolation ? "violation" : "no_violation",
            violations: hasViolation ? [
                {
                    type: "Inappropriate Content",
                    timecode_sec: 44.2,
                    description: "The video contains inappropriate content at this timecode.",
                    evidence_frames: [
                        { timestamp_sec: 44.2, image_base64: "/9j/4AAQ..." }
                    ]
                }
            ] : []
        };
        mockJobs[jobId] = result;
    }, 2000); // Simulate a 2-second processing time
    return jobId;
}

export async function get(jobId: string) : Promise<CheckResult | null> {
    return mockJobs[jobId] || null;
}
