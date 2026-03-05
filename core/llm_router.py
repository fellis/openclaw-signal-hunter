"""
LLM Router.
Reads llm_routing from config, selects the correct provider per operation,
and creates the appropriate client (Anthropic or OpenAI-compatible).

Local LLM credentials come from environment variables, not from config:
  LOCAL_LLM_BASE_URL, LOCAL_LLM_API_KEY, LOCAL_LLM_MODEL

Tokenizer for token-aware batching is auto-selected from LOCAL_LLM_MODEL name.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Maps substrings in LOCAL_LLM_MODEL to tokenizer factory functions.
# Add new families here when switching local models.
_MISTRAL_FAMILIES = {"mistral", "devstral", "mixtral", "codestral", "llm"}

_ANTHROPIC_COST_PER_M = {
    "input": 3.0,
    "output": 15.0,
}


@dataclass
class LLMCall:
    """Describes a single LLM call for routing + logging."""

    operation: str
    messages: list[dict[str, str]]
    max_tokens: int = 4096
    temperature: float = 0.0
    json_mode: bool = False


class LLMRouter:
    """
    Routes LLM calls to the correct provider based on config.llm_routing.
    Lazy-initializes clients on first use.
    Logs token usage to Postgres via the usage_logger callback.
    """

    def __init__(self, config: dict[str, Any], usage_logger=None) -> None:
        """
        Args:
            config: full skill config dict (llm_providers + llm_routing sections).
            usage_logger: optional callable(provider, operation, model, in_tok, out_tok, cost).
        """
        self._config = config
        self._routing: dict[str, str] = config.get("llm_routing", {})
        self._providers_cfg: dict[str, Any] = config.get("llm_providers", {})
        self._usage_logger = usage_logger

        self._anthropic_client = None
        self._openai_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(self, call: LLMCall) -> str:
        """
        Route call to provider, execute, log usage, return response text.
        Raises RuntimeError on provider misconfiguration.
        """
        provider = self._routing.get(call.operation, "local")

        if provider == "claude":
            return self._call_anthropic(call)
        elif provider == "local":
            return self._call_local(call)
        else:
            raise RuntimeError(
                f"Unknown provider '{provider}' for operation '{call.operation}'. "
                f"Check llm_routing in config.json."
            )

    def get_tokenizer(self):
        """
        Return a token-counting callable: text -> int.
        Auto-detected from LOCAL_LLM_MODEL env var.
        Falls back to character-based estimate with a warning.
        """
        model_name = os.environ.get("LOCAL_LLM_MODEL", "").lower()
        family = next((f for f in _MISTRAL_FAMILIES if f in model_name), None)

        if family:
            return self._mistral_tokenizer()

        log.warning(
            "LOCAL_LLM_MODEL='%s' not in known tokenizer families %s. "
            "Falling back to char-based estimate (len/4). "
            "Batches may be inaccurate.",
            model_name,
            _MISTRAL_FAMILIES,
        )
        return lambda text: len(text) // 4

    # ------------------------------------------------------------------
    # Private: Anthropic
    # ------------------------------------------------------------------

    def _call_anthropic(self, call: LLMCall) -> str:
        import anthropic  # noqa: PLC0415

        if self._anthropic_client is None:
            cfg = self._providers_cfg.get("claude", {})
            api_key = self._resolve_env(cfg.get("api_key", "${ANTHROPIC_API_KEY}"))
            self._anthropic_client = anthropic.Anthropic(api_key=api_key)
            self._claude_model = cfg.get("model", "claude-haiku-4-5-20251001")

        system_msg = next(
            (m["content"] for m in call.messages if m["role"] == "system"), None
        )
        user_messages = [m for m in call.messages if m["role"] != "system"]

        kwargs: dict[str, Any] = {
            "model": self._claude_model,
            "max_tokens": call.max_tokens,
            "messages": user_messages,
        }
        if system_msg:
            kwargs["system"] = system_msg

        response = self._anthropic_client.messages.create(**kwargs)
        text = response.content[0].text

        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        cost = (in_tok * _ANTHROPIC_COST_PER_M["input"] + out_tok * _ANTHROPIC_COST_PER_M["output"]) / 1_000_000

        self._log_usage("claude", call.operation, self._claude_model, in_tok, out_tok, cost)
        return text

    # ------------------------------------------------------------------
    # Private: Local OpenAI-compatible
    # ------------------------------------------------------------------

    def _call_local(self, call: LLMCall) -> str:
        import httpx  # noqa: PLC0415
        from openai import OpenAI  # noqa: PLC0415

        if self._openai_client is None:
            base_url = os.environ.get("LOCAL_LLM_BASE_URL")
            api_key = os.environ.get("LOCAL_LLM_API_KEY", "local")
            if not base_url:
                raise RuntimeError(
                    "LOCAL_LLM_BASE_URL env var is not set. "
                    "Local LLM config must be in .env, not in config.json."
                )
            ssl_verify = os.environ.get("LOCAL_LLM_SSL_VERIFY", "true").lower() != "false"
            self._openai_client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=500.0,
                http_client=httpx.Client(verify=ssl_verify),
            )
            self._local_model = os.environ.get("LOCAL_LLM_MODEL", "llm")

        response = self._openai_client.chat.completions.create(
            model=self._local_model,
            messages=call.messages,
            temperature=call.temperature,
            max_tokens=call.max_tokens,
        )

        text = response.choices[0].message.content.strip()

        usage = response.usage
        in_tok = usage.prompt_tokens if usage else 0
        out_tok = usage.completion_tokens if usage else 0
        self._log_usage("local", call.operation, self._local_model, in_tok, out_tok, 0.0)
        return text

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    def _resolve_env(self, value: str) -> str:
        """Replace ${VAR} references with environment variable values."""
        if value.startswith("${") and value.endswith("}"):
            var = value[2:-1]
            resolved = os.environ.get(var)
            if not resolved:
                raise RuntimeError(f"Environment variable '{var}' is not set.")
            return resolved
        return value

    def _log_usage(
        self,
        provider: str,
        operation: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        if self._usage_logger:
            try:
                self._usage_logger(provider, operation, model, input_tokens, output_tokens, cost_usd)
            except Exception as e:
                log.warning("Failed to log LLM usage: %s", e)

    @staticmethod
    def _mistral_tokenizer():
        """Return a token-counting function using mistral-common."""
        from mistral_common.protocol.instruct.messages import UserMessage  # noqa: PLC0415
        from mistral_common.protocol.instruct.request import ChatCompletionRequest  # noqa: PLC0415
        from mistral_common.tokens.tokenizers.mistral import MistralTokenizer  # noqa: PLC0415

        tokenizer = MistralTokenizer.v3()

        def count(text: str) -> int:
            req = ChatCompletionRequest(messages=[UserMessage(content=text)])
            return len(tokenizer.encode_chat_completion(req).tokens)

        return count
