"""The only door to an AI model.

Rules enforced here:
* every answer (and every failure) is stored as an ``AIInteraction`` before anyone uses it;
* the same question is never asked twice: a stored answer for the same input is reused;
* if Claude is not configured or fails, a rule-based suggester produces the same shape of
  answer, clearly labelled, so the product still works offline.
A person reviews every answer before it affects a payroll (see the callers).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from django.conf import settings

from payroll_engine.hashing import sha256

from .models import AIInteraction

log = logging.getLogger("payroll.ai")
RULE_BASED = "rule-based"
ANTHROPIC = "anthropic"
CLAUDE_CODE = "claude-code"  # Claude Code CLI in headless mode (`claude -p`), uses the CLI's own login
AI_DISABLED = "AI is turned off for this company"


def fallback_notice(error: str) -> str:
    if error == AI_DISABLED:
        return f"{AI_DISABLED}. A rule-based suggestion was used."
    return "Model assistance was unavailable. A rule-based suggestion was used."


class AIError(Exception):
    pass


def _claude_code_bin() -> str | None:
    return shutil.which(settings.CLAUDE_CODE_BIN) if settings.CLAUDE_CODE_BIN else None


def cli_allowed() -> bool:
    """Claude Code headless runs on one person's CLI login: fine on a laptop, never in production."""
    return bool(settings.DEBUG or settings.AI_ALLOW_CLI)


def provider() -> str:
    """Which provider answers: the one named in AI_PROVIDER, or the first one available, or the rule-based matcher."""
    choice = settings.AI_PROVIDER.lower()
    if choice in ("off", "none", "rule-based", "rules"):
        return RULE_BASED
    named = PROVIDERS.get(ALIASES.get(choice, choice))
    if named:
        return named.key if named.usable_when_named() else RULE_BASED
    return next((p.key for p in PROVIDERS.values() if p.available()), RULE_BASED)


def allowed_for(company) -> bool:
    """Each company decides whether its HR data may be sent to an AI model."""
    return company is None or getattr(company, "ai_mode", "allowed") != "off"


def is_model() -> bool:
    """True when a real model (API or Claude Code) answers, not the rule-based fallback."""
    return provider() in PROVIDERS


def describe() -> dict:
    p = provider()
    model = PROVIDERS[p].model_name() if p in PROVIDERS else "rule-based matcher (no AI model configured)"
    return {
        "provider": p,
        "model": model,
        "used_for": [
            "reading records from an HR screen when it has no recognisable table",
            "finding the employee, salary and leave screens of an HR tool",
            "suggesting which HR field means which payroll field",
            "turning a plain-English instruction into a proposed change",
            "explaining in plain words what changed since last month",
        ],
        "never_used_for": [
            "working out any amount (salary, arrears, PF, ESI, PT, TDS)",
            "deciding which rule or company setting applies",
            "approving, finalising or changing a payroll",
            "checking results against the answer sheet",
        ],
    }


