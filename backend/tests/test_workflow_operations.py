"""End-user acceptance and isolation tests for the ported admissions workflows."""
from datetime import datetime, timezone
from io import StringIO
import csv

import pytest
from sqlalchemy import select

from test_intake_operations import intake_client, auth_headers, wait_for_terminal
from app.models import Application, ExtractedDocumentContent, IntakeJob, LocalUser
from app.models.workflow import WorkflowRecord
from app.services import workflow as w
from app.services.workflow_billing import record_usage
from app.services.workflow_evidence import analyze


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def setup_program(client, headers, *, document=False, code="DS", max_count=1):
    program = ok(client.post("/api/ops/programs", headers=headers, json={"name": "Data Science", "code": code}), 201)
    requirement = {"key": "transcript", "label": "Transcript", "capture_type": "document" if document else "number", "required": True, "min_count": 1, "max_count": max_count}
    template = ok(client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json={"name": "DS criteria", "requirements": [requirement], "criteria": [{"key": "academic", "label": "Academic preparation", "weight": 100, "evidence_keys": ["transcript"]}]}))
    ok(client.post(f"/api/ops/versions/{template['id']}/publish", headers=headers, json={"revision": template["revision"], "reason": "Ready"}))
    return program, template


def create_case(client, headers, program, external_id="APP-1", candidate_id="C-1"):
    return ok(client.post("/api/ops/cases", headers=headers, json={"applicant_name": "Ada Student", "external_candidate_id": candidate_id, "external_application_id": external_id, "program_id": program["id"], "intake_term": "Fall 2027"}), 201)


def detail(client, headers, case):
    return ok(client.get(f"/api/ops/cases/{case['id']}", headers=headers))


def complete_number(client, headers, case):
    requirement = detail(client, headers, case)["requirements"][0]
    return ok(client.put(f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/value", headers=headers, json={"value": "3.5", "revision": requirement["revision"]}))


def invite(client, headers, email="reviewer@example.edu", role="reviewer"):
    invitation = ok(client.post("/api/ops/invitations", headers=headers, json={"email": email, "role": role}), 201)
    token = invitation["path"].rsplit("/", 1)[-1]
    accepted = ok(client.post(f"/api/ops/public/invitations/{token}", json={"password": "SecureTestPassword!"}))
    reviewer_headers = {"Authorization": "Bearer " + accepted["token"], "X-Workspace-Id": accepted["university_id"]}
    me = ok(client.get("/api/auth/me", headers=reviewer_headers))
    return reviewer_headers, me, token


def assign(client, headers, case, reviewer):
    return ok(client.post(f"/api/ops/cases/{case['id']}/assign", headers=headers, json={"reviewer_id": reviewer["user_id"], "priority": "high", "due_at": "2027-01-10"}))


def test_program_versions_are_immutable_and_cases_stay_pinned(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    program, first = setup_program(client, headers)
    case = create_case(client, headers, program)
    second = ok(client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json={"name": "Updated checklist", "requirements": [{"key": "statement", "label": "Statement", "capture_type": "text"}], "criteria": []}))
    ok(client.post(f"/api/ops/versions/{second['id']}/publish", headers=headers, json={"revision": second["revision"], "reason": "Updated"}))
    original = detail(client, headers, case)
    assert original["case"]["template_id"] == first["id"]
    assert original["requirements"][0]["key"] == "transcript"
    assert client.post(f"/api/ops/versions/{first['id']}/publish", headers=headers, json={"revision": first["revision"], "reason": "Again"}).status_code == 409
    tenant_id = ok(client.get("/api/ops/context", headers=headers))["university_id"]
    with factory() as db:
        first_template = w.find(db, tenant_id, "template", first["id"])
        assert first_template.payload["name"] == "DS criteria"


def test_invalid_criteria_and_stale_drafts_are_rejected(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    program = ok(client.post("/api/ops/programs", headers=headers, json={"name": "P", "code": "P"}), 201)
    bad = {"name": "Bad", "requirements": [{"key": "x", "label": "X", "min_count": 2, "max_count": 1}], "criteria": []}
    assert client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json=bad).status_code == 422
    bad["requirements"] = [{"key": "x", "label": "X"}]
    bad["criteria"] = [{"key": "a", "label": "A", "weight": 80, "evidence_keys": ["x"]}]
    assert client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json=bad).status_code == 422
    draft = ok(client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json={"name": "Draft", "requirements": [], "criteria": []}))
    assert client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json={"name": "Overwrite", "requirements": [], "criteria": [], "revision": draft["revision"] - 1}).status_code == 409


