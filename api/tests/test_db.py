from types import SimpleNamespace
from unittest.mock import MagicMock

from core.database import get_supabase_client


def test_supabase_client_is_cached(monkeypatch):
    create = MagicMock()
    monkeypatch.setattr("core.database.create_client", create)
    monkeypatch.setattr("core.database.get_settings", lambda: SimpleNamespace(
        next_public_supabase_url="https://example.supabase.co", supabase_service_role_key="test-key",
    ))
    get_supabase_client.cache_clear()
    try:
        assert get_supabase_client() is get_supabase_client()
        create.assert_called_once_with("https://example.supabase.co", "test-key")
    finally:
        get_supabase_client.cache_clear()
