import pytest
from unittest.mock import patch, MagicMock, AsyncMock

MOCK_DRIVE_FILES = [
    {
        "gdrive_id": "drive-file-1",
        "name": "Syllabus.pdf",
        "mime_type": "application/pdf",
        "md5_checksum": "checksum1",
    },
    {
        "gdrive_id": "drive-file-2",
        "name": "Policies.docx",
        "mime_type": "application/vnd.google-apps.document",
        "md5_checksum": "checksum2",
    }
]

MOCK_DB_FILES = [
    {
        "id": "db-file-1",
        "gdrive_id": "drive-file-1",
        "name": "Syllabus.pdf",
        "mime_type": "application/pdf",
        "md5_checksum": "old-checksum", # Modified
        "index_complete": True,
        "updated_at": "2026-07-15T00:00:00Z"
    },
    {
        "id": "db-file-3",
        "gdrive_id": "drive-file-3",
        "name": "OldFile.txt",
        "mime_type": "text/plain",
        "md5_checksum": "checksum3", # Deleted from drive
        "index_complete": True,
        "updated_at": "2026-07-15T00:00:00Z"
    }
]

@pytest.fixture
def mock_admin_auth():
    from core.auth import Access
    with patch("core.auth.extract_user_id", return_value="admin-user"), \
         patch("core.auth.get_access", return_value=Access("admin-user", ["knowledge_admin"], ["knowledge.manage"])):
        yield

@pytest.fixture
def mock_rag_supabase():
    with patch("routes.rag.get_supabase_client") as mock:
        yield mock

