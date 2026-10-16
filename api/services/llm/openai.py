"""
Generic OpenAI-compatible LLM integration.

Can be used for OpenRouter, Groq, or any other provider that matches the OpenAI API spec.
"""

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI
from services.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(BaseLLMClient):
    """Implementation of the LLM interface with dynamic model discovery and self-healing recovery."""

    def __init__(self, api_key: str, base_url: str, default_model: str = ""):
        self._configured_model = (default_model or "").strip()
        self._model = self._configured_model
        if not api_key:
            logger.warning(f"No API key provided for OpenAI-compatible endpoint at {base_url}. Calls will likely fail.")
        
        self._client = AsyncOpenAI(api_key=api_key or "dummy", base_url=base_url)

    async def _resolve_model(self, force_refresh: bool = False) -> str:
        """Resolve an active model name, dynamically discovering one from the provider if needed."""
        if self._model and not force_refresh:
            return self._model

        try:
            model_list = await self._client.models.list()
            available = [m.id for m in model_list.data if getattr(m, "id", None)]
            is_groq = "groq" in str(self._client.base_url).lower()
            is_openrouter = "openrouter" in str(self._client.base_url).lower()

            if is_groq:
                preferences = [
                    "openai/gpt-oss-20b",
                    "qwen/qwen3.8-27b",
                    "openai/gpt-oss-120b",
                    "llama-3.3-70b-versatile",
                    "llama-3.1-70b-versatile",
                    "llama-3.1-8b-instant",
                ]
                for pref in preferences:
                    if pref in available:
                        self._model = pref
                        logger.info("Auto-selected Groq model: %s", self._model)
                        return self._model

                candidates = [
                    m for m in available
                    if not any(x in m.lower() for x in ["whisper", "prompt-guard", "safeguard", "orpheus", "embed"])
                ]
                if candidates:
                    self._model = candidates[0]
                    logger.info("Auto-selected Groq candidate model: %s", self._model)
                    return self._model

            elif is_openrouter:
                free_models = [m for m in available if ":free" in m]
                preferences = [
                    "nvidia/nemotron-3.5-lightning:free",
                    "meta-llama/llama-3.3-70b-instruct:free",
                    "meta-llama/llama-3.1-8b-instruct:free",
                    "mistralai/mistral-7b-instruct:free",
                    "google/gemini-2.0-flash-exp:free",
                ]
                for pref in preferences:
                    if pref in available:
                        self._model = pref
                        logger.info("Auto-selected OpenRouter model: %s", self._model)
                        return self._model

                if free_models:
                    candidates = [m for m in free_models if not any(x in m.lower() for x in ["safety", "guard", "preview"])]
                    self._model = candidates[0] if candidates else free_models[0]
                    logger.info("Auto-selected OpenRouter free model: %s", self._model)
                    return self._model

            if available:
                self._model = available[0]
                return self._model
        except Exception as e:
            logger.warning("Failed to dynamically discover models from %s: %s", self._client.base_url, e)

        # Fallback if discovery failed
        default_fallback = "openai/gpt-oss-20b" if "groq" in str(self._client.base_url).lower() else "nvidia/nemotron-3.5-lightning:free"
        self._model = self._configured_model or default_fallback
        return self._model

    def _is_model_error(self, err_msg: str) -> bool:
        """Check if an error string indicates an invalid, missing, or deprecated model."""
        err_lower = err_msg.lower()
        return (
            any(x in err_lower for x in ["model_not_found", "does not exist", "unavailable for free", "no access to it", "decommissioned"])
            or ("404" in err_lower and "model" in err_lower)
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        fast_model: bool = False,
    ) -> str:
        """Generate a text completion with dynamic model discovery and auto-recovery."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = await self._resolve_model()
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            if self._is_model_error(str(e)):
                logger.warning("Model '%s' failed with model error (%s). Re-discovering active model...", model, e)
                new_model = await self._resolve_model(force_refresh=True)
                if new_model and new_model != model:
                    logger.info("Retrying generate with auto-discovered model: %s", new_model)
                    response = await self._client.chat.completions.create(
                        model=new_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    return response.choices[0].message.content or ""
            raise

    async def generate_stream(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        fast_model: bool = False,
    ):
        """Generate a text completion as an async stream with dynamic model discovery."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = await self._resolve_model()
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
        except Exception as e:
            if self._is_model_error(str(e)):
                logger.warning("Stream with model '%s' failed (%s). Re-discovering active model...", model, e)
                new_model = await self._resolve_model(force_refresh=True)
                if new_model and new_model != model:
                    logger.info("Retrying stream with auto-discovered model: %s", new_model)
                    stream = await self._client.chat.completions.create(
                        model=new_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=True,
                    )
                    async for chunk in stream:
                        if chunk.choices and len(chunk.choices) > 0:
                            content = chunk.choices[0].delta.content
                            if content:
                                yield content
                    return
            raise

    async def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float = 0.1,
        fast_model: bool = False,
    ) -> dict[str, Any]:
        """Generate structured JSON with dynamic model discovery and auto-recovery."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = await self._resolve_model()
        is_groq = "groq" in str(self._client.base_url).lower()

        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=2048,
                response_format={"type": "json_object"} if is_groq else None,
            )
        except Exception as e:
            if self._is_model_error(str(e)):
                logger.warning("JSON generation with model '%s' failed (%s). Re-discovering active model...", model, e)
                new_model = await self._resolve_model(force_refresh=True)
                if new_model and new_model != model:
                    logger.info("Retrying JSON generation with auto-discovered model: %s", new_model)
                    response = await self._client.chat.completions.create(
                        model=new_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=2048,
                        response_format={"type": "json_object"} if is_groq else None,
                    )
                else:
                    raise
            else:
                raise
        
        raw_text = response.choices[0].message.content or ""
        raw_text = raw_text.strip()

        # Handle markdown fences if the model still outputs them
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text)
        if json_match:
            raw_text = json_match.group(1).strip()

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON response from OpenAI-compatible provider: %s", raw_text[:200])
            return {"error": "Failed to parse response", "raw": raw_text}

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text."""
        model = await self._resolve_model()
        response = await self._client.embeddings.create(
            input=text,
            model=model,
        )
        return response.data[0].embedding

    async def embed_query(self, text: str) -> list[float]:
        """Generate an embedding vector for a query."""
        return await self.embed(text)
