from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models import LocalUser
from app.models.admissions import University, SavedRubric
from backend.tests.test_intake_operations import intake_client, auth_headers

def test_superadmin_can_manage_universities(intake_client) -> None:
    client, session_factory, _ = intake_client
    
    # 1. Login as Superadmin
    super_headers = auth_headers(client)
    
    # 2. List universities (should succeed)
    response = client.get("/api/auth/universities", headers=super_headers)
    assert response.status_code == 200
    unis = response.json()
    assert len(unis) >= 1
    assert any(u["name"] == "Default University" for u in unis)
    
    # 3. Create a new university
    create_res = client.post(
        "/api/auth/universities",
        headers=super_headers,
        json={"name": "Harvard University"}
    )
    assert create_res.status_code == 201
    harvard = create_res.json()
    assert harvard["name"] == "Harvard University"
    assert harvard["api_key"] is not None
    
    # Verify initial admin user got seeded in DB
    with session_factory() as session:
        admin_user = session.query(LocalUser).filter_by(
            email="admin@harvarduniversity.edu"
        ).first()
        assert admin_user is not None
        assert admin_user.role == "admin"
        assert admin_user.university_id == harvard["university_id"]


def test_university_admin_reviewer_flow_and_api_keys(intake_client) -> None:
    client, session_factory, _ = intake_client
    super_headers = auth_headers(client)
    
    # 1. Create university
    create_res = client.post(
        "/api/auth/universities",
        headers=super_headers,
        json={"name": "Yale University"}
    )
    assert create_res.status_code == 201
    yale = create_res.json()
    
    # 2. Login as the newly provisioned Yale Admin
    login_res = client.post(
        "/api/auth/login",
        json={"email": "admin@yaleuniversity.edu", "password": "EverydayAI"}
    )
    assert login_res.status_code == 200
    yale_admin_token = login_res.json()["token"]
    admin_headers = {"Authorization": f"Bearer {yale_admin_token}"}
    
    # 3. Yale Admin creates a reviewer under their university
    rev_res = client.post(
        "/api/auth/users/create-reviewer",
        headers=admin_headers,
        json={"email": "reviewer@yale.edu", "password": "ReviewerPassword123"}
    )
    assert rev_res.status_code == 200
    reviewer = rev_res.json()
    assert reviewer["email"] == "reviewer@yale.edu"
    assert reviewer["role"] == "admissions_reviewer"
    
    # Verify in DB
    with session_factory() as session:
        db_reviewer = session.query(LocalUser).filter_by(email="reviewer@yale.edu").first()
        assert db_reviewer is not None
        assert db_reviewer.university_id == yale["university_id"]
        
    # 4. Try creating reviewer from reviewer account (should be 403)
    rev_login = client.post(
        "/api/auth/login",
        json={"email": "reviewer@yale.edu", "password": "ReviewerPassword123"}
    )
    assert rev_login.status_code == 200
    rev_headers = {"Authorization": f"Bearer {rev_login.json()['token']}"}
    
    bad_res = client.post(
        "/api/auth/users/create-reviewer",
        headers=rev_headers,
        json={"email": "bad@yale.edu", "password": "SomePassword123"}
    )
    assert bad_res.status_code == 403

    # 5. Yale Admin configures Webhook settings
    webhook_res = client.put(
        "/api/auth/universities/integration",
        headers=admin_headers,
        json={"webhook_url": "https://yale.edu/admissions-webhook"}
    )
    assert webhook_res.status_code == 200
    assert webhook_res.json()["webhook_url"] == "https://yale.edu/admissions-webhook"
    
    # 6. Yale Admin rotates programmatic API Key
    rotate_res = client.post(
        "/api/auth/universities/rotate-key",
        headers=admin_headers
    )
    assert rotate_res.status_code == 200
    new_key = rotate_res.json()["api_key"]
    assert new_key != yale["api_key"]
    
    # 7. Authenticate via programmatic API key and list users
    api_headers = {"Authorization": f"ApiKey {new_key}"}
    api_users_res = client.get("/api/auth/users", headers=api_headers)
    assert api_users_res.status_code == 200
    users_list = api_users_res.json()
    assert len(users_list) == 2  # Admin and Reviewer
    assert any(u["email"] == "admin@yaleuniversity.edu" for u in users_list)
    assert any(u["email"] == "reviewer@yale.edu" for u in users_list)


