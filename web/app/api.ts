
export type Violation = {
    type: string;
    timecode_sec: number;
    description: string;
    evidence_frames: {
        timestamp_sec: number;
        image_base64: string;
    }[];
};

export type CheckResult = {
    job_id: string;
    status: "done" | "pending" | "error";
    verdict: "violation" | "no_violation";
    violations?: Violation[];
};

export async function post(file: File) : Promise<string> {
    try {
        const formData = new FormData();
        formData.append("file", file);

        const response = await fetch("/api/v1/analyze", {
            method: "POST",
            body: formData,
        });

        if (!response.ok) {
            throw new Error(`Error uploading file: ${response.statusText}`);
        }

        const data = await response.json();
        return data.job_id; // Assuming the API returns { job_id: "some_id" }
    } catch (error) {
        console.error("Error uploading file:", error);
        throw error;
    }
}

export async function get(jobId: string) : Promise<CheckResult | null> {
    try {
        const response = await fetch(`/api/v1/jobs/${jobId}`, {
            method: "GET",
        });

        if (!response.ok) {
            throw new Error(`Error fetching job status: ${response.statusText}`);
        }

        const data: CheckResult = await response.json();
        return data;
    } catch (error) {
        console.error("Error fetching job status:", error);
        return null;
    }
}
