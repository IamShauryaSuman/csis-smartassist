import pytest
from unittest.mock import patch, MagicMock, AsyncMock

USER_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"

@pytest.fixture
def chat_mock_supabase(mock_supabase):
    with patch("routes.chat.get_supabase_client", return_value=mock_supabase):
        yield mock_supabase

USER_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"

def test_chat_send_unauthenticated(client):
    """Test that the chat endpoint rejects unauthenticated requests."""
    response = client.post("/api/chat/send", json={
        "session_id": SESSION_ID,
        "message": "Hello"
    })
    assert response.status_code in (401, 403, 422)

def test_create_session(client, chat_mock_supabase):
    chat_mock_supabase.table().insert().execute.return_value = MagicMock(data=[{
        "id": SESSION_ID, "user_id": USER_ID, "title": "New Chat",
        "created_at": "2023-01-01T00:00:00Z", "updated_at": "2023-01-01T00:00:00Z"
    }])
    
    with patch("routes.chat._extract_user_id", return_value=USER_ID):
        response = client.post("/api/chat/sessions", json={"title": "Test Chat"}, headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    assert response.json()["id"] == SESSION_ID

def test_get_sessions(client, chat_mock_supabase):
    chat_mock_supabase.table().select().eq().order().execute.return_value = MagicMock(data=[{
        "id": SESSION_ID, "user_id": USER_ID, "title": "New Chat",
        "created_at": "2023-01-01T00:00:00Z", "updated_at": "2023-01-01T00:00:00Z"
    }])
    
    with patch("routes.chat._extract_user_id", return_value=USER_ID):
        response = client.get("/api/chat/sessions", headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    assert len(response.json()) == 1

def test_chat_send_general_query(client, chat_mock_supabase, mock_gemini):
    # Mock DB queries inside _fetch_db_data and subsequent calls
    s_mock = MagicMock(data={"id": SESSION_ID, "user_id": USER_ID, "title": "New Chat"})
    p_mock = MagicMock(data={"full_name": "Test User"})
    h_mock = MagicMock(data=[])

    chat_mock_supabase.table().select().eq().single().execute.side_effect = [
        s_mock, p_mock, MagicMock(data=None), MagicMock(data=None)
    ]
    chat_mock_supabase.table().select().eq().order().limit().execute.return_value = h_mock
    
    # Mock LLM intent
    mock_gemini.generate_json = AsyncMock(return_value={"intent": "general_query", "confidence": 0.95})
    
    # Mock LLM streaming
    async def mock_stream(*args, **kwargs):
        yield "Hello "
        yield "World"
        
    mock_gemini.generate_stream = mock_stream
    mock_gemini.generate_title = AsyncMock(return_value="Test Title")
    
    # Mock Hybrid client
    with patch("routes.chat.get_hybrid_client", return_value=mock_gemini), \
         patch("routes.chat._extract_user_id", return_value=USER_ID), \
         patch("routes.chat._finalize_chat_response", new_callable=AsyncMock):
        response = client.post("/api/chat/send", json={
            "session_id": SESSION_ID,
            "message": "Hi"
        }, headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 200
    text = response.text
    assert '"intent": "general_query"' in text
    assert '"text": "Hello "' in text
    assert '"text": "World"' in text
    assert '"type": "done"' in text

def test_chat_send_department_query_embedding_failure_fallback(client, chat_mock_supabase, mock_gemini):
    """Test that if embed_query fails (e.g. 403 Permission Denied), the stream still succeeds gracefully."""
    chat_mock_supabase.table().select().eq().single().execute.side_effect = [
        MagicMock(data={"id": SESSION_ID, "user_id": USER_ID}),
        MagicMock(data={"full_name": "Test User"}),
        MagicMock(data=[]),
    ]

    mock_gemini.generate_json = AsyncMock(return_value={"intent": "department_query", "confidence": 0.95})
    mock_gemini.embed_query = AsyncMock(side_effect=Exception("403 PermissionDenied: Your project has been denied access."))
    mock_gemini.generate = AsyncMock(return_value="department query")

    async def mock_stream(*args, **kwargs):
        yield "Fallback "
        yield "Answer"

    mock_gemini.generate_stream = mock_stream
    mock_gemini.generate_title = AsyncMock(return_value="Test Title")

    with patch("routes.chat.get_hybrid_client", return_value=mock_gemini), \
         patch("routes.chat._extract_user_id", return_value=USER_ID), \
         patch("routes.chat._finalize_chat_response", new_callable=AsyncMock):
        response = client.post("/api/chat/send", json={
            "session_id": SESSION_ID,
            "message": "Tell me about CS department"
        }, headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    text = response.text
    assert '"intent": "department_query"' in text
    assert '"text": "Fallback "' in text
    assert '"text": "Answer"' in text
    assert '"type": "done"' in text


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["who is neena maam", "who is neena"])
async def test_department_stream_finds_faculty_when_vector_search_is_empty(question):
    from routes.chat import _handle_department_query_stream

    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = []
    search = db.table.return_value.select.return_value.or_
    search.return_value.limit.return_value.execute.return_value.data = [{
        "id": "directory-chunk", "file_id": "directory", "content": "Neena Goveas Professor",
        "rag_files": {"name": "Faculty directory.pdf"},
    }]

    async def stream(prompt, *, system_prompt):
        assert question in prompt
        assert "Faculty directory.pdf" in system_prompt
        assert "Neena Goveas Professor" in system_prompt
        yield "Neena Goveas"

    llm = MagicMock(embed_query=AsyncMock(return_value=[0.1]), generate_stream=stream)
    with patch("routes.chat.get_supabase_client", return_value=db):
        answer = [chunk async for chunk in _handle_department_query_stream(llm, question, "", "")]
    assert answer == ["Neena Goveas"]
    search.assert_called_once_with("content.ilike.*neena*")


@pytest.mark.asyncio
async def test_faculty_keyword_evidence_prioritizes_exact_name_and_deduplicates():
    from routes.chat import _retrieve_rag_chunks

    directory = {"id": "directory", "file_id": "file1", "content": "Neena\nGoveas Professor"}
    unrelated = {"id": "other", "file_id": "file2", "content": "Other department context",
                 "file_name": "Context", "similarity": 0.7}
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [
        unrelated, {**directory, "file_name": "Faculty directory", "similarity": 0.4},
    ]
    search = db.table.return_value.select.return_value.or_
    search.return_value.limit.return_value.execute.return_value.data = [
        {"id": "substring", "file_id": "file3", "content": "Neenathan ",
         "rag_files": {"name": "Wrong match"}},
        {"id": "partial", "file_id": "file4", "content": "Neena Othername",
         "rag_files": {"name": "Other"}},
        {**directory, "rag_files": {"name": "Faculty directory"}},
    ]

    llm = MagicMock(embed_query=AsyncMock(return_value=[0.1]))
    chunks = await _retrieve_rag_chunks(
        llm, db, "Information about Neena Goveas from the CSIS department",
        "Who is Prof. Neena Goveas?",
    )

    assert [chunk["id"] for chunk in chunks] == ["directory", "partial", "other"]
    assert chunks[0]["similarity"] is None


@pytest.mark.asyncio
async def test_topic_search_preserves_vector_matches_without_extra_keyword_query():
    from routes.chat import _retrieve_rag_chunks

    chunk = {"id": "course", "content": "Machine learning prerequisites", "similarity": 0.7}
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [chunk]
    llm = MagicMock(embed_query=AsyncMock(return_value=[0.1]))
    question = "What are the prerequisites for machine learning?"

    assert await _retrieve_rag_chunks(llm, db, question, question) == [chunk]
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_keyword_search_uses_rewritten_subject_for_follow_up():
    from routes.chat import _retrieve_rag_chunks

    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = []
    search = db.table.return_value.select.return_value.or_
    search.return_value.limit.return_value.execute.return_value.data = [
        {"id": "general", "file_id": "general", "content": "Courses that instructors teach."},
        {"id": "specific", "file_id": "specific", "content": "Courses taught by Neena Goveas."},
    ]
    llm = MagicMock(embed_query=AsyncMock(return_value=[0.1]))
    chunks = await _retrieve_rag_chunks(
        llm, db, "What courses does Neena Goveas teach?", "What courses does she teach?",
    )
    assert [chunk["id"] for chunk in chunks] == ["specific", "general"]

def test_chat_send_calendar_query(client, chat_mock_supabase, mock_gemini):
    # Mock DB queries
    s_mock = MagicMock(data={"id": SESSION_ID, "user_id": USER_ID, "title": "New Chat"})
    p_mock = MagicMock(data={})
    h_mock = MagicMock(data=[])

    chat_mock_supabase.table().select().eq().single().execute.side_effect = [
        s_mock, p_mock, MagicMock(data=None), MagicMock(data=None)
    ]
    chat_mock_supabase.table().select().eq().order().limit().execute.return_value = h_mock
    
    mock_gemini.generate_json = AsyncMock(return_value={"intent": "calendar_query"})
    
    # Calendar doesn't stream, it uses generate
    mock_gemini.generate = AsyncMock(return_value="Sure, here is the proposal.\n```booking_proposal\n{\"room_id\": \"1\"}\n```")
    mock_gemini.generate_title = AsyncMock(return_value="Test Title")
    
    with patch("routes.chat.query_freebusy", new_callable=AsyncMock) as mock_fb, \
         patch("routes.chat.format_availability_for_llm", new_callable=AsyncMock) as mock_fmt, \
         patch("routes.chat.get_rooms_manifest_text", new_callable=AsyncMock) as mock_manifest, \
         patch("routes.chat.get_hybrid_client", return_value=mock_gemini), \
         patch("routes.chat._extract_user_id", return_value=USER_ID), \
         patch("routes.chat._finalize_chat_response", new_callable=AsyncMock):
        
        mock_fb.return_value = {}
        mock_fmt.return_value = ""
        mock_manifest.return_value = ""
        
        response = client.post("/api/chat/send", json={
            "session_id": SESSION_ID,
            "message": "Book a room"
        }, headers={"Authorization": "Bearer token"})
        
    assert response.status_code == 200
    text = response.text
    assert '"interactive_type": "booking_proposal"' in text
    assert '"interactive_payload": {"room_id": "1"}' in text
    assert '"text": "Sure, here is the proposal."' in text

def test_chat_send_rate_limit(client, chat_mock_supabase, mock_gemini):
    chat_mock_supabase.table().select().eq().single().execute.return_value = MagicMock(data={"id": SESSION_ID, "user_id": USER_ID})
    
    # Simulate Quota exceeded
    mock_gemini.generate_json.side_effect = Exception("429 ResourceExhausted")
    
    with patch("routes.chat.get_hybrid_client", return_value=mock_gemini), \
         patch("routes.chat._extract_user_id", return_value=USER_ID), \
         patch("routes.chat._finalize_chat_response", new_callable=AsyncMock):
        response = client.post("/api/chat/send", json={
            "session_id": SESSION_ID,
            "message": "Hi"
        }, headers={"Authorization": "Bearer token"})
    
    assert response.status_code == 429
    assert "LLM Rate limit exceeded" in response.json()["detail"]

def test_get_session_messages(client, chat_mock_supabase):
    chat_mock_supabase.table().select().eq().single().execute.side_effect = [
        MagicMock(data={"user_id": USER_ID}),
        MagicMock(data=[{"id": "msg-1", "session_id": SESSION_ID, "role": "user", "content": "Hello", "created_at": "2023-01-01T00:00:00Z"}])
    ]
    
    with patch("routes.chat._extract_user_id", return_value=USER_ID):
        response = client.get(f"/api/chat/sessions/{SESSION_ID}/messages", headers={"Authorization": "Bearer token"})
        
    assert response.status_code == 200
    assert len(response.json()) == 1

def test_delete_session(client, chat_mock_supabase):
    chat_mock_supabase.table().select().eq().single().execute.return_value = MagicMock(data={"user_id": USER_ID})
    
    with patch("routes.chat._extract_user_id", return_value=USER_ID):
        response = client.delete(f"/api/chat/sessions/{SESSION_ID}", headers={"Authorization": "Bearer token"})
        
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}

def test_update_session(client, chat_mock_supabase):
    chat_mock_supabase.table().select().eq().single().execute.side_effect = [
        MagicMock(data={"user_id": USER_ID}),
        MagicMock(data=[{"id": SESSION_ID, "user_id": USER_ID, "title": "Updated Title", "created_at": "2023-01-01T00:00:00Z", "updated_at": "2023-01-01T00:01:00Z"}])
    ]
    
    with patch("routes.chat._extract_user_id", return_value=USER_ID):
        response = client.patch(f"/api/chat/sessions/{SESSION_ID}", json={"title": "Updated Title"}, headers={"Authorization": "Bearer token"})
        
    assert response.status_code == 200
    assert response.json()["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_finalize_chat_response_json_title(chat_mock_supabase):
    from routes.chat import _finalize_chat_response
    mock_llm = MagicMock()
    mock_llm.generate_title = AsyncMock(return_value='{"title": "Machine Learning Prerequisites"}')

    with patch("routes.chat.get_hybrid_client", return_value=mock_llm), \
         patch("routes.chat._synthesize_memory_task", return_value=None):
        await _finalize_chat_response(
            user_id=USER_ID,
            session_id=SESSION_ID,
            session_title="New Chat",
            user_message="Tell me about CS F429",
            assistant_content="CS F429 is Machine Learning.",
            interactive_type=None,
            interactive_payload=None,
        )

    chat_mock_supabase.table().update.assert_any_call({"title": "Machine Learning Prerequisites"})


@pytest.mark.asyncio
async def test_finalize_chat_response_fallback_on_empty(chat_mock_supabase):
    from routes.chat import _finalize_chat_response
    mock_llm = MagicMock()
    mock_llm.generate_title = AsyncMock(return_value="")

    with patch("routes.chat.get_hybrid_client", return_value=mock_llm), \
         patch("routes.chat._synthesize_memory_task", return_value=None):
        await _finalize_chat_response(
            user_id=USER_ID,
            session_id=SESSION_ID,
            session_title="New Chat Session",
            user_message="What are the prerequisites for ML?",
            assistant_content="Probability and Linear Algebra.",
            interactive_type=None,
            interactive_payload=None,
        )

    chat_mock_supabase.table().update.assert_any_call({"title": "What are the prerequisites for ML?"})

