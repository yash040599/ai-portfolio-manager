# ================================================================
# core/claude_client.py  — BACKWARD-COMPAT SHIM
# ================================================================
# The real implementation now lives in core/llm_client.py (LLMClient)
# which supports Gemini, GPT, and Claude via Config.AI_PROVIDER.
#
# This file re-exports LLMClient as ClaudeClient so every existing
# import (`from core.claude_client import ClaudeClient`) keeps working
# without touching dozens of files.
# ================================================================

from core.llm_client import LLMClient as ClaudeClient  # noqa: F401

__all__ = ["ClaudeClient"]
