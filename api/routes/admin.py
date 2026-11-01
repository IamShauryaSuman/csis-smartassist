"""Permission discovery and role assignments."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError
from pydantic import BaseModel

from core.auth import Access, get_access, get_current_user_id, require_permission
from core.database import get_supabase_client

router = APIRouter(tags=["access"])
RoleName = Literal["booking_admin", "knowledge_admin", "super_admin"]


class UserRolesUpdate(BaseModel):
    roles: list[RoleName]


@router.get("/me/permissions")
def my_permissions(user_id: str = Depends(get_current_user_id)) -> Access:
    return get_access(user_id)


@router.get("/admin/roles")
def list_roles(access: Access = Depends(require_permission("roles.manage"))):
    return get_supabase_client().table("roles").select("id, name").order("id").execute().data


@router.get("/admin/users")
def find_user(
    email: str = Query(..., min_length=3, max_length=254),
    access: Access = Depends(require_permission("roles.manage")),
):
    db = get_supabase_client()
    users = db.table("profiles").select("id, email, full_name").eq(
        "email", email.strip().lower()
    ).limit(2).execute().data
    if not users:
        raise HTTPException(status_code=404, detail="User not found.")
    if len(users) > 1:
        raise HTTPException(status_code=409, detail="Multiple profiles use this email. Resolve duplicates first.")
    user = users[0]
    account = db.auth.admin.get_user_by_id(user["id"]).user
    if not account or not account.email or account.email.lower() != email.strip().lower():
        raise HTTPException(status_code=404, detail="User not found.")
    assignments = db.table("user_roles").select("role_id").eq(
        "user_id", user["id"]
    ).execute().data
    return {**user, "roles": sorted(row["role_id"] for row in assignments)}


@router.put("/admin/users/{user_id}/roles")
def update_user_roles(
    user_id: UUID,
    body: UserRolesUpdate,
    access: Access = Depends(require_permission("roles.manage")),
):
    try:
        get_supabase_client().rpc("set_user_roles", {
            "target_user_id": str(user_id),
            "requested_roles": sorted(set(body.roles)),
            "actor_id": access.user_id,
        }).execute()
    except APIError as exc:
        statuses = {"42501": 403, "P0002": 404, "23514": 409, "22023": 400}
        if exc.code in statuses:
            raise HTTPException(status_code=statuses[exc.code], detail=exc.message) from exc
        raise
    return {"id": str(user_id), "roles": sorted(set(body.roles))}
