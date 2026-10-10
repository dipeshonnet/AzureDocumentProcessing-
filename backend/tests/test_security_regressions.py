"""Regression coverage for tenant permissions and credential recovery."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuthSession, LocalUser, University
from app.services.local_auth import create_session, hash_password
from test_intake_operations import intake_client, auth_headers
from test_operations_rubrics import sample_rubric_payload


def test_api_key_never_falls_back_to_superadmin(intake_client):
    client, factory, _ = intake_client
    staff = auth_headers(client)
    with factory() as db:
        university = db.scalar(select(University).where(University.name == "Default University"))
        headers = {"Authorization": "ApiKey " + university.api_key}
    for path in ("/api/auth/universities", "/api/auth/users", "/api/jobs/status",
                 "/api/rubrics/diagnostics/storage"):
        assert client.get(path, headers=headers).status_code == 401
    assert client.get("/api/auth/universities", headers=staff).status_code == 200


def test_tenant_admin_api_key_keeps_tenant_access(intake_client):
    client, _, _ = intake_client
    staff = auth_headers(client)
    university = client.post("/api/auth/universities", headers=staff,
                             json={"name": "Integration University"}).json()
    headers = {"Authorization": "ApiKey " + university["api_key"]}
    users = client.get("/api/auth/users", headers=headers)
    assert users.status_code == 200
    assert all(user["university_id"] == university["university_id"] for user in users.json())
    assert client.get("/api/jobs/status", headers=headers).status_code == 200
    assert client.get("/api/auth/universities", headers=headers).status_code == 403
    assert client.get("/api/ops/context", headers=headers).status_code == 401
    assert client.get("/api/auth/users", headers={**headers, "X-Workspace-Id": "another"}).status_code == 403


def test_password_change_revokes_other_sessions_only(intake_client):
    client, factory, _ = intake_client
    current = auth_headers(client)
    stolen = auth_headers(client)
    with factory() as db:
        university = db.scalar(select(University))
        other = LocalUser(email="other@example.edu", role="admin", university_id=university.university_id,
                          password_hash=hash_password("OtherPassword!"))
        db.add(other)
        db.flush()
        other_headers = {"Authorization": "Bearer " + create_session(db=db, user=other)}
    payload = {"current_password": "EverydayAI", "new_password": "ChangedPassword!",
               "confirm_new_password": "ChangedPassword!"}
    assert client.put("/api/auth/password", headers=current, json=payload).status_code == 200
    assert client.get("/api/auth/me", headers=stolen).status_code == 401
    assert client.get("/api/auth/me", headers=current).status_code == 200
    assert client.get("/api/auth/me", headers=other_headers).status_code == 200
    assert client.post("/api/auth/login", json={"email": "superadmin", "password": "EverydayAI"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "superadmin", "password": "ChangedPassword!"}).status_code == 200


@pytest.mark.parametrize("age", [timedelta(hours=24), timedelta(days=90), timedelta(hours=-1)])
def test_expired_or_future_session_rejected(intake_client, age):
    client, factory, _ = intake_client
    headers = auth_headers(client)
    with factory() as db:
        session = db.get(AuthSession, headers["Authorization"].removeprefix("Bearer "))
        session.created_at = (datetime.now(timezone.utc) - age).replace(tzinfo=None)
        db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=auth_headers(client)).status_code == 200


def test_shared_rubric_is_readable_but_only_tenant_copy_is_writable(intake_client):
    client, _, _ = intake_client
    staff = auth_headers(client)
    shared = client.get("/api/rubrics", headers=staff).json()[0]
    tenant = client.post("/api/auth/universities", headers=staff, json={"name": "Rubric University"}).json()
    headers = {"Authorization": "ApiKey " + tenant["api_key"]}
    assert client.get(f"/api/rubrics/{shared['rubric_id']}", headers=headers).status_code == 200
    payload = sample_rubric_payload()
    update = {**payload, "version": 2, "is_active": True}
    assert client.put(f"/api/rubrics/{shared['rubric_id']}", headers=headers, json=update).status_code == 403
    assert client.get(f"/api/rubrics/{shared['rubric_id']}", headers=staff).json()["name"] == shared["name"]
    copied = client.post("/api/rubrics", headers=headers, json=payload)
    assert copied.status_code == 201
    assert client.put(f"/api/rubrics/{copied.json()['rubric_id']}", headers=headers, json=update).status_code == 200
