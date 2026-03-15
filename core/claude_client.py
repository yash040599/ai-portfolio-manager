# ================================================================
# core/claude_client.py
# ================================================================
# All Claude API interactions in one place.
#
# Responsibilities:
#   - Making API calls with the correct model + token settings
#   - Classifying errors into plain English messages
#   - Advising whether an error is worth retrying
#
# Retry logic lives in AnalysisQueue (services/analysis_queue.py),
# not here — this class just makes the call and surfaces the result.
# Phase 2 will call this for buy/sell decisions too.
# ================================================================

from config      import Config
from core.logger import Logger


class ClaudeClient:

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log

    # ================================================================
    # API CALL
    # ================================================================

    def call(self, prompt: str) -> str:
        """
        Sends a prompt to the Claude API and returns the response text.
        Raises an exception on failure — the caller handles retry logic.

        Model and max_tokens are read from Config.claude() so they
        automatically reflect whichever plan is set in config.py.
        """
        import anthropic

        plan    = self.cfg.claude()
        client  = anthropic.Anthropic(api_key=self.cfg.CLAUDE_API_KEY)

        message = client.messages.create(
            model      = plan["model"],
            max_tokens = plan["max_tokens"],
            messages   = [{"role": "user", "content": prompt}],
        )

        return message.content[0].text

    # ================================================================
    # ERROR CLASSIFICATION
    # ================================================================

    @staticmethod
    def classify_error(exception: Exception) -> str:
        """
        Converts a raw Python exception into a plain English message.
        Called by AnalysisQueue to show meaningful errors in the terminal
        and log file instead of raw stack traces.
        """
        err = str(exception).lower()

        if "rate_limit" in err or "429" in err:
            return "Rate limit hit — Claude API is busy, will retry"
        elif "timeout" in err or "timed out" in err:
            return "Request timed out — Claude took too long to respond"
        elif "overloaded" in err or "529" in err:
            return "Claude API overloaded — servers under heavy load, will retry"
        elif "invalid_api_key" in err or "401" in err:
            return "Invalid Claude API key — check CLAUDE_API_KEY in your .env file"
        elif "insufficient_quota" in err or "credit" in err:
            return "Claude API credit exhausted — top up at console.anthropic.com"
        elif "connection" in err or "network" in err:
            return "Network error — check your internet connection"
        elif "context_length" in err or "too long" in err:
            return "Prompt too long — stock data exceeded Claude's context window"
        else:
            return f"Unexpected error: {str(exception)[:120]}"

    @staticmethod
    def is_retryable(error_message: str) -> bool:
        """
        Returns True if the error is transient and worth retrying
        (rate limits, overloads, timeouts).

        Returns False for permanent errors where retrying wastes time
        (bad API key, no credit balance, prompt too long).
        """
        non_retryable = ["API key", "credit exhausted", "too long"]
        return not any(phrase in error_message for phrase in non_retryable)
