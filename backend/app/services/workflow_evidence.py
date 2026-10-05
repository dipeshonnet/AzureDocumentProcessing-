"""Conservative, source-linked suggestions ported from DocumentProcessing.

Staff decisions stay separate from OCR, identity, requirement values and scores.
"""
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import ExtractedDocumentContent, IntakeJob
from app.schemas.workflow import EvidenceInput
from app.services.workflow import add, audit, change, check_revision, evidence_changed, find, now, rows, view

DOCUMENT_TYPES = {"transcript": "Academic transcript", "recommendation": "Recommendation letter", "statement": "Personal statement",
                  "resume": "Résumé / CV", "english_test": "English test report", "other": "Other document"}
TYPE_SIGNALS = {
    "transcript": r"\b(?:academic|official|unofficial) transcript\b|\btranscript of records\b|^\s*transcript\s*$",
    "recommendation": r"\bletter of recommendation\b|\brecommendation letter\b",
    "statement": r"\bpersonal statement\b|\bstatement of purpose\b",
    "resume": r"\bcurriculum vitae\b|^\s*(?:resume|résumé)\s*$",
    "english_test": r"\bIELTS\b|\bTOEFL\b",
}
FIELD_PATTERNS = {
    "applicant_name": ("Applicant name", r"(?:applicant|student) name\s*[:：]\s*([^\n|]{2,100})"),
    "applicant_id": ("Applicant / student ID", r"(?:applicant|student|application) (?:id|number)\s*[:：#]\s*([\w-]{2,50})"),
    "gpa": ("GPA (as stated)", r"\b(?:(?:cumulative|overall)\s+)?(?:GPA|grade point average)\s*[:：=]?\s*(\d+(?:\.\d+)?(?:\s*(?:/|out of)\s*\d+(?:\.\d+)?)?)"),
    "degree": ("Degree (as stated)", r"\bdegree(?: awarded)?\s*[:：]\s*([^\n|]{2,100})"),
    "ielts": ("IELTS overall band", r"\b(?:IELTS\s+(?:overall\s+)?(?:band|score)|overall band(?: score)?)\s*[:：=]?\s*(\d+(?:\.\d+)?)"),
    "toefl": ("TOEFL total score", r"\bTOEFL\s+(?:iBT\s+)?(?:total\s+)?score\s*[:：=]?\s*(\d+(?:\.\d+)?)"),
}


def analyze(text: str, source_pages: list[dict]) -> dict:
    pages = []
    for item in source_pages:
        number = item.get("page_number", item.get("page", item.get("number")))
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            number = None
        content = item.get("text") or item.get("md") or ""
        if isinstance(content, str) and content.strip():
            pages.append({"number": number, "text": content})
    pages = pages or [{"number": None, "text": text}]
    numbers = [p["number"] for p in pages if p["number"] is not None]
    for page in pages:
        if page["number"] is not None and numbers.count(page["number"]) > 1:
            page["number"] = None
    signals = []
    for kind, pattern in TYPE_SIGNALS.items():
        for page in pages:
            match = re.search(pattern, page["text"], re.I | re.M)
            if match:
                signals.append({"type": kind, "page": page["number"], "quote": match.group(0)})
                break
    types = {s["type"] for s in signals}
    extracted = {}
    for page_index, page in enumerate(pages):
        for line_number, line in enumerate(page["text"].splitlines(), 1):
            for key, (label, pattern) in FIELD_PATTERNS.items():
                for match in re.finditer(pattern, line, re.I):
                    value = match.group(1).strip(" .|")
                    citation = {"page": page["number"], "page_index": page_index, "line": line_number, "quote": line[:1000]}
                    extracted.setdefault((key, value), {"label": label, "citations": []})["citations"].append(citation)
    fields = []
    for (key, value), item in extracted.items():
        reasons = []
        if any(c["page"] is None for c in item["citations"]):
            reasons.append("Source page unavailable; verify against the original.")
        if key == "gpa" and not re.search(r"/|out of", value):
            reasons.append("GPA scale is not stated; do not infer equivalence.")
        fields.append({"id": str(uuid4()), "key": key, "label": item["label"], "suggested_value": value,
            "citations": item["citations"], "uncertainty": " ".join(reasons), "staff_status": "pending", "staff_value": None,
            "staff_citation": None, "reason": ""})
    return {"pages": pages, "signals": signals, "fields": fields, "proposed_type": next(iter(types)) if len(types) == 1 else "other",
            "classification_state": "suggested" if len(types) == 1 else "conflicting" if types else "uncertain",
            "staff_status": "pending", "staff_type": None, "history": [], "engine": "explicit-text-v1"}


