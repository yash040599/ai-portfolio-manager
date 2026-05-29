# ================================================================
# core/llm_client.py
# ================================================================
# Unified LLM client — routes to Gemini, GPT, or Claude based on
# Config.AI_PROVIDER.
#
# Drop-in replacement for the old ClaudeClient. Same interface:
#   client = LLMClient(config, log)
#   text   = client.call(prompt)
#
# Error classification and retry-advice are provider-agnostic.
# ================================================================

from __future__ import annotations

import time

from config      import Config
from core.logger import Logger


# Gemini free-tier daily limit (for display only — not tracked
# server-side because Google doesn't expose a usage API and an
# in-memory counter resets on every process restart, which is
# misleading).
GEMINI_FREE_DAILY_LIMIT: int = 500

# Retry config for transient errors (503, rate-limit, timeout).
MAX_RETRIES: int = 3
RETRY_BACKOFF_SECONDS: list[float] = [2.0, 5.0, 10.0]

# Fallback chain: order of providers to try after primary fails.
# Only providers with a configured API key are eligible.
FALLBACK_CHAIN: list[str] = ["gemini", "gpt", "claude"]


class LLMClient:
    """Unified LLM client. Provider is chosen from Config.AI_PROVIDER."""

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log
        self._client = None          # lazy-init, reused across calls
        self._active_provider = None  # tracks which provider was init'd

    # ================================================================
    # API CALL — main entry point
    # ================================================================

    def call(self, prompt: str) -> str:
        """
        Sends a prompt to the active AI provider and returns the
        response text.

        Retry policy (built-in):
          1. Try primary provider up to MAX_RETRIES with exponential
             backoff on transient errors (503, rate-limit, timeout).
          2. If all retries fail AND the error is retryable, offer
             the user an interactive fallback to the next provider
             in FALLBACK_CHAIN that has a configured API key.
          3. Fallback to a PAID provider requires explicit user
             approval via terminal prompt (never auto-charges).
          4. Quota/credit exhaustion is NOT retried — shows top-up
             guide and re-raises immediately.
        """
        provider = self.cfg.AI_PROVIDER
        plan     = self.cfg.ai()
        cost_info = plan['cost_inr_approx']

        self.log.info(
            f"[AI] {provider.upper()} / {plan['model']} "
            f"({self.cfg.AI_PLAN}) — {cost_info}"
        )

        # ── Phase 1: retry primary provider with backoff ──
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return self._dispatch(provider, plan, prompt)
            except Exception as exc:
                last_exc = exc
                err_msg = self.classify_error(exc)

                if self._is_quota_error(exc):
                    # Quota = permanent, don't retry
                    guide = self.topup_guide(provider)
                    print(guide)
                    self.log.error(
                        f"[AI] {provider.upper()} quota/credit exhausted. "
                        f"Add funds or switch provider (see console output)."
                    )
                    raise

                if not self.is_retryable(err_msg):
                    self.log.error(f"[AI] Non-retryable error: {err_msg}")
                    raise

                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                    self.log.warning(
                        f"[AI] {provider.upper()} attempt {attempt + 1}/{MAX_RETRIES} "
                        f"failed: {err_msg}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    self.log.error(
                        f"[AI] {provider.upper()} failed after {MAX_RETRIES} attempts: {err_msg}"
                    )

        # ── Phase 2: interactive fallback to another provider ──
        fallback_provider = self._find_fallback(provider)
        if fallback_provider is None:
            self.log.error("[AI] No fallback provider available (no other API keys configured).")
            raise last_exc  # type: ignore[misc]

        fallback_plan = self._get_provider_plan(fallback_provider)
        is_paid = fallback_plan.get("free_tier") is None
        cost_label = fallback_plan["cost_inr_approx"]

        # Ask user for approval (especially for paid providers)
        print(f"\n  ⚠  {provider.upper()} is unavailable after {MAX_RETRIES} retries.")
        print(f"  →  Fallback available: {fallback_provider.upper()} / {fallback_plan['model']}")
        if is_paid:
            print(f"  💰 This is a PAID provider. Estimated cost: {cost_label}")
        else:
            print(f"  🆓 This is a FREE-tier provider. Cost: {cost_label}")

        try:
            answer = input("  Approve fallback? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer != "y":
            self.log.info("[AI] User declined fallback. Raising original error.")
            raise last_exc  # type: ignore[misc]

        self.log.info(
            f"[AI] User approved fallback → {fallback_provider.upper()} / "
            f"{fallback_plan['model']} ({cost_label})"
        )

        # Single attempt on fallback (no retry chain of chains)
        return self._dispatch(fallback_provider, fallback_plan, prompt)

    # ================================================================
    # DISPATCH (routes to provider-specific method)
    # ================================================================

    def _dispatch(self, provider: str, plan: dict, prompt: str) -> str:
        """Route to the correct provider call method."""
        if provider == "gemini":
            return self._call_gemini(plan, prompt)
        elif provider == "gpt":
            return self._call_gpt(plan, prompt)
        elif provider == "claude":
            return self._call_claude(plan, prompt)
        else:
            raise ValueError(f"Unknown AI_PROVIDER: {provider!r}")

    def _find_fallback(self, failed_provider: str) -> str | None:
        """Find the next provider in FALLBACK_CHAIN with a valid API key."""
        key_map = {
            "gemini": self.cfg.GEMINI_API_KEY,
            "gpt":    self.cfg.OPENAI_API_KEY,
            "claude": self.cfg.CLAUDE_API_KEY,
        }
        for p in FALLBACK_CHAIN:
            if p != failed_provider and key_map.get(p):
                return p
        return None

    def _get_provider_plan(self, provider: str) -> dict:
        """Get the plan dict for a specific provider at current AI_PLAN level."""
        table_attr = self.cfg._AI_PROVIDER_TABLE.get(provider)
        if not table_attr:
            raise ValueError(f"Unknown provider: {provider!r}")
        rules = getattr(self.cfg, table_attr)
        # Try current plan level, fall back to "basic" if not available
        plan_name = self.cfg.AI_PLAN
        if plan_name not in rules:
            plan_name = "basic"
        return rules[plan_name]

    # ================================================================
    # GEMINI
    # ================================================================

    def _call_gemini(self, plan: dict, prompt: str) -> str:
        from google import genai

        if self._client is None or self._active_provider != "gemini":
            self._client = genai.Client(api_key=self.cfg.GEMINI_API_KEY)
            self._active_provider = "gemini"

        response = self._client.models.generate_content(
            model=plan["model"],
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                max_output_tokens=plan["max_tokens"],
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini returned empty response")

        return response.text

    # ================================================================
    # GPT (OpenAI)
    # ================================================================

    def _call_gpt(self, plan: dict, prompt: str) -> str:
        from openai import OpenAI

        if self._client is None or self._active_provider != "gpt":
            self._client = OpenAI(api_key=self.cfg.OPENAI_API_KEY)
            self._active_provider = "gpt"

        response = self._client.responses.create(
            model=plan["model"],
            input=prompt,
            max_output_tokens=plan["max_tokens"],
        )

        text = response.output_text
        if not text:
            raise RuntimeError("GPT returned empty response")

        return text

    # ================================================================
    # CLAUDE (Anthropic)
    # ================================================================

    def _call_claude(self, plan: dict, prompt: str) -> str:
        import anthropic
        import httpx

        if self._client is None or self._active_provider != "claude":
            self._client = anthropic.Anthropic(
                api_key=self.cfg.CLAUDE_API_KEY,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            self._active_provider = "claude"

        message = self._client.messages.create(
            model      = plan["model"],
            max_tokens = plan["max_tokens"],
            messages   = [{"role": "user", "content": prompt}],
        )

        if not message.content:
            raise RuntimeError("Claude returned empty response")

        return message.content[0].text

    # ================================================================
    # ERROR CLASSIFICATION (provider-agnostic)
    # ================================================================

    # Per-provider top-up instructions shown when quota is exhausted.
    _TOPUP_GUIDE: dict[str, str] = {
        "gemini": (
            "\n"
            "  ╔══════════════════════════════════════════════════════════╗\n"
            "  ║  GEMINI — quota / credit exhausted                      ║\n"
            "  ╠══════════════════════════════════════════════════════════╣\n"
            "  ║  Free tier: 500 requests/day, 1M tokens/min.           ║\n"
            "  ║  If you hit the daily cap, wait until midnight PT or:  ║\n"
            "  ║                                                        ║\n"
            "  ║  1. Go to https://aistudio.google.com/apikey           ║\n"
            "  ║  2. Enable billing on your Google Cloud project        ║\n"
            "  ║  3. Paid rate: $0.15/M input + $0.60/M output tokens   ║\n"
            "  ║                                                        ║\n"
            "  ║  Or switch provider in config.py / dashboard:          ║\n"
            "  ║    AI_PROVIDER = \"gpt\"    (needs OPENAI_API_KEY)       ║\n"
            "  ║    AI_PROVIDER = \"claude\" (needs CLAUDE_API_KEY)       ║\n"
            "  ╚══════════════════════════════════════════════════════════╝"
        ),
        "gpt": (
            "\n"
            "  ╔══════════════════════════════════════════════════════════╗\n"
            "  ║  OPENAI GPT — quota / credit exhausted                  ║\n"
            "  ╠══════════════════════════════════════════════════════════╣\n"
            "  ║  Your OpenAI account has no remaining credits.          ║\n"
            "  ║                                                        ║\n"
            "  ║  To add credits:                                       ║\n"
            "  ║  1. Go to https://platform.openai.com/settings/billing ║\n"
            "  ║  2. Add a payment method                               ║\n"
            "  ║  3. Buy credits ($5–10 is plenty to start)             ║\n"
            "  ║                                                        ║\n"
            "  ║  Or switch to a free provider:                         ║\n"
            "  ║    AI_PROVIDER = \"gemini\" (500 free req/day)           ║\n"
            "  ╚══════════════════════════════════════════════════════════╝"
        ),
        "claude": (
            "\n"
            "  ╔══════════════════════════════════════════════════════════╗\n"
            "  ║  CLAUDE — quota / credit exhausted                      ║\n"
            "  ╠══════════════════════════════════════════════════════════╣\n"
            "  ║  Your Anthropic account has no remaining credits.       ║\n"
            "  ║                                                        ║\n"
            "  ║  To add credits:                                       ║\n"
            "  ║  1. Go to https://console.anthropic.com                ║\n"
            "  ║  2. Settings → Billing → Add credits                   ║\n"
            "  ║  3. Rs.500–1000 is a good starting amount              ║\n"
            "  ║                                                        ║\n"
            "  ║  Or switch to a free provider:                         ║\n"
            "  ║    AI_PROVIDER = \"gemini\" (500 free req/day)           ║\n"
            "  ╚══════════════════════════════════════════════════════════╝"
        ),
    }

    @staticmethod
    def _is_quota_error(exception: Exception) -> bool:
        """True if the error is a quota/billing/credit exhaustion."""
        err = str(exception).lower()
        return any(kw in err for kw in (
            "insufficient_quota", "quota", "credit",
            "billing", "exceeded your current",
            "resource_exhausted", "rate_limit",
        ))

    @classmethod
    def topup_guide(cls, provider: str) -> str:
        """Returns the top-up instruction box for a provider."""
        return cls._TOPUP_GUIDE.get(provider, cls._TOPUP_GUIDE["gemini"])

    @staticmethod
    def classify_error(exception: Exception) -> str:
        """Plain-English error message from a raw exception."""
        err = str(exception).lower()

        if "insufficient_quota" in err or "credit" in err or "billing" in err:
            return "API credit exhausted — top up your account (see guidance above)"
        elif "rate_limit" in err or "429" in err or "resource_exhausted" in err:
            return "Rate limit hit — API is busy, will retry"
        elif "timeout" in err or "timed out" in err or "deadline" in err:
            return "Request timed out — model took too long to respond"
        elif "overloaded" in err or "529" in err or "503" in err:
            return "API overloaded — servers under heavy load, will retry"
        elif ("invalid_api_key" in err or "401" in err
              or "api_key_invalid" in err or "invalid api key" in err):
            return "Invalid API key — check your .env file"
        elif "connection" in err or "network" in err:
            return "Network error — check your internet connection"
        elif "context_length" in err or "too long" in err or "token" in err:
            return "Prompt too long — exceeded the model's context window"
        else:
            return f"Unexpected error: {str(exception)[:120]}"

    @staticmethod
    def is_retryable(error_message: str) -> bool:
        """True if the error is transient and worth retrying."""
        non_retryable = ["API key", "credit exhausted", "too long"]
        return not any(phrase in error_message for phrase in non_retryable)


# ── Backward-compat alias ─────────────────────────────────────
# All existing code imports `from core.claude_client import ClaudeClient`.
# That file will re-export this class so nothing breaks.
ClaudeClient = LLMClient
