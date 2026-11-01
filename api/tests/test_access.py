from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from postgrest.exceptions import APIError

from core.auth import extract_user_id

USER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"
ROLE_PERMISSIONS = {
    "booking_admin": ["bookings.manage"],
    "knowledge_admin": ["knowledge.manage"],
    "super_admin": ["bookings.manage", "knowledge.manage", "roles.manage"],
}


@pytest.fixture
def access_db(monkeypatch):
    db = MagicMock()
    assignments = {USER_ID: []}

    def get_user(token):
        if token != "valid":
            raise ValueError("Invalid token")
        return SimpleNamespace(user=SimpleNamespace(id=USER_ID))

    def rpc(name, params):
        if name == "get_user_access":
            roles = assignments.get(params["target_user_id"], [])
            permissions = sorted({p for role in roles for p in ROLE_PERMISSIONS[role]})
            return MagicMock(execute=lambda: SimpleNamespace(data={"roles": roles, "permissions": permissions}))
        return MagicMock()

    db.auth.get_user.side_effect = get_user
    db.auth.admin.get_user_by_id.return_value = SimpleNamespace(user=SimpleNamespace(email="other@example.com"))
    db.rpc.side_effect = rpc
    table = db.table.return_value
    for method in ["select", "eq", "order", "limit"]:
        getattr(table, method).return_value = table
    table.execute.return_value = SimpleNamespace(data=[])
    for module in ["core.auth", "routes.booking", "routes.rag", "routes.admin", "routes.chat"]:
        monkeypatch.setattr(f"{module}.get_supabase_client", lambda: db)
    monkeypatch.setattr("core.auth.get_settings", lambda: SimpleNamespace(local_auth=False))
    return db, assignments


@pytest.mark.parametrize("roles", [
    [], ["booking_admin"], ["knowledge_admin"],
    ["booking_admin", "knowledge_admin"], ["super_admin"],
])
@pytest.mark.parametrize("url,permission,allowed_status", [
    ("/api/bookings?all_users=true", "bookings.manage", 200),
    ("/api/rag/delta", "knowledge.manage", 200),
    ("/api/admin/users?email=unknown@example.com", "roles.manage", 404),
    ("/api/admin/roles", "roles.manage", 200),
])
def test_role_access_matrix(client, access_db, roles, url, permission, allowed_status):
    _, assignments = access_db
    assignments[USER_ID] = roles
    with patch("routes.rag._list_drive_files", return_value=[]):
        response = client.get(url, headers={"Authorization": "Bearer valid"})
    allowed = any(permission in ROLE_PERMISSIONS[role] for role in roles)
    assert response.status_code == (allowed_status if allowed else 403)


