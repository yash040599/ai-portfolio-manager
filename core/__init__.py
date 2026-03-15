# core/__init__.py
# Makes core a package and exposes all three classes at package level.
# Other files can then do: from core import Logger, ZerodhaClient, ClaudeClient

from core.logger         import Logger
from core.zerodha_client import ZerodhaClient
from core.claude_client  import ClaudeClient

__all__ = ["Logger", "ZerodhaClient", "ClaudeClient"]