def test_candidate_ids_are_scoped_and_shared_between_program_applications(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers)
    first = create_case(client, headers, p)
    second = create_case(client, headers, p, "APP-2")
    assert first["candidate_id"] == second["candidate_id"]
    university = ok(client.post("/api/auth/universities", headers=headers, json={"name": "Other University"}), 201)
    other_headers = {**headers, "X-Workspace-Id": university["university_id"]}
    other_p, _ = setup_program(client, other_headers)
    other = create_case(client, other_headers, other_p)
    assert other["candidate_id"] != first["candidate_id"]
    assert client.get(f"/api/ops/cases/{first['id']}", headers=other_headers).status_code == 404
    assert client.get(f"/api/ops/cases/{other['id']}", headers=headers).status_code == 404


def test_requirements_waivers_and_review_revisions_preserve_signed_scores(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers)
    case = create_case(client, headers, p)
    reviewer_headers, reviewer, _ = invite(client, headers)
    assert client.post(f"/api/ops/cases/{case['id']}/assign", headers=headers, json={"reviewer_id": reviewer["user_id"]}).status_code == 409
    complete_number(client, headers, case)
    assign(client, headers, case, reviewer)
    current = detail(client, reviewer_headers, case)["case"]
    form = {"scores": {"academic": 75}, "criterion_notes": {"academic": "Checked source"}, "comments": "Reviewed", "revision": current["case_revision"], "source_verified": True}
    ok(client.put(f"/api/ops/cases/{case['id']}/review/draft", headers=reviewer_headers, json=form))
    ok(client.post(f"/api/ops/cases/{case['id']}/review/sign", headers=reviewer_headers, json=form), 201)
    assert detail(client, reviewer_headers, case)["own_review_signed"] is True
    assert client.put(f"/api/ops/cases/{case['id']}/review/draft", headers=reviewer_headers, json=form).status_code == 409
    old_export = client.get("/api/ops/exchange/scores.csv", headers=headers)
    assert "75" in old_export.text and "APP-1" in old_export.text
    requirement = detail(client, headers, case)["requirements"][0]
    ok(client.post(f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/waive", headers=headers, json={"revision": requirement["revision"], "reason": "Approved exception"}))
    changed = detail(client, headers, case)
    assert changed["case"]["status"] == "evidence_changed"
    assert changed["reviews"][0]["scores"]["academic"] == 75
    assert client.post(f"/api/ops/cases/{case['id']}/review/sign", headers=reviewer_headers, json=form).status_code == 409
    assert "APP-1" not in client.get("/api/ops/exchange/scores.csv", headers=headers).text
    form["revision"] = changed["case"]["case_revision"]
    form["scores"]["academic"] = 80
    ok(client.post(f"/api/ops/cases/{case['id']}/review/sign", headers=reviewer_headers, json=form), 201)
    assert len(detail(client, headers, case)["reviews"]) == 2


def test_invitation_replay_workspace_roles_and_assigned_access(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers)
    case = create_case(client, headers, p)
    reviewer_headers, reviewer, token = invite(client, headers)
    assert client.post(f"/api/ops/public/invitations/{token}", json={"password": "SecureTestPassword!"}).status_code == 410
    assert client.get(f"/api/ops/cases/{case['id']}", headers=reviewer_headers).status_code == 403
    assert client.get("/api/ops/cases", headers=reviewer_headers).json() == []
    assert client.post("/api/ops/programs", headers=reviewer_headers, json={"name": "Forbidden", "code": "F"}).status_code == 403
    finance_headers, _, _ = invite(client, headers, "finance@example.edu", "finance_viewer")
    assert client.get("/api/ops/cases", headers=finance_headers).status_code == 403
    assert client.get("/api/ops/billing", headers=finance_headers).status_code == 200
    assert client.get("/api/ops/reports", headers=finance_headers).status_code == 200


def test_workload_and_program_coverage_are_enforced(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers)
    other_p, _ = setup_program(client, headers, code="OTHER")
    case = create_case(client, headers, p)
    complete_number(client, headers, case)
    _, reviewer, _ = invite(client, headers)
    ok(client.put(f"/api/ops/people/{reviewer['user_id']}/coverage", headers=headers, json={"workload_limit": 1, "program_ids": [other_p["id"]]}))
    assert client.post(f"/api/ops/cases/{case['id']}/assign", headers=headers, json={"reviewer_id": reviewer["user_id"]}).status_code == 409
    ok(client.put(f"/api/ops/people/{reviewer['user_id']}/coverage", headers=headers, json={"workload_limit": 1, "program_ids": [p["id"]]}))
    assign(client, headers, case, reviewer)
    second = create_case(client, headers, p, "APP-2")
    complete_number(client, headers, second)
    assert client.post(f"/api/ops/cases/{second['id']}/assign", headers=headers, json={"reviewer_id": reviewer["user_id"]}).status_code == 409


def test_secure_upload_cardinality_replacement_and_original_access(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers, document=True)
    case = create_case(client, headers, p)
    requirement = detail(client, headers, case)["requirements"][0]
    base = f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}"
    link = ok(client.post(base + "/links", headers=headers, json={}), 201)
    assert client.get(f"/api/ops/public/uploads/{link['token']}").status_code == 200
    first = ok(client.post(f"/api/ops/public/uploads/{link['token']}", files={"file": ("transcript.txt", b"Academic transcript", "text/plain")}), 201)
    assert set(first) == {"status", "message"}  # No case/candidate details leak.
    assert client.post(f"/api/ops/public/uploads/{link['token']}", files={"file": ("again.txt", b"x")}).status_code == 410
    initial = detail(client, headers, case)["documents"][0]
    wait_for_terminal(client, initial["job_id"], headers)
    assert client.post(base + "/upload", headers=headers, files={"file": ("second.txt", b"x")}).status_code == 409
    second = ok(client.post(base + "/upload", headers=headers, data={"slot": "1"}, files={"file": ("updated.txt", b"updated transcript")}), 201)
    docs = detail(client, headers, case)["documents"]
    assert len(docs) == 2 and sum(d["current"] for d in docs) == 1
    assert second["version"] == 2
    assert client.get(f"/api/ops/cases/{case['id']}/documents/{initial['id']}/source").status_code == 401
    assert client.get(f"/api/ops/cases/{case['id']}/documents/{initial['id']}/source", headers=headers).content == b"Academic transcript"
    analysis = ok(client.get(f"/api/ops/cases/{case['id']}/documents/{initial['id']}/evidence", headers=headers))
    assert client.put(f"/api/ops/cases/{case['id']}/documents/{initial['id']}/evidence", headers=headers, json={"revision": analysis["revision"], "status": "verified", "value": "transcript", "reason": "Original"}).status_code == 409


def test_expired_and_revoked_secure_links_and_binary_validation(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers, document=True)
    case = create_case(client, headers, p)
    requirement = detail(client, headers, case)["requirements"][0]
    base = f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}"
    link = ok(client.post(base + "/links", headers=headers, json={}), 201)
    ok(client.post(f"/api/ops/links/{link['id']}/revoke", headers=headers))
    assert client.get(f"/api/ops/public/uploads/{link['token']}").status_code == 410
    expired = ok(client.post(base + "/links", headers=headers, json={}), 201)
    with factory() as db:
        record = db.scalar(select(WorkflowRecord).where(WorkflowRecord.resource_key == expired["id"]))
        w.change(record, expires_at="2020-01-01T00:00:00+00:00")
        db.commit()
    assert client.get(f"/api/ops/public/uploads/{expired['token']}").status_code == 410
    assert client.post(base + "/upload", headers=headers, files={"file": ("wrong.pdf", b"Not a PDF")}).status_code == 400