@pytest.mark.parametrize("method,url", [
    ("get", "/api/me/permissions"),
    ("get", "/api/admin/users"),
    ("get", "/api/rag/stats"),
    ("post", "/api/bookings/test-email?target_email=someone@example.com"),
    ("post", "/api/bookings/test-calendar"),
    ("post", f"/api/chat/sessions/{OTHER_ID}/synthesize"),
])
def test_protected_endpoints_require_authentication(client, access_db, method, url):
    assert getattr(client, method)(url).status_code == 422
    response = getattr(client, method)(url, headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


def test_permissions_are_combined_and_revocation_is_immediate(client, access_db):
    db, assignments = access_db
    assignments[USER_ID] = ["booking_admin", "knowledge_admin"]
    response = client.get("/api/me/permissions", headers={"Authorization": "Bearer valid"})
    assert response.json() == {
        "user_id": USER_ID, "roles": ["booking_admin", "knowledge_admin"],
        "permissions": ["bookings.manage", "knowledge.manage"],
    }
    assignments[USER_ID] = []
    response = client.get("/api/bookings?all_users=true", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 403
    assert db.rpc.call_count == 2


def test_permission_lookup_failure_does_not_grant_access(client, access_db):
    db, _ = access_db
    db.rpc.side_effect = APIError({"code": "PGRST202", "message": "Missing migration", "details": None, "hint": None})
    response = client.get("/api/bookings?all_users=true", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Permissions could not be loaded."
    db.table.return_value.execute.assert_not_called()


ADMIN_ACTIONS = [
    ("get", "/api/rag/stats", None, "knowledge.manage"),
    ("post", "/api/rag/sync-file?gdrive_id=file", None, "knowledge.manage"),
    ("delete", "/api/rag/file?gdrive_id=file", None, "knowledge.manage"),
    ("patch", f"/api/bookings/{OTHER_ID}/status", {"status": "approved"}, "bookings.manage"),
    ("post", "/api/bookings/test-email?target_email=someone@example.com", None, "bookings.manage"),
    ("post", "/api/bookings/test-calendar", None, "bookings.manage"),
    ("put", f"/api/admin/users/{OTHER_ID}/roles", {"roles": ["super_admin"]}, "roles.manage"),
]


@pytest.mark.parametrize("role,method,url,body", [
    (role, method, url, body)
    for role in [None, "booking_admin", "knowledge_admin"]
    for method, url, body, permission in ADMIN_ACTIONS
    if permission not in ROLE_PERMISSIONS.get(role, [])
])
def test_admin_actions_require_their_permission(client, access_db, role, method, url, body):
    access_db[1][USER_ID] = [role] if role else []
    response = client.request(method, url, headers={"Authorization": "Bearer valid"}, json=body)
    assert response.status_code == 403
    access_db[0].table.assert_not_called()


def test_role_assignment_passes_verified_actor_and_deduplicates_roles(client, access_db):
    db, assignments = access_db
    assignments[USER_ID] = ["super_admin"]
    response = client.put(f"/api/admin/users/{OTHER_ID}/roles",
                          headers={"Authorization": "Bearer valid"},
                          json={"roles": ["booking_admin", "booking_admin"]})
    assert response.status_code == 200
    db.rpc.assert_any_call("set_user_roles", {
        "target_user_id": OTHER_ID, "requested_roles": ["booking_admin"], "actor_id": USER_ID,
    })


def test_user_lookup_returns_roles_for_the_exact_email(client, access_db):
    db, assignments = access_db
    assignments[USER_ID] = ["super_admin"]
    db.table.return_value.execute.side_effect = [
        SimpleNamespace(data=[{"id": OTHER_ID, "email": "other@example.com", "full_name": "Other"}]),
        SimpleNamespace(data=[
            {"user_id": OTHER_ID, "role_id": "knowledge_admin"},
            {"user_id": OTHER_ID, "role_id": "booking_admin"},
        ]),
    ]
    response = client.get("/api/admin/users?email=Other@Example.com",
                          headers={"Authorization": "Bearer valid"})
    assert response.json() == {
        "id": OTHER_ID, "email": "other@example.com", "full_name": "Other",
        "roles": ["booking_admin", "knowledge_admin"],
    }
    db.table.return_value.eq.assert_any_call("email", "other@example.com")


@pytest.mark.parametrize("duplicate,auth_email,status", [
    (True, "other@example.com", 409), (False, "someone-else@example.com", 404),
])
def test_lookup_rejects_ambiguous_or_forged_profile_emails(client, access_db, duplicate, auth_email, status):
    db, assignments = access_db
    assignments[USER_ID] = ["super_admin"]
    profile = {"id": OTHER_ID, "email": "other@example.com", "full_name": "Other"}
    db.table.return_value.execute.return_value = SimpleNamespace(data=[profile] * (2 if duplicate else 1))
    db.auth.admin.get_user_by_id.return_value = SimpleNamespace(user=SimpleNamespace(email=auth_email))
    response = client.get("/api/admin/users?email=other@example.com", headers={"Authorization": "Bearer valid"})
    assert response.status_code == status
    db.table.return_value.execute.assert_called_once()


@pytest.mark.parametrize("code,status", [("42501", 403), ("P0002", 404), ("23514", 409), ("22023", 400)])
def test_assignment_database_errors_are_reported(client, access_db, code, status):
    db, assignments = access_db
    assignments[USER_ID] = ["super_admin"]
    original_rpc = db.rpc.side_effect

    def rpc(name, params):
        if name == "set_user_roles":
            raise APIError({"code": code, "message": "Assignment rejected", "details": None, "hint": None})
        return original_rpc(name, params)

    db.rpc.side_effect = rpc
    response = client.put(f"/api/admin/users/{OTHER_ID}/roles",
                          headers={"Authorization": "Bearer valid"}, json={"roles": []})
    assert response.status_code == status
    assert response.json()["detail"] == "Assignment rejected"


@pytest.mark.parametrize("roles", [["made_up_role"], ["faculty"]])
def test_unknown_and_academic_roles_cannot_be_assigned(client, access_db, roles):
    access_db[1][USER_ID] = ["super_admin"]
    response = client.put(f"/api/admin/users/{OTHER_ID}/roles",
                          headers={"Authorization": "Bearer valid"}, json={"roles": roles})
    assert response.status_code == 422


@pytest.mark.parametrize("suffix", ["synthesize", "synthesize_memory"])
def test_memory_synthesis_requires_session_ownership(client, access_db, suffix):
    with patch("routes.chat._synthesize_memory_task", new_callable=AsyncMock) as synthesize:
        response = client.post(f"/api/chat/sessions/{OTHER_ID}/{suffix}",
                               headers={"Authorization": "Bearer valid"})
    assert response.status_code == 404
    synthesize.assert_not_awaited()


def test_memory_synthesis_uses_authenticated_identity_and_ignores_supplied_user_id(client, access_db):
    access_db[0].table.return_value.execute.return_value = SimpleNamespace(data=[{"id": OTHER_ID}])
    with patch("routes.chat._synthesize_memory_task", new_callable=AsyncMock, return_value={"status": "synthesized"}) as synthesize:
        response = client.post(f"/api/chat/sessions/{OTHER_ID}/synthesize?user_id={OTHER_ID}",
                               headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    synthesize.assert_awaited_once_with(OTHER_ID, USER_ID)


def test_local_auth_only_changes_identity(monkeypatch, access_db, client):
    monkeypatch.setattr("core.auth.get_settings", lambda: SimpleNamespace(local_auth=True, local_user_id=USER_ID))
    assert extract_user_id("local") == USER_ID
    response = client.get("/api/bookings?all_users=true", headers={"Authorization": "local"})
    assert response.status_code == 403
