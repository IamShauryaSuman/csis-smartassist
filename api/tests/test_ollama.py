import json
from unittest.mock import patch

import httpx
import pytest

from services.llm.ollama import OllamaClient


@pytest.mark.asyncio
async def test_embed_query_uses_nomic_query_prefix():
    request_payload = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3, 0.4]]})

    client = OllamaClient.__new__(OllamaClient)
    client._base_url = "http://ollama"
    client._embedding_model = "nomic-embed-text"
    client._embedding_dim = 3
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))

    with patch.object(client, "_create_client", return_value=async_client):
        embedding = await client.embed_query("Which room is this?")

    assert request_payload["input"] == "search_query: Which room is this?"
    assert embedding == [0.1, 0.2, 0.3]
