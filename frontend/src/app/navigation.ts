import type { Page } from "./types";

const pages: Page[] = ["operations", "intake", "universities", "integrations", "rubrics", "users", "profile"];

export function parseHash(hash: string): Page {
  const value = hash.replace(/^#\/?/, "").split("/")[0] as Page;
  return pages.includes(value) ? value : "operations";
}

export function pageTitle(page: Page): string {
  const titles: Record<Page, string> = {
    operations: "Admissions cases",
    intake: "Applicant document intake",
    universities: "University Management",
    integrations: "Integrations",
    rubrics: "Rubrics",
    users: "Users",
    profile: "Profile"
  };
  return titles[page];
}

export function pageEyebrow(page: Page): string {
  const eyebrows: Record<Page, string> = {
    operations: "Admissions workspace",
    intake: "Document operations",
    universities: "Superadmin Console",
    integrations: "Connections",
    rubrics: "Rubric operations",
    users: "Administration",
    profile: "Billing"
  };
  return eyebrows[page];
}

export function pageDescription(page: Page): string {
  const descriptions: Record<Page, string> = {
    operations: "Collect required evidence, verify source documents, and complete structured human reviews.",
    intake: "Upload applicant materials, track parsing progress, review extracted details, and monitor recent intake activity.",
    universities: "Manage university tenant accounts, rotate global programmatic credentials, and configure billing structures.",
    integrations: "Connect external document sources and admissions systems as the workspace expands.",
    rubrics: "Create and maintain program scoring templates used during document intake.",
    users: "Admin-only view of accounts created for this workspace.",
    profile: "Review processed-document usage, billable units, and current charges for this account."
  };
  return descriptions[page];
}
