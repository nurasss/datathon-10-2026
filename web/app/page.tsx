'use client';
import { post, get } from "./mock";
import { useState, useEffect, SubmitEventHandler } from "react";
// import Image from "next/image";

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
	return (
		<form onSubmit={handleUpload}>
			<input type="file" onChange={handleFile} />
			<input type="submit" value="Upload" />
		</form>
	);
}

function JobStatus({ statusId }: { statusId: string }) {

	const [status, setStatus] = useState<string | null>(null);
	const [verdict, setVerdict] = useState<string | null>(null);
	const [violations, setViolations] = useState<any[] | null>(null);

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

	return (
		<div>
			<p>Job ID: {statusId}</p>
			<p>Status: {status}</p>
			{status === "done" && (
				<div>
					<p>Verdict: {verdict}</p>
					{verdict === "violation" && violations && (
						<div>
							<h3>Violations:</h3>
							<ul>
								{violations.map((violation, index) => (
									<li key={index}>
										Type: {violation.type}, Timecode: {violation.timecode_sec}, Description: {violation.description}
									</li>
								))}
							</ul>
						</div>
					)}
				</div>
			)}
			{status === "error" && <p>An error occurred while processing the job.</p>}
		</div>
	);
}

export default function Home() {
	const [jobId, setJobId] = useState<string | null>(null);
	let JobView = jobId ? <JobStatus statusId={jobId} /> : <FileUploadForm setJobID={setJobId}/>;
	return (
		<div className="flex flex-col flex-1 items-center justify-center bg-zinc-50 font-sans dark:bg-black">
		<main className="flex flex-1 w-full max-w-3xl flex-col items-center justify-between py-32 px-16 bg-white dark:bg-black sm:items-start">
		
		{JobView}

		</main>
		</div>
	);
}