def test_get_rag_delta(client, mock_admin_auth, mock_rag_supabase):
    class RagFilesMockTable:
        def select(self, *args, **kwargs):
            return self
        def execute(self):
            return MagicMock(data=MOCK_DB_FILES)
            
    mock_rag_supabase.return_value.table = lambda name: RagFilesMockTable()

    with patch("routes.rag._list_drive_files", return_value=MOCK_DRIVE_FILES):
        response = client.get("/api/rag/delta", headers={"Authorization": "Bearer token"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_changes"] == 3
        
        # New: drive-file-2
        assert len(data["new"]) == 1
        assert data["new"][0]["gdrive_id"] == "drive-file-2"
        
        # Modified: drive-file-1 (checksum mismatch)
        assert len(data["modified"]) == 1
        assert data["modified"][0]["gdrive_id"] == "drive-file-1"
        
        # Deleted: drive-file-3 (not in drive files)
        assert len(data["deleted"]) == 1
        assert data["deleted"][0]["gdrive_id"] == "drive-file-3"

def test_sync_single_file(client, mock_admin_auth, mock_rag_supabase):
    db = mock_rag_supabase.return_value
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    db.rpc.return_value.execute.return_value.data = "new-id"

    with patch("routes.rag._list_drive_files", return_value=MOCK_DRIVE_FILES), \
         patch("routes.rag._download_file_content", return_value="This is some content. " * 50), \
         patch("routes.rag.get_hybrid_client") as mock_get_client:
        
        class FakeLLM:
            async def embed(self, *args, **kwargs):
                return [0.1] * 768
                
        mock_get_client.return_value = FakeLLM()
        
        response = client.post("/api/rag/sync-file?gdrive_id=drive-file-1", headers={"Authorization": "Bearer token"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["gdrive_id"] == "drive-file-1"
        assert data["status"] == "synced"
        assert data["chunks_created"] > 0

def test_delete_rag_file(client, mock_admin_auth, mock_rag_supabase):
    class DeleteMockTable:
        def delete(self, *args, **kwargs): return self
        def eq(self, *args, **kwargs): return self
        def execute(self): return MagicMock(data=[{"id": "deleted"}])
        
    mock_rag_supabase.return_value.table = lambda name: DeleteMockTable()
    
    with patch("routes.rag._list_drive_files", return_value=[]):
        response = client.delete("/api/rag/file?gdrive_id=drive-file-1", headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

def test_get_rag_stats(client, mock_admin_auth, mock_rag_supabase):
    class StatsMockTable:
        def __init__(self, name):
            self.name = name
        def select(self, *args, **kwargs): return self
        def eq(self, *args, **kwargs): return self
        def execute(self):
            if self.name == "rag_files":
                return MagicMock(data=MOCK_DB_FILES)
            if self.name == "rag_chunks":
                mock = MagicMock(data=[])
                mock.count = 42
                return mock
            return MagicMock(data=[])
            
    mock_rag_supabase.return_value.table = lambda name: StatsMockTable(name)
    
    response = client.get("/api/rag/stats", headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 2
    assert data["total_chunks"] == 42
    assert len(data["files"]) == 2


def test_missing_folder_blocks_scan_and_stale_deletion(client, mock_admin_auth, mock_rag_supabase):
    from types import SimpleNamespace
    headers = {"Authorization": "Bearer token"}
    with patch("routes.rag.get_settings", return_value=SimpleNamespace(google_drive_folder_id="")):
        assert client.get("/api/rag/delta", headers=headers).status_code == 503
        assert client.delete("/api/rag/file?gdrive_id=old", headers=headers).status_code == 503
    mock_rag_supabase.return_value.table.return_value.delete.assert_not_called()


def test_native_document_changes_are_detected(client, mock_admin_auth, mock_rag_supabase):
    from types import SimpleNamespace
    db = mock_rag_supabase.return_value
    stored = {"index_complete": True, "gdrive_id": "doc", "name": "Syllabus", "md5_checksum": ""}
    db.table.return_value.select.return_value.execute.return_value.data = [stored]
    files = MagicMock()
    files.get.return_value.execute.return_value = {"mimeType": "application/vnd.google-apps.folder"}
    files.list.return_value.execute.return_value = {"files": [{
        "id": "doc", "name": "Syllabus", "mimeType": "application/vnd.google-apps.document", "version": "12",
    }]}
    with patch("routes.rag.get_settings", return_value=SimpleNamespace(google_drive_folder_id="folder")), \
         patch("routes.rag._get_drive_service") as service:
        service.return_value.files.return_value = files
        def scan():
            response = client.get("/api/rag/delta", headers={"Authorization": "Bearer token"})
            assert response.status_code == 200
            return response.json()["modified"]
        assert scan()[0]["md5_checksum"] == "version:12"
        stored["md5_checksum"] = "version:12"
        assert scan() == []
        files.list.return_value.execute.return_value["files"][0]["version"] = "13"
        assert scan()[0]["md5_checksum"] == "version:13"


def test_partial_embedding_failure_does_not_publish(client, mock_admin_auth, mock_rag_supabase):
    db = mock_rag_supabase.return_value
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"md5_checksum": "old"}]
    with patch("routes.rag._list_drive_files", return_value=MOCK_DRIVE_FILES), \
         patch("routes.rag._download_file_content", return_value="document"), \
         patch("routes.rag._chunk_text", return_value=["first", "second"]), \
         patch("routes.rag.get_hybrid_client") as llm:
        llm.return_value.embed = AsyncMock(side_effect=[[0.1] * 768, RuntimeError("offline")])
        res = client.post("/api/rag/sync-file?gdrive_id=drive-file-1", headers={"Authorization": "Bearer token"})
    assert res.status_code == 503
    db.rpc.assert_not_called()
    db.table.return_value.delete.assert_not_called()


def test_legacy_partial_index_is_rescanned_even_with_matching_checksum(client, mock_admin_auth, mock_rag_supabase):
    db = mock_rag_supabase.return_value
    db.table.return_value.select.return_value.execute.return_value.data = [{
        **MOCK_DRIVE_FILES[0], "index_complete": False,
    }]
    with patch("routes.rag._list_drive_files", return_value=[MOCK_DRIVE_FILES[0]]):
        result = client.get("/api/rag/delta", headers={"Authorization": "Bearer token"})
    assert result.status_code == 200
    assert len(result.json()["modified"]) == 1