def test_source_corrections_require_quote_and_reason_and_stale_edits_fail(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers, document=True)
    case = create_case(client, headers, p)
    requirement = detail(client, headers, case)["requirements"][0]
    document = ok(client.post(f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/upload", headers=headers, files={"file": ("transcript.txt", b"Academic transcript")}), 201)
    wait_for_terminal(client, document["job_id"], headers)
    with factory() as db:
        content = db.get(ExtractedDocumentContent, document["id"])
        content.raw_text = "Academic transcript\nGPA: 3.5\nCorrected GPA: 3.6 / 4.0"
        content.pages = [{"page_number": 2, "text": content.raw_text}]
        db.commit()
    endpoint = f"/api/ops/cases/{case['id']}/documents/{document['id']}/evidence"
    analysis = ok(client.get(endpoint, headers=headers))
    field = analysis["fields"][0]
    body = {"revision": analysis["revision"], "field_id": field["id"], "status": "corrected", "value": "3.6 / 4.0", "reason": "Source correction", "source_index": 0, "source_quote": "Not in the source"}
    assert client.put(endpoint, headers=headers, json=body).status_code == 400
    body["source_quote"] = "Corrected GPA: 3.6 / 4.0"
    corrected = ok(client.put(endpoint, headers=headers, json=body))
    assert corrected["fields"][0]["suggested_value"] == field["suggested_value"]
    assert corrected["fields"][0]["staff_citation"]["page"] == 2
    assert len(corrected["history"]) == 1
    assert client.put(endpoint, headers=headers, json=body).status_code == 409


def test_conservative_page_metadata_does_not_invent_locators():
    analysis = analyze("", [{"page_number": 2, "text": "Academic transcript\nGPA: 3.5"}, {"page_number": 2, "text": "GPA: 3.7"}])
    assert all(page["number"] is None for page in analysis["pages"])
    assert all(field["citations"][0]["page"] is None for field in analysis["fields"])
    assert "GPA scale" in analysis["fields"][0]["uncertainty"]


def test_csv_preview_conflicts_commit_replay_and_withdrawal(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    setup_program(client, headers)
    csv_text = "external_application_id,external_candidate_id,applicant_name,program_code,cycle,application_status\nA1,C1,Ada Student,DS,Fall 2027,submitted\nA2,C2,Bob Student,UNKNOWN,Fall 2027,submitted\n"
    batch = ok(client.post("/api/ops/exchange/import/preview", headers=headers, files={"file": ("applications.csv", csv_text.encode(), "text/csv")}), 201)
    assert [r["status"] for r in batch["rows"]] == ["create", "invalid"]
    assert client.get("/api/ops/cases", headers=headers).json() == []
    committed = ok(client.post(f"/api/ops/exchange/import/{batch['id']}/commit", headers=headers))
    assert committed["rows"][0]["status"] == "created"
    assert client.post(f"/api/ops/exchange/import/{batch['id']}/commit", headers=headers).status_code == 409
    again = ok(client.post("/api/ops/exchange/import/preview", headers=headers, files={"file": ("same.csv", csv_text.encode())}), 201)
    assert again["rows"][0]["status"] == "unchanged"
    withdrawn = csv_text.replace("A1,C1,Ada Student,DS,Fall 2027,submitted", "A1,C1,Ada Student,DS,Fall 2027,withdrawn")
    batch2 = ok(client.post("/api/ops/exchange/import/preview", headers=headers, files={"file": ("withdrawn.csv", withdrawn.encode())}), 201)
    ok(client.post(f"/api/ops/exchange/import/{batch2['id']}/commit", headers=headers))
    assert client.get("/api/ops/cases", headers=headers).json() == []
    assert client.get("/api/ops/cases?status=withdrawn", headers=headers).json()[0]["status"] == "withdrawn"


def test_price_snapshots_invoice_idempotency_and_replacement_charges(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers, document=True)
    case = create_case(client, headers, p)
    requirement = detail(client, headers, case)["requirements"][0]
    base = f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/upload"
    ok(client.post("/api/ops/billing/contracts", headers=headers, json={"pages_per_unit": 10, "price_per_unit_cents": 250, "currency": "USD", "billing_timezone": "UTC"}), 201)
    document = ok(client.post(base, headers=headers, files={"file": ("transcript.txt", b"Academic transcript")}), 201)
    wait_for_terminal(client, document["job_id"], headers)
    with factory() as db:
        record_usage(db, db.get(IntakeJob, document["job_id"]))
        db.commit()
    before = ok(client.get("/api/ops/billing", headers=headers))["usage"]
    assert len(before) == 1 and before[0]["charge_cents"] == 250
    ok(client.post("/api/ops/billing/contracts", headers=headers, json={"pages_per_unit": 1, "price_per_unit_cents": 500, "currency": "USD", "billing_timezone": "UTC"}), 201)
    assert ok(client.get("/api/ops/billing", headers=headers))["usage"][0]["charge_cents"] == 250
    invoices = ok(client.post("/api/ops/billing/invoices", headers=headers), 201)
    assert invoices[0]["total_cents"] == 250
    assert client.post("/api/ops/billing/invoices", headers=headers).status_code == 409
    invoice_id = invoices[0]["id"]
    ok(client.put(f"/api/ops/billing/invoices/{invoice_id}", headers=headers, json={"status": "issued"}))
    ok(client.put(f"/api/ops/billing/invoices/{invoice_id}", headers=headers, json={"status": "paid"}))
    assert client.put(f"/api/ops/billing/invoices/{invoice_id}", headers=headers, json={"status": "draft"}).status_code == 409
    failed = ok(client.post(base, headers=headers, data={"slot": "1"}, files={"file": ("parser_fail.txt", b"x")}), 201)
    assert wait_for_terminal(client, failed["job_id"], headers)["status"] == "failed"
    assert len(ok(client.get("/api/ops/billing", headers=headers))["usage"]) == 1


def test_reports_platform_and_training_enablement(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    assert client.get("/api/ops/training", headers=headers).status_code == 404
    ok(client.put("/api/ops/settings", headers=headers, json={"brand_name": "University admissions", "brand_color": "#216b58", "fictional_example_enabled": True}))
    assert len(ok(client.get("/api/ops/training", headers=headers))["criteria"]) == 2
    assert client.get("/api/ops/reports", headers=headers).status_code == 200
    assert client.get("/api/ops/platform", headers=headers).status_code == 200


def test_existing_database_upgrade_is_idempotent(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    p, _ = setup_program(client, headers)
    case = create_case(client, headers, p)
    with factory() as db:
        w.migrate_legacy(db)
        w.migrate_legacy(db)
        db.commit()
        assert db.scalar(select(Application).where(Application.application_id == case["id"])) is not None
        assert len(w.rows(db, ok(client.get("/api/ops/context", headers=headers))["university_id"], "case")) == 1


def test_legacy_upload_cannot_bypass_managed_evidence_slots(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    program, _ = setup_program(client, headers, document=True)
    case = create_case(client, headers, program)
    for fields in ({"application_id": case["id"]}, {"student_unique_id": "C-1", "program_applied": "Data Science", "intake_term": "Fall 2027", "applicant_name": "Changed Name"}):
        response = client.post("/api/upload", headers=headers, data=fields, files={"file": ("transcript.txt", b"Academic transcript")})
        assert response.status_code == 409, response.text
    unchanged = detail(client, headers, case)
    assert unchanged["case"]["applicant_name"] == "Ada Student"
    assert not unchanged["documents"]


def test_conflicting_current_sources_block_signoff_until_staff_resolves_them(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    program, _ = setup_program(client, headers, document=True, max_count=2)
    case = create_case(client, headers, program)
    requirement = detail(client, headers, case)["requirements"][0]
    documents = []
    for value in ("3.5 / 4.0", "3.8 / 4.0"):
        document = ok(client.post(f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/upload", headers=headers, files={"file": ("transcript.txt", b"Academic transcript")}), 201)
        wait_for_terminal(client, document["job_id"], headers)
        with factory() as db:
            content = db.get(ExtractedDocumentContent, document["id"])
            content.raw_text = f"Academic transcript\nGPA: {value}"
            content.pages = [{"page_number": 1, "text": content.raw_text}]
            db.commit()
        documents.append(document)
    current = detail(client, headers, case)
    assert current["evidence"]["conflicts"]
    reviewer_headers, reviewer, _ = invite(client, headers)
    assign(client, headers, case, reviewer)
    form = {"scores": {"academic": 80}, "revision": current["case"]["case_revision"], "source_verified": True}
    assert client.post(f"/api/ops/cases/{case['id']}/review/sign", headers=reviewer_headers, json=form).status_code == 409
    endpoint = f"/api/ops/cases/{case['id']}/documents/{documents[1]['id']}/evidence"
    analysis = ok(client.get(endpoint, headers=headers))
    field = analysis["fields"][0]
    ok(client.put(endpoint, headers=headers, json={"revision": analysis["revision"], "field_id": field["id"], "status": "rejected", "reason": "This transcript refers to a different grading period"}))
    resolved = detail(client, reviewer_headers, case)
    assert not resolved["evidence"]["conflicts"]
    form["revision"] = resolved["case"]["case_revision"]
    ok(client.post(f"/api/ops/cases/{case['id']}/review/sign", headers=reviewer_headers, json=form), 201)


def test_structured_value_cardinality_and_replacement_history(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    program = ok(client.post("/api/ops/programs", headers=headers, json={"name": "Value collection", "code": "VALUES"}), 201)
    template = ok(client.post(f"/api/ops/programs/{program['id']}/draft", headers=headers, json={"name": "Values", "requirements": [{"key": "scores", "label": "Exam scores", "capture_type": "number", "min_count": 2, "max_count": 2}], "criteria": []}))
    ok(client.post(f"/api/ops/versions/{template['id']}/publish", headers=headers, json={"revision": template["revision"], "reason": "Ready"}))
    case = create_case(client, headers, program)
    for value, completeness in (("80", 0), ("90", 100)):
        requirement = detail(client, headers, case)["requirements"][0]
        endpoint = f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/value"
        ok(client.put(endpoint, headers=headers, json={"value": value, "revision": requirement["revision"]}))
        assert detail(client, headers, case)["case"]["completeness"] == completeness
    requirement = detail(client, headers, case)["requirements"][0]
    assert client.put(endpoint, headers=headers, json={"value": "91", "revision": requirement["revision"]}).status_code == 409
    changed = ok(client.put(endpoint, headers=headers, json={"value": "91", "slot": 2, "revision": requirement["revision"]}))
    assert [value["value"] for value in changed["values"]] == [80, 91]
    assert changed["value_history"][-1]["before"]["value"] == 90
    assert len(changed["value_history"]) == 3


def test_detail_returns_committed_requirement_revision_after_processing(intake_client):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    program, _ = setup_program(client, headers, document=True)
    case = create_case(client, headers, program)
    requirement = detail(client, headers, case)["requirements"][0]
    document = ok(client.post(f"/api/ops/cases/{case['id']}/requirements/{requirement['id']}/upload", headers=headers, files={"file": ("transcript.txt", b"Academic transcript")}), 201)
    wait_for_terminal(client, document["job_id"], headers)
    accepted = detail(client, headers, case)["requirements"][0]
    with factory() as db:
        stored = db.scalar(select(WorkflowRecord).where(WorkflowRecord.resource_key == accepted["id"]))
        assert stored.revision == accepted["revision"]
    ok(client.post(f"/api/ops/cases/{case['id']}/requirements/{accepted['id']}/waive", headers=headers, json={"revision": accepted["revision"], "reason": "Approved"}))


def test_integration_api_keys_keep_intake_access_without_staff_workspace_access(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    university = ok(client.post("/api/auth/universities", headers=headers, json={"name": "API key university"}), 201)
    api_headers = {"Authorization": "ApiKey " + university["api_key"]}
    assert client.get("/api/jobs/status", headers=api_headers).status_code == 200
    assert client.get("/api/ops/context", headers=api_headers).status_code == 401
    assert client.get("/api/ops/platform", headers=api_headers).status_code == 401


def test_optional_invitation_email_delivery_and_failure_preserve_links(intake_client, monkeypatch):
    from app.config import get_settings
    from app.services import workflow_mail
    client, _, _ = intake_client
    headers = auth_headers(client)
    plain = ok(client.post("/api/ops/invitations", headers=headers, json={"email": "plain@example.test", "role": "reviewer"}), 201)
    assert plain["email_delivery"] == "not_requested"
    missing = ok(client.post("/api/ops/invitations", headers=headers, json={"email": "missing@example.test", "role": "reviewer", "deliver_email": True}), 201)
    assert missing["email_delivery"] == "not_configured"
    settings = client.app.dependency_overrides[get_settings]().model_copy(update={"smtp_host": "mock-smtp", "smtp_username": "sender@example.test", "smtp_password": "test-only-password", "public_app_url": "https://admissions.example.test"})
    client.app.dependency_overrides[get_settings] = lambda: settings
    captured = []
    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("mock-smtp", 587, 15)
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def starttls(self, context): assert context is not None
        def login(self, username, password): assert (username, password) == ("sender@example.test", "test-only-password")
        def send_message(self, message): captured.append(message)
    monkeypatch.setattr(workflow_mail.smtplib, "SMTP", FakeSMTP)
    delivered = ok(client.post("/api/ops/invitations", headers=headers, json={"email": "sent@example.test", "role": "reviewer", "deliver_email": True}), 201)
    assert delivered["email_delivery"] == "sent"
    assert captured[0]["To"] == "sent@example.test"
    assert "https://admissions.example.test/" + delivered["path"] in captured[0].get_content()
    def failed_transport(*_, **__): raise OSError("Unavailable")
    monkeypatch.setattr(workflow_mail.smtplib, "SMTP", failed_transport)
    failed = ok(client.post("/api/ops/invitations", headers=headers, json={"email": "failed@example.test", "role": "reviewer", "deliver_email": True}), 201)
    assert failed["email_delivery"] == "failed"
    token = failed["path"].rsplit("/", 1)[-1]
    assert client.get("/api/ops/public/invitations/" + token).status_code == 200
