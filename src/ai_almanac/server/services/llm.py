"""
LLM service — PydanticAI agent with typed tools for job data access.

Provider is configured via LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.
The default provider targets OpenAI chat-completions-compatible endpoints.

Tools give the LLM structured access to benchmark state and job results without
pre-loading everything into the prompt. The backend owns durable state and
validation; the LLM operates through typed tool calls.
"""

from __future__ import annotations

import json
import re
import time
from asyncio import Lock, Semaphore
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    ApprovalRequired,
    DeferredToolRequests,
    DeferredToolResults,
    ModelMessage,
    ModelMessagesTypeAdapter,
    RunContext,
)
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import FunctionToolset

from ai_almanac.settings import settings

from . import chat_tools, rulesets
from .benchmark_state import BenchmarkRunSpec
from .blend_state import BlendRunSpec
from .chat_state import (
    ChatArtifact,
    ChatScope,
    ChatToolCall,
    ChatTurn,
    GuardrailNotice,
    new_turn_id,
    utc_now,
)
from .rulesets import Ruleset
from .turn_log import TurnRecord, record_turn

_SANDBOX_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(sandbox:[^)]+\)")
_SUPPORTED_LLM_PROVIDERS = {"openai-compatible", "pydantic-ai"}
_TRUNCATED_TOOL_RESULT = "[tool result trimmed from conversation history]"
_llm_semaphores: dict[str, Semaphore] = {}
_llm_request_times: dict[str, deque[float]] = defaultdict(deque)
_llm_limit_lock = Lock()


@dataclass
class ChatDeps:
    user_id: str
    session_id: str
    scope: ChatScope


# Scope values are interpolated into the system prompt, so anything that could
# read as a new instruction (newlines, backticks, markdown headings) must not
# survive. Ids and keys are opaque handles; this is deliberately narrow.
_SAFE_SCOPE_TOKEN = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")


def _safe_scope_token(value: str) -> str:
    return value if _SAFE_SCOPE_TOKEN.match(value) else "(unrecognized)"


def _scope_suffix(scope: ChatScope) -> str:
    if not scope.job_ids:
        return ""
    ids_str = ", ".join(_safe_scope_token(job_id) for job_id in scope.job_ids)
    return (
        f"\n\nThis session is scoped to {scope.kind} "
        f"`{_safe_scope_token(scope.key)}`. "
        f"Only use these job IDs unless the scope is explicitly changed: {ids_str}"
    )


def _instructions_for_ruleset(ruleset: Ruleset, scope: ChatScope) -> str:
    """Assemble the system prompt for a ruleset and a session scope.

    The ruleset owns the wording, section ordering, and which sections apply to
    which scope kind. This function owns only the scope suffix, because the
    sanitizing of scope ids belongs next to the tested guard above and must not
    become an admin-editable string.
    """
    return rulesets.build_instructions(ruleset, scope.kind) + _scope_suffix(scope)


def serialize_model_messages(messages: Sequence[ModelMessage]) -> list[dict]:
    """Serialize pydantic-ai message history to JSON-compatible objects."""
    return json.loads(ModelMessagesTypeAdapter.dump_json(list(messages)))


def deserialize_model_messages(value: object) -> list[ModelMessage]:
    if isinstance(value, str):
        value = json.loads(value) if value.strip() else []
    return ModelMessagesTypeAdapter.validate_python(value or [])


def llm_is_configured() -> bool:
    provider = settings.llm_provider.lower()
    if provider == "openai-compatible":
        return bool(settings.llm_base_url)
    if provider == "pydantic-ai":
        return bool(settings.llm_model)
    return False


