"""Shared authentication and current database permissions."""

from dataclasses import dataclass
import logging

from fastapi import Depends, Header, HTTPException
from httpx import HTTPError
from postgrest.exceptions import APIError

from core.config import get_settings
from core.database import get_supabase_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Access:
    user_id: str
    roles: list[str]
    permissions: list[str]


def extract_user_id(authorization: str) -> str:
    settings = get_settings()
    if settings.local_auth:
        return settings.local_user_id
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return get_supabase_client().auth.get_user(token).user.id
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")


def get_current_user_id(authorization: str = Header(...)) -> str:
    return extract_user_id(authorization)


def get_access(user_id: str) -> Access:
    try:
        data = get_supabase_client().rpc(
            "get_user_access", {"target_user_id": user_id}
        ).execute().data
    except (APIError, HTTPError) as exc:
        logger.exception("Could not load permissions for %s", user_id)
        raise HTTPException(status_code=503, detail="Permissions could not be loaded.") from exc
    return Access(user_id, data["roles"], data["permissions"])


def require_permission(permission: str):
    def check(user_id: str = Depends(get_current_user_id)) -> Access:
        return check_permission(user_id, permission)

    return check


def check_permission(user_id: str, permission: str) -> Access:
    access = get_access(user_id)
    if permission not in access.permissions:
        raise HTTPException(status_code=403, detail="Permission denied.")
    return access