def _call_claude(system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
    import anthropic

    client = anthropic.Anthropic(timeout=settings.AI_TIMEOUT_SECONDS, max_retries=2)
    try:
        response = client.beta.messages.create(
            model=settings.AI_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": settings.AI_EFFORT},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("Claude rejected the API key") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Claude rate limit reached; try again shortly") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Could not reach the Claude API") from exc
    if response.stop_reason == "refusal":
        raise AIError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise AIError("Claude's answer was cut off")
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
    if text is None:
        raise AIError("Claude returned no text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIError("Claude returned invalid JSON") from exc
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "model_served": getattr(response, "model", settings.AI_MODEL),
        "request_id": getattr(response, "_request_id", ""),
    }
    return data, usage


def _call_claude_code(system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
    """One headless Claude Code call: no tools, no saved session, JSON checked against ``schema`` by the CLI."""
    binary = _claude_code_bin()
    if not binary:
        raise AIError(f"Claude Code CLI '{settings.CLAUDE_CODE_BIN}' was not found on PATH")
    cmd = [binary, "-p", "--output-format", "json", "--no-session-persistence", "--tools", "",
           "--system-prompt", system, "--json-schema", json.dumps(schema)]
    if settings.CLAUDE_CODE_MODEL:
        cmd += ["--model", settings.CLAUDE_CODE_MODEL]
    if settings.AI_EFFORT:
        cmd += ["--effort", settings.AI_EFFORT]
    try:
        # Run from a neutral folder so no project instructions or settings are picked up.
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=settings.AI_TIMEOUT_SECONDS,
                              cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired as exc:
        raise AIError(f"Claude Code did not answer within {settings.AI_TIMEOUT_SECONDS:.0f} seconds") from exc
    except OSError as exc:
        raise AIError(f"Could not run Claude Code: {exc}") from exc
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise AIError(f"Claude Code failed (exit {proc.returncode}): {detail[0][:300]}") from exc
    if out.get("is_error") or out.get("subtype") != "success":
        raise AIError(f"Claude Code returned an error: {str(out.get('result') or out.get('subtype'))[:300]}")
    data = out.get("structured_output")
    if data is None:
        try:
            data = json.loads(out.get("result") or "")
        except json.JSONDecodeError as exc:
            raise AIError("Claude Code returned text that is not JSON") from exc
    if not isinstance(data, dict):
        raise AIError("Claude Code returned JSON that is not an object")
    served = next(iter(out.get("modelUsage") or {}), settings.CLAUDE_CODE_MODEL or "claude")
    usage = {
        "model_served": served,
        "input_tokens": (out.get("usage") or {}).get("input_tokens", 0),
        "output_tokens": (out.get("usage") or {}).get("output_tokens", 0),
        "cost_usd": out.get("total_cost_usd"),
        "duration_ms": out.get("duration_ms"),
        "session_id": out.get("session_id", ""),
        "via": "claude -p",
    }
    return data, usage


# --------------------------------------------------------------------------- providers

class ModelProvider:
    """An AI model the system can ask. A new one (another API, a model on the company's own servers) is a new class
    in ``PROVIDERS``; choosing a provider, storing answers and falling back to rules stay the same."""

    key = ""

    def available(self) -> bool:
        """Whether it can be used when AI_PROVIDER does not name a provider."""
        raise NotImplementedError

    def usable_when_named(self) -> bool:
        """Whether it may be used when AI_PROVIDER names it."""
        return True

    def model_name(self) -> str:
        raise NotImplementedError

    def call(self, system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
        """The answer (JSON matching ``schema``) and usage details. Raises ``AIError`` on any failure."""
        raise NotImplementedError


class AnthropicAPI(ModelProvider):
    key = ANTHROPIC

    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) or (
            Path.home() / ".config" / "anthropic"
        ).exists()

    def model_name(self) -> str:
        return settings.AI_MODEL

    def call(self, system, prompt, schema):
        return _call_claude(system, prompt, schema)


class ClaudeCodeCLI(ModelProvider):
    key = CLAUDE_CODE

    def available(self) -> bool:
        return cli_allowed() and bool(_claude_code_bin())

    def usable_when_named(self) -> bool:
        if not cli_allowed():
            log.warning("AI_PROVIDER=claude-code is for development only (DEBUG is off); using the rule-based matcher")
            return False
        return True

    def model_name(self) -> str:
        return f"Claude Code headless (claude -p){', model ' + settings.CLAUDE_CODE_MODEL if settings.CLAUDE_CODE_MODEL else ''}"

    def call(self, system, prompt, schema):
        return _call_claude_code(system, prompt, schema)


# In the order tried when AI_PROVIDER does not name one.
PROVIDERS: dict[str, ModelProvider] = {p.key: p for p in (AnthropicAPI(), ClaudeCodeCLI())}
MODEL_PROVIDERS = tuple(PROVIDERS)
ALIASES = {"anthropic": ANTHROPIC, "claude-code": CLAUDE_CODE, "claude_code": CLAUDE_CODE, "cli": CLAUDE_CODE}


def ask(
    *,
    purpose: str,
    system: str,
    prompt: str,
    schema: dict,
    fallback: Callable[[], dict],
    validate: Callable[[dict], list[dict]] | None = None,
    company=None,
    status: str = "pending_review",
    force: bool = False,
) -> AIInteraction:
    """Ask once, store always. ``fallback`` builds a rule-based answer of the same shape."""
    input_hash = sha256({"purpose": purpose, "system": system, "prompt": prompt, "schema": schema})
    if not force:
        cached = AIInteraction.objects.filter(purpose=purpose, input_hash=input_hash, company=company).exclude(status="error")
        if not allowed_for(company):
            cached = cached.filter(provider=RULE_BASED)
        elif provider() in PROVIDERS:
            cached = cached.exclude(provider=RULE_BASED)  # a model is available now: don't settle for an old rule-based answer
        cached = cached.order_by("-created_at").first()
        if cached:
            AIInteraction.objects.filter(pk=cached.pk).update(reuse_count=cached.reuse_count + 1)
            cached.reuse_count += 1
            cached.reused = True
            return cached

    chosen = provider()
    started = time.monotonic()
    response, usage, error, model = None, {}, "", ""
    if chosen in PROVIDERS and not allowed_for(company):
        chosen, error = RULE_BASED, AI_DISABLED
    if chosen in PROVIDERS:
        try:
            response, usage = PROVIDERS[chosen].call(system, prompt, schema)
            model = usage.get("model_served") or settings.AI_MODEL
        except AIError as exc:
            log.warning("Claude call failed for %s: %s; using rule-based fallback", purpose, exc)
            error = str(exc)
    if response is None:
        response = fallback()
        chosen, model = RULE_BASED, "rule-based"
    checks = validate(response) if validate else []
    interaction = AIInteraction.objects.create(
        company=company,
        purpose=purpose,
        provider=chosen,
        model=model,
        input_hash=input_hash,
        request={"system": system, "prompt": prompt, "schema": schema},
        response=response,
        status=status,
        validation=checks + ([{"level": "warning", "message": fallback_notice(error)}] if error else []),
        latency_ms=int((time.monotonic() - started) * 1000),
        usage=usage,
    )
    interaction.reused = False
    return interaction