def _build_model() -> OpenAIChatModel | str:
    from openai import AsyncOpenAI

    provider_name = settings.llm_provider.lower()
    if provider_name not in _SUPPORTED_LLM_PROVIDERS:
        supported = ", ".join(sorted(_SUPPORTED_LLM_PROVIDERS))
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER {settings.llm_provider!r}; expected one of {supported}"
        )

    if provider_name == "pydantic-ai":
        if ":" not in settings.llm_model:
            raise RuntimeError(
                "LLM_MODEL must be a provider-prefixed Pydantic AI model string "
                "when LLM_PROVIDER=pydantic-ai"
            )
        return settings.llm_model

    if not settings.llm_base_url:
        raise RuntimeError("LLM_BASE_URL is not configured")
    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    provider = OpenAIProvider(openai_client=client)
    return OpenAIChatModel(settings.llm_model, provider=provider)


def _trim_text(value: str) -> str:
    max_chars = settings.llm_tool_result_max_chars
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n\n{_TRUNCATED_TOOL_RESULT}"


def _trim_tool_content(value: object) -> object:
    if isinstance(value, str):
        return _trim_text(value)
    if isinstance(value, list):
        return [_trim_tool_content(item) for item in value]
    if isinstance(value, dict):
        return {key: _trim_tool_content(item) for key, item in value.items() if key != "artifacts"}
    return value


async def trim_chat_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    max_messages = settings.llm_history_max_messages
    if max_messages > 0 and len(messages) > max_messages:
        messages = messages[-max_messages:]

    trimmed: list[ModelMessage] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            trimmed.append(message)
            continue
        parts = [
            replace(part, content=_trim_tool_content(part.content))
            if isinstance(part, ToolReturnPart)
            else part
            for part in message.parts
        ]
        trimmed.append(replace(message, parts=parts))
    return trimmed


