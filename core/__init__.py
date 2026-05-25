# core/__init__.py
# Makes core a package and exposes all classes at package level.
# Other files can then do: from core import Logger, ZerodhaClient, LLMClient

from core.logger         import Logger
from core.zerodha_client import ZerodhaClient
from core.llm_client     import LLMClient
from core.llm_client     import LLMClient as ClaudeClient  # backward compat

__all__ = ["Logger", "ZerodhaClient", "LLMClient", "ClaudeClient"]