def test_tenancy_isolation_for_rubrics(intake_client) -> None:
    client, session_factory, _ = intake_client
    super_headers = auth_headers(client)
    
    # Create MIT and Stanford
    mit_res = client.post(
        "/api/auth/universities",
        headers=super_headers,
        json={"name": "MIT"}
    )
    stan_res = client.post(
        "/api/auth/universities",
        headers=super_headers,
        json={"name": "Stanford University"}
    )
    mit_uni = mit_res.json()
    stan_uni = stan_res.json()
    
    # Login MIT Admin
    mit_login = client.post("/api/auth/login", json={"email": "admin@mit.edu", "password": "EverydayAI"})
    mit_headers = {"Authorization": f"Bearer {mit_login.json()['token']}"}
    
    # Login Stanford Admin
    stan_login = client.post("/api/auth/login", json={"email": "admin@stanforduniversity.edu", "password": "EverydayAI"})
    stan_headers = {"Authorization": f"Bearer {stan_login.json()['token']}"}
    
    # 1. MIT Admin creates a rubric
    rubric_input = {
        "name": "MIT CS Rubric",
        "description": "Specific CS rubric",
        "total_points": 50,
        "sections": [
            {
                "section_id": "gpa",
                "name": "Academic Record",
                "description": "Review of academic GPA.",
                "max_points": 50,
                "components": []
            }
        ]
    }
    create_rubric_res = client.post("/api/rubrics", headers=mit_headers, json=rubric_input)
    assert create_rubric_res.status_code == 201
    mit_rubric = create_rubric_res.json()
    
    # 2. MIT Admin lists rubrics (should see CS Rubric)
    mit_list = client.get("/api/rubrics", headers=mit_headers)
    assert mit_list.status_code == 200
    assert any(r["name"] == "MIT CS Rubric" for r in mit_list.json())
    
    # 3. Stanford Admin lists rubrics (should NOT see MIT CS Rubric)
    stan_list = client.get("/api/rubrics", headers=stan_headers)
    assert stan_list.status_code == 200
    assert not any(r["name"] == "MIT CS Rubric" for r in stan_list.json())
    
    # 4. Stanford Admin tries to fetch MIT rubric directly (should be 404/isolated)
    direct_res = client.get(f"/api/rubrics/{mit_rubric['rubric_id']}", headers=stan_headers)
    assert direct_res.status_code == 404


def test_superadmin_can_delete_university(intake_client) -> None:
    client, session_factory, _ = intake_client
    super_headers = auth_headers(client)
    
    # 1. Create Columbia University
    create_res = client.post(
        "/api/auth/universities",
        headers=super_headers,
        json={"name": "Columbia University"}
    )
    assert create_res.status_code == 201
    columbia = create_res.json()
    columbia_id = columbia["university_id"]
    
    # Verify Columbia Admin exists
    with session_factory() as session:
        columbia_admin = session.query(LocalUser).filter_by(email="admin@columbiauniversity.edu").first()
        assert columbia_admin is not None
        
    # 2. Try deleting Default University (should be 400 Bad Request)
    unis_res = client.get("/api/auth/universities", headers=super_headers)
    default_uni = next(u for u in unis_res.json() if u["name"] == "Default University")
    bad_del = client.delete(f"/api/auth/universities/{default_uni['university_id']}", headers=super_headers)
    assert bad_del.status_code == 400
    
    # 3. Delete Columbia University (should be 204 No Content)
    del_res = client.delete(f"/api/auth/universities/{columbia_id}", headers=super_headers)
    assert del_res.status_code == 204
    
    # Verify university and admin are cascade deleted
    with session_factory() as session:
        uni = session.query(University).get(columbia_id)
        assert uni is None
        admin = session.query(LocalUser).filter_by(email="admin@columbiauniversity.edu").first()
        assert admin is None