def _benchmark_toolset() -> FunctionToolset[ChatDeps]:
    toolset = FunctionToolset[ChatDeps](id="benchmark")

    @toolset.tool
    async def list_regions(ctx: RunContext[ChatDeps]) -> dict:
        """List benchmark regions and whether each has configured observation data."""
        return await chat_tools.list_regions(ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def list_datasets(ctx: RunContext[ChatDeps], region: str | None = None) -> dict:
        """List available ground-truth observation datasets, optionally filtered by region id."""
        return await chat_tools.list_datasets(region, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def list_models(ctx: RunContext[ChatDeps], region: str | None = None) -> dict:
        """List available forecast models, optionally filtered by region id."""
        return await chat_tools.list_models(region, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def get_benchmark_config(ctx: RunContext[ChatDeps]) -> dict:
        """Read the canonical benchmark configuration attached to this chat session."""
        return await chat_tools.get_benchmark_config(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

    @toolset.tool
    async def update_benchmark_config(
        ctx: RunContext[ChatDeps], patch: chat_tools.BenchmarkConfigPatch
    ) -> dict:
        """Patch and validate the canonical benchmark configuration for this chat session."""
        return await chat_tools.update_benchmark_config(
            patch.model_dump(exclude_none=True),
            ctx.deps.user_id,
            ctx.deps.scope,
            ctx.deps.session_id,
        )

    @toolset.tool
    async def validate_benchmark_config(ctx: RunContext[ChatDeps]) -> dict:
        """Validate the current benchmark configuration and report run readiness."""
        return await chat_tools.validate_benchmark_config(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

    @toolset.tool
    async def submit_benchmark(ctx: RunContext[ChatDeps]) -> dict:
        """Submit the current benchmark plan after pydantic-ai human approval."""
        if not ctx.tool_call_approved:
            payload = await chat_tools.propose_benchmark_submit(
                ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
            )
            if payload.get("error") or not payload.get("approval_required"):
                return payload
            raise ApprovalRequired(metadata=payload)

        approved_config_payload = (ctx.tool_call_metadata or {}).get("approved_config")
        if approved_config_payload:
            current = await chat_tools.get_current_benchmark_config(
                ctx.deps.session_id, ctx.deps.user_id
            )
            approved = BenchmarkRunSpec.model_validate(approved_config_payload)
            if current.model_dump(mode="json") != approved.model_dump(mode="json"):
                return {
                    "error": "Benchmark config changed after approval; please review and approve the updated plan.",
                    **chat_tools.benchmark_payload(
                        current, await chat_tools.validation_for_config(current)
                    ),
                }

        return await chat_tools.submit_benchmark_for_session(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

    return toolset


def _blend_toolset() -> FunctionToolset[ChatDeps]:
    toolset = FunctionToolset[ChatDeps](id="blend")

    @toolset.tool
    async def list_blend_models(ctx: RunContext[ChatDeps], region: str | None = None) -> dict:
        """List forecast model data sources available to blend, optionally filtered by region id."""
        return await chat_tools.list_blend_models(region, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def get_blend_config(ctx: RunContext[ChatDeps]) -> dict:
        """Read the canonical blend configuration attached to this chat session."""
        return await chat_tools.get_blend_config(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

    @toolset.tool
    async def update_blend_config(
        ctx: RunContext[ChatDeps], patch: chat_tools.BlendConfigPatch
    ) -> dict:
        """Patch and validate the canonical blend configuration for this chat session."""
        return await chat_tools.update_blend_config(
            patch.model_dump(exclude_none=True),
            ctx.deps.user_id,
            ctx.deps.scope,
            ctx.deps.session_id,
        )

    @toolset.tool
    async def validate_blend_config(ctx: RunContext[ChatDeps]) -> dict:
        """Validate the current blend configuration and report run readiness."""
        return await chat_tools.validate_blend_config(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

    @toolset.tool
    async def submit_blend(ctx: RunContext[ChatDeps]) -> dict:
        """Submit the current blend for training after pydantic-ai human approval."""
        if not ctx.tool_call_approved:
            payload = await chat_tools.propose_blend_submit(
                ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
            )
            if payload.get("error") or not payload.get("approval_required"):
                return payload
            raise ApprovalRequired(metadata=payload)

        approved_config_payload = (ctx.tool_call_metadata or {}).get("approved_config")
        if approved_config_payload:
            current = await chat_tools.get_current_blend_config(
                ctx.deps.session_id, ctx.deps.user_id
            )
            approved = BlendRunSpec.model_validate(approved_config_payload)
            if current.model_dump(mode="json") != approved.model_dump(mode="json"):
                return {
                    "error": "Blend config changed after approval; please review and approve the updated plan.",
                    **chat_tools.blend_payload(
                        current,
                        await chat_tools.blend_validation_for_config(current, ctx.deps.user_id),
                    ),
                }

        return await chat_tools.submit_blend_for_session(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

    @toolset.tool
    async def get_blend_results(ctx: RunContext[ChatDeps], job_id: str) -> dict:
        """Read a completed blend's skill, to explain its results.

        Returns pooled per-model scores (Ranked Probability Skill Score, Brier Skill
        Score, Area Under ROC Curve, and Brier skill per lead time), a per-grid-point
        summary of where the blend beats Traditional Climatology, and the weight/output
        artifacts. All skill is against Traditional Climatology (`unc_clim_raw`): zero
        matches it, above zero beats it. Conditional Climatology (`clim_raw`) appears as
        a scored member, not as the reference. Prefer the Ranked Probability Skill Score
        as the headline — it is the metric that accounts for how far off a forecast was,
        not merely whether it was wrong, which is what the five ordered onset windows
        call for.

        Check how many years the blend was trained and scored on before interpreting
        any of it. Under roughly ten years the scores are noisy in both directions and
        differences between members may not be real; the per-grid-point summary is
        noisier still and overstates spatial differences, so do not narrate individual
        grid points. Member skill is also optimistic where the scored years overlap a
        model's training or fine-tuning period, and pessimistic where they reach into
        the pre-satellite era (1965-1978)."""
        return await chat_tools.get_blend_results(job_id, ctx.deps.user_id, ctx.deps.scope)

    return toolset


def _job_toolset() -> FunctionToolset[ChatDeps]:
    toolset = FunctionToolset[ChatDeps](id="jobs")

    @toolset.tool
    async def list_jobs(ctx: RunContext[ChatDeps]) -> dict:
        """List the user's benchmark jobs available in this chat scope, including running and failed jobs."""
        return await chat_tools.list_jobs(ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def list_failed_jobs(ctx: RunContext[ChatDeps]) -> dict:
        """List failed benchmark jobs in this chat scope with stored error summaries."""
        return await chat_tools.list_jobs(ctx.deps.user_id, ctx.deps.scope, "failed")

    @toolset.tool
    async def get_job_info(ctx: RunContext[ChatDeps], job_id: str) -> dict:
        """Get configuration details for a specific job."""
        return await chat_tools.get_job_info(job_id, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def get_job_logs(ctx: RunContext[ChatDeps], job_id: str, max_chars: int = 12000) -> dict:
        """Fetch logs for a running or failed job so failures can be diagnosed without user copy/paste."""
        return await chat_tools.get_job_logs(job_id, max_chars, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def rerun_job(ctx: RunContext[ChatDeps], request: chat_tools.RerunJobRequest) -> dict:
        """Clone and rerun an existing job, optionally overriding ROMP params with validated values."""
        return await chat_tools.rerun_job(request, ctx.deps.user_id, ctx.deps.scope)

    return toolset


def _metrics_toolset() -> FunctionToolset[ChatDeps]:
    toolset = FunctionToolset[ChatDeps](id="metrics")

    @toolset.tool
    async def get_job_metrics(ctx: RunContext[ChatDeps], job_id: str) -> dict:
        """Get aggregate spatial statistics (false alarm rate, miss rate, mean absolute
        error) for a completed job. Deterministic models only — an ensemble model
        produces no spatial metrics, so use get_skill_scores for those.
        """
        return await chat_tools.get_job_metrics(job_id, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def get_skill_scores(ctx: RunContext[ChatDeps], job_id: str) -> dict:
        """Get probabilistic verification scores for a completed ensemble job: Brier
        Score, Brier Skill Score, Ranked Probability Score and its skill score, and
        Area Under ROC Curve, both pooled and broken down by forecast lead-time bin.

        Use this for any probabilistic (ensemble) model. Such jobs produce no spatial
        metrics at all, so get_job_metrics will return nothing for them. Read the
        `notes` field in the response before interpreting the numbers.
        """
        return await chat_tools.get_skill_scores(job_id, ctx.deps.user_id, ctx.deps.scope)

    @toolset.tool
    async def get_spatial_summary(
        ctx: RunContext[ChatDeps], request: chat_tools.SpatialMetricRequest
    ) -> dict:
        """Get the spatial distribution of a specific metric for a job."""
        return await chat_tools.get_spatial_summary(request, ctx.deps.user_id, ctx.deps.scope)

    return toolset


def _analysis_toolset() -> FunctionToolset[ChatDeps]:
    toolset = FunctionToolset[ChatDeps](id="analysis")

    if chat_tools.is_tool_available("run_code_sandbox"):

        @toolset.tool
        async def run_code_sandbox(
            ctx: RunContext[ChatDeps], request: chat_tools.CodeSandboxRequest
        ) -> dict:
            """Run arbitrary Python code in an isolated sandbox with no network access."""
            return await chat_tools.run_code_sandbox(
                request, ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
            )

    if chat_tools.is_tool_available("run_code"):

        @toolset.tool
        async def run_code(ctx: RunContext[ChatDeps], request: chat_tools.JobCodeRequest) -> dict:
            """Execute custom Python analysis code against the NC output files for a job."""
            return await chat_tools.run_code(
                request, ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
            )

    return toolset


# Tools the assistant must never be given, whatever a ruleset says. A tool that
# could read or write a ruleset, a guardrail threshold, or a setting would put
# the rules back inside the model's reach, which is the one thing this design
# exists to prevent. Asserted in the test suite so a future toolset addition
# that crosses the line fails rather than ships.
SELF_CONFIGURATION_TOOL_PATTERNS = ("ruleset", "guardrail", "setting", "prompt", "user_")


def _apply_tool_policy(
    toolsets: list[FunctionToolset[ChatDeps]], ruleset: Ruleset
) -> list[FunctionToolset[ChatDeps]]:
    """Drop denied tools at registration, so they have no schema entry at all.

    Filtering here rather than refusing at call time means there is nothing for
    the model to attempt and nothing to explain in the prompt — the same approach
    ``_analysis_toolset`` already takes for tools the deployment cannot run.
    """
    denied = set(ruleset.tool_policy.deny)
    if not denied:
        return toolsets
    for toolset in toolsets:
        for name in denied & set(toolset.tools):
            del toolset.tools[name]
    return toolsets


def _build_agent(scope: ChatScope, ruleset: Ruleset, model=None):
    toolsets = _apply_tool_policy(
        [
            _benchmark_toolset(),
            _blend_toolset(),
            _job_toolset(),
            _metrics_toolset(),
            _analysis_toolset(),
        ],
        ruleset,
    )
    return Agent(
        model or _build_model(),
        output_type=[str, DeferredToolRequests],
        instructions=_instructions_for_ruleset(ruleset, scope),
        deps_type=ChatDeps,
        toolsets=toolsets,
        model_settings=ruleset.model_settings or None,
        capabilities=[ProcessHistory(trim_chat_history)],
    )


def _tool_event_args(args: object) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {"raw": args}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _guardrail_event(turn_id: str, tool_call_id: str, parsed_result: object) -> str | None:
    """A guardrail event for any validation carried by a tool result.

    Emitted by the stream, from the tool result, rather than by the model. The
    assistant is expected to explain these findings well, but the user is told
    either way: no prose can suppress this event and no instruction in the
    conversation can turn it off. That is the whole point of routing the
    statistical cautions through here instead of trusting the prompt.
    """
    if not isinstance(parsed_result, dict):
        return None
    validation = parsed_result.get("benchmark_validation") or parsed_result.get("blend_validation")
    if not isinstance(validation, dict):
        return None
    errors = [item for item in validation.get("errors") or [] if isinstance(item, str)]
    warnings = [item for item in validation.get("warnings") or [] if isinstance(item, str)]
    if not errors and not warnings:
        return None
    keys = [item for item in validation.get("finding_keys") or [] if isinstance(item, str)]
    return json.dumps(
        {
            "type": "guardrail",
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "errors": errors,
            "warnings": warnings,
            "finding_keys": keys,
        }
    )


def _tool_result_content(content: object) -> object:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content}
    return content


async def _acquire_llm_slot(user_id: str) -> Semaphore:
    now = time.monotonic()
    async with _llm_limit_lock:
        request_times = _llm_request_times[user_id]
        while request_times and now - request_times[0] >= 60:
            request_times.popleft()
        if len(request_times) >= settings.max_llm_requests_per_minute:
            raise RuntimeError("LLM request rate limit exceeded")
        request_times.append(now)
        semaphore = _llm_semaphores.setdefault(
            user_id, Semaphore(settings.max_concurrent_llm_requests_per_user)
        )
    await semaphore.acquire()
    return semaphore


async def stream_response(
    message_history: list[ModelMessage],
    user_id: str,
    session_id: str,
    session_scope: ChatScope,
    *,
    latest_user_message: str | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
    active_ruleset: Ruleset | None = None,
    comparison_id: str | None = None,
    turn_id: str | None = None,
) -> AsyncIterator[str]:
    semaphore = await _acquire_llm_slot(user_id)
    started = time.perf_counter()
    failure_category: str | None = None
    # Filled in by the inner generator as the turn streams, written once here so
    # the log lands on the failure path too — a turn that errored is exactly the
    # kind a ruleset comparison needs to count.
    record = TurnRecord(
        session_id=session_id,
        user_id=user_id,
        scope_kind=session_scope.kind,
        comparison_id=comparison_id,
    )
    try:
        async for event in _stream_response_unlimited(
            message_history,
            user_id,
            session_id,
            session_scope,
            latest_user_message=latest_user_message,
            deferred_tool_results=deferred_tool_results,
            active_ruleset=active_ruleset,
            record=record,
            turn_id=turn_id,
        ):
            yield event
    except Exception as exc:
        failure_category = type(exc).__name__
        raise
    finally:
        semaphore.release()
        from ai_almanac.server.db import get_db
        from ai_almanac.server.services.events import usage

        record.latency_ms = int((time.perf_counter() - started) * 1000)
        record.failure_category = failure_category
        await record_turn(record)

        async with get_db() as conn:
            await usage(
                conn,
                "llm.request",
                user_id=user_id,
                resource_type="chat_session",
                resource_id=session_id,
                quantity=1,
                metadata={
                    "latency_ms": record.latency_ms,
                    "failure_category": failure_category,
                },
            )


async def _stream_response_unlimited(
    message_history: list[ModelMessage],
    user_id: str,
    session_id: str,
    session_scope: ChatScope,
    *,
    latest_user_message: str | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
    active_ruleset: Ruleset | None = None,
    record: TurnRecord | None = None,
    turn_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Run one turn of the conversation, yielding SSE-formatted data lines.

    PydanticAI owns provider/tool orchestration for the turn. The backend
    still owns durable chat state, benchmark state, validation, and artifacts.

    Yields JSON strings (without the 'data: ' prefix — the router adds that).
    """
    deps = ChatDeps(
        user_id=user_id,
        session_id=session_id,
        scope=session_scope,
    )
    ruleset = active_ruleset or await rulesets.active_ruleset()
    # A ruleset may pin a model name (that is how a comparison runs two models
    # against one policy). It must never decide *credentials*: in a shared
    # deployment the user's profile still resolves, so a user who chose to use
    # their own key keeps using it and their prompts do not silently move onto
    # the host's provider. The pin substitutes the model name on whatever
    # provider that resolution produced.
    model = None
    if settings.deployment_mode == "shared":
        from .llm_profiles import resolve_llm_for_user

        profile = await resolve_llm_for_user(user_id)
        model_name = ruleset.model or profile.model_name
        if profile.provider_type == "pydantic-ai":
            if ":" not in model_name:
                raise RuntimeError("Profile model name must include a Pydantic AI provider prefix")
            model = model_name
        elif profile.provider_type == "openai-compatible":
            from openai import AsyncOpenAI

            if not profile.base_url:
                raise RuntimeError("The selected provider has no base URL")
            client = AsyncOpenAI(
                base_url=profile.base_url,
                api_key=profile.api_key,
                timeout=settings.llm_timeout_seconds,
            )
            model = OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client))
        else:
            raise RuntimeError(f"Unsupported provider type: {profile.provider_type}")
    elif ruleset.model:
        # Personal install: one set of credentials from env, so a pinned model
        # name is unambiguous.
        model = ruleset.model
    agent = _build_agent(session_scope, ruleset, model)
    # The caller's id when it has one: the transcript keeps its own turn id, and
    # a rating looks the log row up by it — a fresh id here would orphan the row.
    turn = ChatTurn(id=turn_id or new_turn_id(), role="assistant", content="", created_at=utc_now())
    if record is not None:
        record.turn_id = turn.id
        record.ruleset_id = ruleset.id
        record.ruleset_version = ruleset.version
        record.model_name = getattr(model, "model_name", None) or (
            model if isinstance(model, str) else settings.llm_model
        )
    tool_calls_by_id: dict[str, ChatToolCall] = {}
    final_output: str | None = None
    final_messages: list[ModelMessage] = message_history
    just_finished_tool_call = False

    # pydantic-ai 2.0: run_stream_events is an async context manager (it owns a
    # background run task). Wrap it in a generator so the event-handling body
    # below stays unchanged while the stream is still closed deterministically.
    async def _events() -> AsyncIterator[object]:
        async with agent.run_stream_events(
            latest_user_message,
            message_history=message_history,
            deps=deps,
            deferred_tool_results=deferred_tool_results,
            conversation_id=session_id,
        ) as event_stream:
            async for event in event_stream:
                yield event

    async for event in _events():
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            content = event.part.content
            if content:
                if just_finished_tool_call and turn.content and not turn.content[-1].isspace():
                    sep = "\n\n"
                    turn.content += sep
                    yield json.dumps({"type": "text_delta", "turn_id": turn.id, "content": sep})
                just_finished_tool_call = False
                turn.content += content
                yield json.dumps({"type": "text_delta", "turn_id": turn.id, "content": content})
            continue

        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            content = event.delta.content_delta
            if not content:
                continue
            if just_finished_tool_call and turn.content and not turn.content[-1].isspace():
                sep = "\n\n"
                turn.content += sep
                yield json.dumps({"type": "text_delta", "turn_id": turn.id, "content": sep})
            just_finished_tool_call = False
            turn.content += content
            yield json.dumps({"type": "text_delta", "turn_id": turn.id, "content": content})
            continue

        if isinstance(event, FunctionToolCallEvent):
            part = event.part
            args = _tool_event_args(part.args_as_dict())
            tool_call = ChatToolCall(
                id=part.tool_call_id,
                name=part.tool_name,
                status="running",
                input=args,
            )
            tool_calls_by_id[tool_call.id] = tool_call
            turn.tool_calls.append(tool_call)
            if record is not None:
                record.tool_calls.append(tool_call.name)
            yield json.dumps(
                {
                    "type": "tool_call",
                    "turn_id": turn.id,
                    "tool_call": tool_call.model_dump(mode="json"),
                }
            )
            continue

        if isinstance(event, FunctionToolResultEvent):
            result_part = event.part  # pydantic-ai 2.0 renamed `.result` to `.part`
            tool_call_id = getattr(result_part, "tool_call_id", "")
            parsed_result = _tool_result_content(getattr(result_part, "content", None))
            status = (
                "failed"
                if isinstance(parsed_result, dict) and parsed_result.get("error")
                else "completed"
            )
            tool_call = tool_calls_by_id.get(tool_call_id)
            if tool_call is not None:
                tool_call.status = status
                tool_call.result = parsed_result
            if isinstance(parsed_result, dict):
                for artifact_payload in parsed_result.get("artifacts", []):
                    if not isinstance(artifact_payload, dict):
                        continue
                    artifact = ChatArtifact.model_validate(artifact_payload)
                    if tool_call is not None:
                        tool_call.artifacts.append(artifact)
                    turn.artifacts.append(artifact)
                    yield json.dumps(
                        {
                            "type": "artifact",
                            "turn_id": turn.id,
                            "tool_call_id": tool_call_id,
                            "artifact": artifact.model_dump(mode="json"),
                        }
                    )
            yield json.dumps(
                {
                    "type": "tool_result",
                    "turn_id": turn.id,
                    "tool_call_id": tool_call_id,
                    "status": status,
                    "result": parsed_result,
                }
            )
            guardrail_event = _guardrail_event(turn.id, tool_call_id, parsed_result)
            if guardrail_event is not None:
                yield guardrail_event
                payload = json.loads(guardrail_event)
                # Also record it on the turn itself. The `done` event ships this
                # turn as the persisted one, so a finding left only on the SSE
                # stream would render live and then vanish the moment the turn
                # was replaced — and be gone entirely on reload.
                turn.guardrails.append(
                    GuardrailNotice(
                        tool_call_id=tool_call_id,
                        errors=payload["errors"],
                        warnings=payload["warnings"],
                        finding_keys=payload["finding_keys"],
                    )
                )
                if record is not None:
                    record.guardrail_keys.extend(payload["finding_keys"])
            if isinstance(parsed_result, dict) and parsed_result.get("benchmark_config"):
                if parsed_result.get("approval_required"):
                    yield json.dumps(
                        {
                            "type": "benchmark_approval_request",
                            "turn_id": turn.id,
                            "tool_call_id": tool_call_id,
                            "config": parsed_result["benchmark_config"],
                            "validation": parsed_result.get("benchmark_validation"),
                        }
                    )
                else:
                    yield json.dumps(
                        {
                            "type": "benchmark_config",
                            "turn_id": turn.id,
                            "config": parsed_result["benchmark_config"],
                            "validation": parsed_result.get("benchmark_validation"),
                            "run_id": parsed_result.get("run_id"),
                            "jobs": parsed_result.get("jobs"),
                        }
                    )
            if isinstance(parsed_result, dict) and parsed_result.get("blend_config"):
                if parsed_result.get("approval_required"):
                    yield json.dumps(
                        {
                            "type": "blend_approval_request",
                            "turn_id": turn.id,
                            "tool_call_id": tool_call_id,
                            "config": parsed_result["blend_config"],
                            "validation": parsed_result.get("blend_validation"),
                        }
                    )
                else:
                    yield json.dumps(
                        {
                            "type": "blend_config",
                            "turn_id": turn.id,
                            "config": parsed_result["blend_config"],
                            "validation": parsed_result.get("blend_validation"),
                            "run_id": parsed_result.get("run_id"),
                            "jobs": parsed_result.get("jobs"),
                        }
                    )
            just_finished_tool_call = True
            continue

        if isinstance(event, AgentRunResultEvent):
            output = event.result.output
            final_messages = event.result.all_messages()
            if record is not None:
                # `.usage` is the RunUsage itself on this result type, not a
                # method as it is on AgentRunResult.
                usage_totals = event.result.usage
                record.input_tokens = getattr(usage_totals, "input_tokens", None)
                record.output_tokens = getattr(usage_totals, "output_tokens", None)
                record.requests = getattr(usage_totals, "requests", None)
            if isinstance(output, str):
                final_output = output
            elif isinstance(output, DeferredToolRequests):
                for call in output.approvals:
                    metadata = output.metadata.get(call.tool_call_id, {})
                    if call.tool_name == "submit_benchmark":
                        yield json.dumps(
                            {
                                "type": "benchmark_approval_request",
                                "turn_id": turn.id,
                                "tool_call_id": call.tool_call_id,
                                "config": metadata.get("benchmark_config"),
                                "validation": metadata.get("benchmark_validation"),
                            }
                        )
                    elif call.tool_name == "submit_blend":
                        yield json.dumps(
                            {
                                "type": "blend_approval_request",
                                "turn_id": turn.id,
                                "tool_call_id": call.tool_call_id,
                                "config": metadata.get("blend_config"),
                                "validation": metadata.get("blend_validation"),
                            }
                        )
                    else:
                        yield json.dumps(
                            {
                                "type": "tool_approval_request",
                                "turn_id": turn.id,
                                "tool_call": {
                                    "id": call.tool_call_id,
                                    "name": call.tool_name,
                                    "input": _tool_event_args(call.args),
                                    "status": "running",
                                },
                                "metadata": metadata,
                            }
                        )

    if final_output is not None and not turn.content:
        turn.content = final_output
        yield json.dumps({"type": "text_delta", "turn_id": turn.id, "content": final_output})

    turn.content = _SANDBOX_IMAGE_RE.sub("", turn.content).strip()
    if record is not None:
        record.text = turn.content
    yield json.dumps(
        {
            "type": "done",
            "provider_state": serialize_model_messages(final_messages),
            "turn": turn.model_dump(mode="json"),
        }
    )
