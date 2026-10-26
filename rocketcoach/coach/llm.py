"""Optional language-model reasoning for post-match reports.

Never called per frame. The default provider is "none": every report is complete without it. The Claude
provider receives only a compact JSON summary of the match (metrics, top mistakes, plan): no video, no
raw telemetry, no player names or account data.

Guards: hourly request limit, monthly USD budget, timeout, SDK retries, output-token cap, and a
response cache keyed by the exact prompt so re-generating a report is free.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import time
from typing import Any

from rocketcoach.config import LLMConfig
from rocketcoach.storage.db import Database

log = logging.getLogger(__name__)

# USD per million tokens (input, output), first-party API, cached 2026-10-06. Unknown models use the Opus price.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}

SYSTEM = (
    "You are a Rocket League 2v2 coach reviewing one offline match against bots for a Diamond 1 player who wants "
    "to reach Champion. You receive measured telemetry statistics, rule-engine findings with confidence scores, and "
    "a training plan. Write a short debrief (under 220 words): the single most important habit to change, why it "
    "cost goals in this match (cite the timestamps given), one thing to keep doing, and how to approach the next "
    "session. Treat items marked 'estimate' or 'low confidence' as tentative and say so. Do not invent statistics, "
    "controller inputs, or training-pack codes that are not in the data."
)


class LLMUnavailable(Exception):
    pass


class Provider(abc.ABC):
    name = "abstract"

    @abc.abstractmethod
    def summarize(self, compact: dict[str, Any]) -> str | None: ...


class NoneProvider(Provider):
    name = "none"

    def summarize(self, compact: dict[str, Any]) -> str | None:
        return None


def compact_report(report: dict) -> dict:
    """The only data sent to a hosted model."""
    return {
        "result": report["result"],
        "simulated": report["data_source"]["simulated"],
        "mistakes": [{k: m[k] for k in ("tip", "category", "count", "explanation", "avg_confidence", "timestamps",
                                        "low_confidence")} for m in report["mistakes"]],
        "strengths": report["strengths"],
        "grades": [{k: g.get(k) for k in ("area", "grade", "confidence")} for g in report["grades"]],
        "boost": {k: report["boost"][k] for k in ("pct_below", "spent_supersonic", "starved_out_of_play_s",
                                                  "low_boost_ok_s", "avg_challenge_boost")},
        "mechanics": {k: v for k, v in report["mechanics"].items() if k != "not_measured"},
        "trend_last5": report["comparison"].get("5"),
        "plan": [{k: f.get(k) for k in ("skill", "why", "name", "code", "success")}
                 for f in report["training_plan"]["focus"]],
    }


class ClaudeProvider(Provider):
    name = "anthropic"

    def __init__(self, cfg: LLMConfig, db: Database | None) -> None:
        self.cfg = cfg
        self.db = db
        key = os.environ.get(cfg.api_key_env)
        try:
            import anthropic  # optional dependency
        except ImportError as exc:
            raise LLMUnavailable("Install the optional dependency: pip install anthropic") from exc
        self._anthropic = anthropic
        # With no key in the environment the SDK falls back to an `ant auth login` profile, if any.
        self.client = anthropic.Anthropic(api_key=key, timeout=cfg.timeout_seconds,
                                          max_retries=cfg.max_retries) if key else \
            anthropic.Anthropic(timeout=cfg.timeout_seconds, max_retries=cfg.max_retries)

    def _budget_ok(self) -> tuple[bool, str]:
        if self.db is None:
            return True, ""
        spent_month, _ = self.db.llm_spend_since(time.time() - 30 * 86400)
        if spent_month >= self.cfg.monthly_budget_usd:
            return False, f"monthly budget reached (${spent_month:.2f} of ${self.cfg.monthly_budget_usd:.2f})"
        _, n_hour = self.db.llm_spend_since(time.time() - 3600)
        if n_hour >= self.cfg.max_requests_per_hour:
            return False, f"rate limit: {n_hour} requests in the last hour"
        return True, ""

    @staticmethod
    def cost(model: str, tin: int, tout: int) -> float:
        pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
        return (tin * pin + tout * pout) / 1e6

    def summarize(self, compact: dict[str, Any]) -> str | None:
        payload = json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
        key = hashlib.sha256(f"{self.cfg.model}|{SYSTEM}|{payload}".encode()).hexdigest()
        if self.db is not None:
            cached = self.db.llm_cached(key)
            if cached:
                return cached
        ok, why = self._budget_ok()
        if not ok:
            log.info("Claude summary skipped: %s", why)
            return None
        a = self._anthropic
        try:
            resp = self.client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=self.cfg.max_output_tokens,
                system=SYSTEM,
                messages=[{"role": "user", "content": f"Match data (JSON):\n{payload}"}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError:
            log.error("Claude API key rejected; set %s or run `ant auth login`.", self.cfg.api_key_env)
            return None
        except a.RateLimitError:
            log.warning("Claude API rate limited; report saved without AI summary.")
            return None
        except a.APIStatusError as e:
            log.warning("Claude API error %s; report saved without AI summary.", e.status_code)
            return None
        except (a.APIConnectionError, a.APITimeoutError):
            log.warning("Claude API unreachable (offline?); report saved without AI summary.")
            return None
        tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
        cost = self.cost(self.cfg.model, tin, tout)
        if resp.stop_reason == "refusal":
            text = None
        else:
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip() or None
        if self.db is not None:
            self.db.llm_record(self.name, self.cfg.model, tin, tout, cost, key, text)
        log.info("Claude summary: %d in / %d out tokens, $%.4f", tin, tout, cost)
        return text


def make_provider(cfg: LLMConfig, db: Database | None) -> Provider:
    if cfg.provider == "anthropic":
        try:
            return ClaudeProvider(cfg, db)
        except LLMUnavailable as exc:
            log.warning("%s; continuing in local-only mode.", exc)
    return NoneProvider()