def ensure_analysis(db: Session, tenant: str, document) -> object | None:
    existing = find(db, tenant, "evidence", document.resource_key, required=False)
    if existing:
        return existing
    job = db.get(IntakeJob, document.payload["job_id"])
    if job is None or job.status != "completed":
        return None
    extracted = db.get(ExtractedDocumentContent, document.resource_key)
    text = extracted.raw_text or "" if extracted else ""
    pages = extracted.pages or [] if extracted else []
    return add(db, tenant, "evidence", analyze(text, pages), key=document.resource_key, application_id=document.application_id)


def summary(db: Session, tenant: str, case_id: str) -> dict:
    documents, values, issues, conflicts = [], {}, [], []
    for document in rows(db, tenant, "document", case_id):
        if not document.payload["current"]:
            continue
        analysis = ensure_analysis(db, tenant, document)
        if analysis is None:
            continue
        data = {**view(analysis), "document_id": document.resource_key, "filename": document.payload["filename"], "requirement_id": document.payload["requirement_id"]}
        documents.append(data)
        if data["staff_status"] == "pending" and data["classification_state"] in {"conflicting", "uncertain"}:
            issues.append(f"Confirm document type for {data['filename']}.")
        for field in data["fields"]:
            if field["staff_status"] == "rejected":
                continue
            effective = field["staff_value"] if field["staff_status"] == "corrected" else field["suggested_value"]
            values.setdefault(field["key"], []).append({**field, "effective_value": effective, "document_id": document.resource_key, "filename": data["filename"]})
            if field["uncertainty"] and field["staff_status"] == "pending":
                issues.append(f"Verify or reject {field['label']} in {data['filename']}.")
    for key, entries in values.items():
        if len({re.sub(r"\s+", " ", e["effective_value"].strip().casefold()) for e in entries}) > 1:
            conflicts.append({"key": key, "label": entries[0]["label"], "entries": entries})
            issues.append(f"Resolve conflicting {entries[0]['label'].lower()} values.")
    return {"documents": documents, "issues": issues, "conflicts": conflicts}


def correct(db: Session, authenticated, document, payload: EvidenceInput):
    tenant = authenticated.university_id
    analysis = ensure_analysis(db, tenant, document)
    job = db.get(IntakeJob, document.payload["job_id"])
    if analysis is None or not document.payload["current"] or job.status != "completed":
        raise HTTPException(409, "Only current completed evidence can be corrected.")
    check_revision(analysis, payload.revision)
    fields = [dict(field) for field in analysis.payload["fields"]]
    if payload.field_id:
        field = next((f for f in fields if f["id"] == payload.field_id), None)
        if field is None:
            raise HTTPException(404, "Extracted field not found.")
        if payload.status in {"corrected", "rejected"} and not payload.reason:
            raise HTTPException(400, "Explain the correction or rejection.")
        citation = None
        if payload.status == "corrected":
            pages = analysis.payload["pages"]
            if not payload.value or payload.source_index is None or payload.source_index >= len(pages) or not payload.source_quote:
                raise HTTPException(400, "Provide a corrected value, source reference, supporting quote and reason.")
            page = pages[payload.source_index]
            if payload.source_quote not in page["text"]:
                raise HTTPException(400, "The supporting quote must occur in the selected source text.")
            citation = {"page_index": payload.source_index, "page": page["number"], "quote": payload.source_quote}
        before = dict(field)
        field.update(staff_status=payload.status, staff_value=payload.value if payload.status == "corrected" else None,
                     staff_citation=citation, reason=payload.reason, actor=authenticated.user.email)
        change(analysis, fields=fields)
        after = dict(field)
    else:
        if payload.status != "verified" or payload.value not in DOCUMENT_TYPES or not payload.reason:
            raise HTTPException(400, "Choose a document type and explain your confirmation.")
        before = {k: analysis.payload[k] for k in ("proposed_type", "staff_type", "staff_status")}
        change(analysis, staff_type=payload.value, staff_status="verified", staff_reason=payload.reason)
        after = {"staff_type": payload.value, "staff_status": "verified", "reason": payload.reason}
    change(analysis, history=[*analysis.payload["history"], {"actor": authenticated.user.email, "at": now(), "before": before, "after": after}])
    evidence_changed(db, tenant, document.application_id)
    audit(db, authenticated, "evidence_staff_decision", document.application_id, document_id=document.resource_key, field_id=payload.field_id, status=payload.status)
    return analysis
