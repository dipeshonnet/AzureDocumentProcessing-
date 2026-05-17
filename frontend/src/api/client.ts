import type { AuthResponse, AuthUser, Job, ManagedUser, Rubric, RubricInput } from "./types";

const fallbackApiBaseUrl = "http://127.0.0.1:8000";
const authTokenKey = "admission-analyser-token";

export const apiBaseUrl =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ||
  fallbackApiBaseUrl;

export function getAuthToken(): string | null {
  return window.localStorage.getItem(authTokenKey);
}

export function setAuthToken(token: string): void {
  window.localStorage.setItem(authTokenKey, token);
}

export function clearAuthToken(): void {
  window.localStorage.removeItem(authTokenKey);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers
    }
  });
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export async function register(email: string, password: string, confirmPassword: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, confirm_password: confirmPassword })
  });
}

export async function logout(): Promise<void> {
  await request("/api/auth/logout", { method: "POST" });
  clearAuthToken();
}

export async function changePassword(currentPassword: string, newPassword: string, confirmNewPassword: string): Promise<void> {
  await request("/api/auth/password", {
    method: "PUT",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
      confirm_new_password: confirmNewPassword
    })
  });
}

export async function updateProfileLogo(universityLogoDataUrl: string | null): Promise<AuthUser> {
  return request<AuthUser>("/api/auth/profile-logo", {
    method: "PUT",
    body: JSON.stringify({ university_logo_data_url: universityLogoDataUrl })
  });
}

export async function fetchCurrentUser(): Promise<AuthUser> {
  return request<AuthUser>("/api/auth/me");
}

export async function fetchManagedUsers(): Promise<ManagedUser[]> {
  return request<ManagedUser[]>("/api/auth/users");
}

export async function updateManagedUserBillingRate(userId: string, billingRatePerUnit: number): Promise<ManagedUser> {
  return request<ManagedUser>(`/api/auth/users/${userId}/billing-rate`, {
    method: "PUT",
    body: JSON.stringify({ billing_rate_per_unit: billingRatePerUnit })
  });
}

export async function fetchJobsStatus(jobIds?: string[]): Promise<Job[]> {
  const query = jobIds?.length ? `?job_ids=${encodeURIComponent(jobIds.join(","))}` : "";
  const payload = await request<{ jobs: Job[] }>(`/api/jobs/status${query}`);
  return payload.jobs;
}

export async function fetchRubrics(): Promise<Rubric[]> {
  return request<Rubric[]>("/api/rubrics");
}

export async function createRubric(input: RubricInput): Promise<Rubric> {
  return request<Rubric>("/api/rubrics", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function updateRubric(rubricId: string, input: RubricInput): Promise<Rubric> {
  return request<Rubric>(`/api/rubrics/${rubricId}`, {
    method: "PUT",
    body: JSON.stringify(input)
  });
}

export type UploadInput = {
  file: File;
  rubric_id: string;
  applicant_name: string;
  applicant_id?: string;
  program_applied: string;
  intake_term: string;
  application_id?: string;
};

export function uploadFileWithProgress(
  input: UploadInput,
  onProgress: (progress: number) => void
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiBaseUrl}/api/upload`);
    const token = getAuthToken();
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed. Check that the backend is running."));
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(xhrResponseError(xhr)));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText) as Job);
      } catch {
        reject(new Error("Upload response was not valid JSON."));
      }
    };

    const formData = new FormData();
    formData.append("file", input.file);
    formData.append("rubric_id", input.rubric_id);
    formData.append("applicant_name", input.applicant_name);
    formData.append("program_applied", input.program_applied);
    formData.append("intake_term", input.intake_term);
    if (input.applicant_id?.trim()) {
      formData.append("applicant_id", input.applicant_id.trim());
    }
    if (input.application_id) {
      formData.append("application_id", input.application_id);
    }
    xhr.send(formData);
  });
}

export async function downloadJobRecord(jobId: string): Promise<void> {
  const token = getAuthToken();
  const response = await fetch(`${apiBaseUrl}/api/jobs/${jobId}/record`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  });
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${jobId}-extracted-record.json`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function responseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Keep the safe status message.
  }
  return `Request failed with status ${response.status}`;
}

function xhrResponseError(xhr: XMLHttpRequest): string {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Keep the safe status message.
  }
  return `Upload failed with status ${xhr.status}`;
}
