'use client';
import { post, get } from "./mock";
import { useState, useEffect } from "react";
import type { Violation } from "./api";
import { AppShell } from "./components/app-shell";
import { UploadView } from "./components/upload-view";
import { JobView } from "./components/job-view";

function FileUploadForm({ setJobID }: { setJobID: (jobId: string) => void }) {
	const [file, setFile] = useState<File | null>(null);
	const handleFile = (event: React.ChangeEvent<HTMLInputElement>) => {
		const value = event.target.files?.[0];
		if (value && value.type !== "video/quicktime") {
			alert("Please upload a valid .mov video file.");
			event.target.value = "";
			setFile(null);
			return;
		} 
		setFile(value || null);
	};
	const handleUpload = async (e: React.SyntheticEvent<HTMLFormElement>) => {
		e.preventDefault();
		if (file) {
			alert(`Uploading file: ${file.name}`);
			try {
				setJobID(await post(file));
			} catch (error) {
				console.error("Error uploading file:", error);
			}
		} else {
			alert("Please select a file to upload.");
		}
	};
	return <UploadView file={file} onFileChange={handleFile} onSubmit={handleUpload} />;
}

function JobStatus({ statusId }: { statusId: string }) {

	const [status, setStatus] = useState<string | null>(null);
	const [verdict, setVerdict] = useState<string | null>(null);
	const [violations, setViolations] = useState<Violation[] | null>(null);

	useEffect(() => {
		const interval = setInterval(async () => {
			try {
				const result = await get(statusId);
				if (result) {
					setStatus(result.status);
					setVerdict(result.verdict);
					setViolations(result.violations || []);
					if (result.status === "done" || result.status === "error") {
						clearInterval(interval);
					}
				}
			} catch (error) {
				console.error("Error fetching job status:", error);
			}
		}, 2000); // Poll every 2 seconds

		return () => clearInterval(interval);
	}, [statusId]);

	return <JobView jobId={statusId} status={status} verdict={verdict} violations={violations} />;
}

export default function Home() {
	const [jobId, setJobId] = useState<string | null>(null);
	const content = jobId ? <JobStatus statusId={jobId} /> : <FileUploadForm setJobID={setJobId}/>;
	return <AppShell>{content}</AppShell>;
}
