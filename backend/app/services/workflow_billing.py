import math
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import AuditLog, IntakeJob, LocalUser, University
from app.services.workflow import add, change, find, now, rows, view


def record_usage(db: Session, job: IntakeJob) -> None:
    """One successful document, one immutable price snapshot (including replacements)."""
    tenant = job.application.university_id
    if not tenant or job.status != "completed" or find(db, tenant, "usage", job.document_id, required=False):
        return
    contracts = rows(db, tenant, "contract")
    finished = job.finished_at or datetime.now(timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    eligible = [c for c in contracts if datetime.fromisoformat(c.payload["effective_from"]) <= finished]
    contract = eligible[-1] if eligible else None
    university = db.get(University, tenant)
    upload_audit = db.scalar(select(AuditLog).where(AuditLog.document_id == job.document_id, AuditLog.action.in_(["document_upload", "requirement_document_upload"])).order_by(AuditLog.timestamp))
    uploader = db.scalar(select(LocalUser).where(LocalUser.email == upload_audit.actor)) if upload_audit else None
    default_price = round((uploader.billing_rate_per_unit if uploader else 1.0) * 100)
    terms = contract.payload if contract else {"pages_per_unit": university.pages_per_billable_unit, "price_per_unit_cents": default_price, "currency": "USD", "billing_timezone": "UTC", "invoice_terms_days": 30}
    pages = job.document.page_count
    if not pages or pages < 1:
        return  # Never invent a page count for billing.
    units = math.ceil(pages / terms["pages_per_unit"])
    add(db, tenant, "usage", {"document_id": job.document_id, "job_id": job.job_id, "filename": job.document.original_filename,
        "page_count": pages, "units": units, "charge_cents": units * terms["price_per_unit_cents"],
        "contract_id": contract.resource_key if contract else None, "contract_snapshot": dict(terms), "currency": terms["currency"],
        "occurred_at": finished.isoformat(), "invoice_id": None}, key=job.document_id, application_id=job.application_id)


def generate_invoices(db: Session, tenant: str) -> list[dict]:
    usage = [u for u in rows(db, tenant, "usage") if not u.payload["invoice_id"]]
    if not usage:
        raise HTTPException(409, "There is no unbilled successful document usage.")
    result = []
    for currency in sorted({u.payload["currency"] for u in usage}):
        items = [u for u in usage if u.payload["currency"] == currency]
        from zoneinfo import ZoneInfo
        zone = items[-1].payload["contract_snapshot"]["billing_timezone"]
        local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(zone))
        terms = items[-1].payload["contract_snapshot"]["invoice_terms_days"]
        invoice = add(db, tenant, "invoice", {"currency": currency, "status": "draft", "total_cents": sum(u.payload["charge_cents"] for u in items),
            "period_start": min(u.payload["occurred_at"] for u in items), "period_end": max(u.payload["occurred_at"] for u in items),
            "due_at": (local_now + timedelta(days=terms)).date().isoformat(), "billing_timezone": zone,
            "lines": [view(u) for u in items], "history": [{"status": "draft", "at": now()}]})
        change(invoice, invoice_number=f"AA-{local_now:%Y%m}-{invoice.resource_key[:8].upper()}")
        for item in items:
            change(item, invoice_id=invoice.resource_key)
        result.append(view(invoice))
    return result


def invoice_status(db: Session, tenant: str, invoice_id: str, status: str):
    invoice = find(db, tenant, "invoice", invoice_id)
    previous = invoice.payload["status"]
    transitions = {"draft": {"issued", "void"}, "issued": {"paid", "overdue", "void"}, "overdue": {"paid", "void"}, "paid": {"credited"}, "void": set(), "credited": set()}
    if status != previous and status not in transitions[previous]:
        raise HTTPException(409, f"Cannot change an invoice from {previous} to {status}.")
    if status != previous:
        change(invoice, status=status, history=[*invoice.payload["history"], {"status": status, "at": now()}])
        if status == "void":
            for item in rows(db, tenant, "usage"):
                if item.payload["invoice_id"] == invoice_id:
                    change(item, invoice_id=None)
    return invoice
