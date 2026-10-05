# Admissions case workflows

The **Cases** navigation entry opens the admissions workspace. Existing Intake,
Rubrics, Users, Profile, API keys, webhooks and document processing remain available.
The completed DocumentProcessing workflows have been adapted to FastAPI, React,
SQLAlchemy and the configured OnlyAzure storage/parser adapters.

## Start using the workspace

1. Select a university workspace. Platform administrators can access any university;
   other staff can switch only between their active memberships.
2. Open **Programs & Criteria**, create a program, define evidence requirements and
   optional scoring criteria, save a draft and publish it. Scoring weights total
   100; programs may instead use a checklist without scores.
3. Invite reviewers from **People** and copy their invitation links. Existing staff
   use their account password to accept membership in an additional university.
   Set program/department coverage and workload limits as needed.
4. Create applications directly or use **Data exchange** to download the CSV
   template, preview rows and changes, then commit valid rows. Candidate identity
   uses exact university-scoped system IDs; names never merge candidates.
5. Fulfil the pinned checklist through staff uploads, secure applicant links,
   verified structured values or reasoned waivers. Replace an occupied document
   slot explicitly to preserve every previous version. Failed documents do not
   fulfil a requirement. Optional items do not block completeness.
   Structured requirements also support multiple value slots. Replacing a verified
   value preserves its previous value and verifier in the requirement history.
6. Assign complete cases to reviewers. Reviewers inspect originals alongside
   extracted text, confirm ambiguous types, and verify/correct/reject uncertain
   fields. Corrections require a reason, source reference and an exact quote.
7. Reviewers enter bounded criterion scores and notes. Drafts autosave; sign-off
   requires source verification, complete evidence and resolved conflicts. A
   declared conflict of interest blocks sign-off.
8. Export current signed scores for reconciliation in the university system.
   Evidence changes preserve previous signed reviews and require rechecking.
   Withdrawn cases and outdated reviews are excluded from exports.

Institution setup controls the workspace name/accent, link expiry, external
processing approval and an optional fictional training walkthrough. Live parser
uploads require institution approval; mock mode never sends documents externally.
Invitation links can be copied and shared. To enable optional invitation email
delivery, configure `PUBLIC_APP_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` and,
if needed, `SMTP_USERNAME`/`SMTP_PASSWORD`. STARTTLS is enabled by default.
Staff explicitly select email delivery when inviting someone; failed delivery
preserves the invitation and offers its link for manual sharing.

## Billing and reports

University owners/managers and finance viewers can open **Billing** and **Reports**.
Platform administrators publish effective-dated contracts, generate draft invoices,
and track offline payment statuses. Charges retain the successful document's
original page count, unit rule, price and currency. Each replacement that processes
successfully is a separate charge; failures are nonbillable. Invoice generation
does not bill usage twice. Currency groups produce separate invoices.

Reports show program completeness, missing evidence, current-version processing
health, late submissions, rechecking cases and reviewer throughput/turnaround.
The platform overview shows university onboarding and commercial activity.
Historical documents without a captured charge are not retroactively invoiced.
The older Profile ledger remains an estimate at current settings; workspace Billing
contains the recorded commercial history.

## Schema and compatibility

The additive `admissions_workflow_records` table holds domain-controlled,
tenant-scoped records with a unique kind/resource key and optimistic version column.
No generic record-write API is exposed. Existing document/OCR/job tables are reused.
Published criteria, signed reviews, document originals and economic charge snapshots
are preserved; their mutable lifecycle metadata is kept separately.

SQLite local schema initialization upgrades automatically. Before deploying over
an existing Azure SQL/PostgreSQL database, back up the database and run:

```powershell
.\.venv\Scripts\python.exe scripts\migrate-admissions-workflow.py
```

The script uses the configured `DATABASE_URL`, adds the workflow table, replaces the
old globally unique student-ID index with a nonunique lookup index and splits
candidate records historically shared by universities. Run it with deployment
credentials that can create tables/indexes. It is safe to rerun. Legacy applications
retain originals and processing output; they appear with criteria unassigned.
Managers can explicitly pin a published program and fulfil its new checklist.

New APIs live under `/api/ops`. Authenticated calls accept `X-Workspace-Id` and
validate membership before reading or writing records. Public upload/invitation
endpoints accept only hashed, expiring, revocable tokens. Secure upload responses
expose receipt status without candidate identifiers or staff records.

Keep `EXPOSE_LEGACY_API=false` and `DEV_AUTH_ENABLED=false` in deployed profiles as
documented by the Azure MVP guide. The new workspace uses bearer sessions; it does
not use the development-only legacy review authentication.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
# In frontend, with Node.js available:
npm test
npm run build
```

The integration suite covers pinned versions, university identity isolation,
requirements/waivers, uploads and replacements, token expiry/revocation/replay,
assigned reviewer access, source corrections, stale edits, signed review revisions,
CSV preview/commit conflicts, immutable billing and invoice replay protection.
Frontend tests cover workspace role navigation, publishing revisions, secure
receipts, revoked links and saving human scores before sign-off. The production
UI was also exercised in an isolated local preview using fictional applications.

Provider adapters/sync, unmatched-file resolution, SSO, automated notifications
and automatic retention/deletion were incomplete in the reference and are excluded.
