import type { Job, UploadQueueItem } from "../api/types";

export function mergeRecentJobs(current: Job[], updates: Job[]): Job[] {
  const byId = new Map(current.map((job) => [job.job_id, job]));
  for (const job of updates) {
    byId.set(job.job_id, job);
  }
  return Array.from(byId.values())
    .sort((left, right) => new Date(right.received_at).getTime() - new Date(left.received_at).getTime())
    .slice(0, 20);
}

export function jobsForApplication(applicationId: string, recentJobs: Job[], queueItems: UploadQueueItem[]): Job[] {
  const byId = new Map<string, Job>();
  for (const job of recentJobs) {
    if (job.application_id === applicationId) {
      byId.set(job.job_id, job);
    }
  }
  for (const item of queueItems) {
    const job = item.job;
    if (job?.application_id === applicationId) {
      byId.set(job.job_id, job);
    }
  }
  return Array.from(byId.values()).sort(
    (left, right) => new Date(right.received_at).getTime() - new Date(left.received_at).getTime()
  );
}

export function pageCountForJob(job: Job): number {
  const extractedRecord = job.extracted_record ?? {};
  const documentRecord = asRecord(extractedRecord.document);
  const metadataRecord = asRecord(extractedRecord.metadata);
  const directCount =
    positiveNumber(job.page_count) ??
    positiveNumber(extractedRecord.page_count) ??
    positiveNumber(documentRecord?.page_count) ??
    positiveNumber(metadataRecord?.page_count);
  if (directCount) {
    return Math.ceil(directCount);
  }
  if (Array.isArray(extractedRecord.pages) && extractedRecord.pages.length) {
    return extractedRecord.pages.length;
  }
  return 1;
}

export function billableUnitsForPages(pages: number, pagesPerUnit?: number): number {
  const divisor = pagesPerUnit && pagesPerUnit >= 1 ? pagesPerUnit : 10;
  return pages > 0 ? Math.max(1, Math.ceil(pages / divisor)) : 0;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function positiveNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  return value;
}
