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
from dataclasses import dataclass, replace
from typing import AsyncIterator, Sequence

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
from ..config import settings
from . import chat_tools
from .benchmark_state import BenchmarkRunSpec
from .chat_state import (
    ChatArtifact,
    ChatScope,
    ChatToolCall,
    ChatTurn,
    new_turn_id,
    utc_now,
)

_SANDBOX_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(sandbox:[^)]+\)")
_SUPPORTED_LLM_PROVIDERS = {"openai-compatible", "pydantic-ai"}
_TRUNCATED_TOOL_RESULT = "[tool result trimmed from conversation history]"


@dataclass
class ChatDeps:
    user_id: str
    session_id: str
    scope: ChatScope


SYSTEM_PROMPT = """You are an expert in AI weather prediction and monsoon onset forecasting, \
helping researchers set up, run, and interpret benchmark results from ROMP (Rainfall Onset Metrics Package).

## Domain knowledge

ROMP evaluates how well AI weather prediction (AIWP) models forecast monsoon onset dates \
compared to observed climatology. Key metrics:

- **mean_mae**: Mean Absolute Error in days between predicted and observed onset date. \
Lower is better. Values under 5 days indicate strong skill; over 15 days indicates poor skill.
- **false_alarm_rate (FAR)**: Fraction of predicted onsets that did not correspond to a real \
onset. Higher means more false alarms.
- **miss_rate (MR)**: Fraction of real onsets the model failed to detect. Higher means more \
missed events.
- Earth2Studio can also add spatial verification metrics such as **rmse**, **mae**, **bias**, \
and **acc** over the full evaluation period. Treat these as rainfall/grid verification metrics, \
not monsoon onset-date metrics.

Forecast windows (e.g. "1-15", "16-30") are lead-time ranges in days. Shorter windows are \
easier; longer windows test extended-range skill. Always compare model metrics against the \
climatology baseline — skill is only meaningful relative to that reference.

## Approach

- Treat the user as being in one continuous benchmark session. During setup, answer normally in prose \
and use benchmark tools to inspect system state, update the canonical benchmark config, validate it, \
and submit it. Never claim the benchmark config changed unless a tool result confirms it.
- If the user asks a conceptual or explanatory question such as "what does climatology mean?", \
"what is a probabilistic model?", "how do these metrics relate?", or "why would I choose this?", \
answer directly from your domain knowledge. Do not update, validate, submit, or summarize the \
benchmark configuration unless the user also asks for a concrete plan change.
- If a benchmark plan already exists and the user asks an unrelated or conceptual question, preserve \
the existing plan silently and answer the question. Do not end every response with a run-plan summary.
- You are not a JSON generator. Explain concepts, ask clarifying questions, and discuss tradeoffs in \
natural language. Operate the application only through tools.
- Use `list_regions`, `list_datasets`, and `list_models` when you need to know what the system can run.
- Use `update_benchmark_config` when the user asks for a concrete plan change. Use \
`validate_benchmark_config` after meaningful changes. Use `submit_benchmark` when the user clearly \
asks to run, submit, start, or launch the benchmark. That tool is protected by human approval, so \
call it directly instead of inventing a confirmation flow. Never claim a benchmark was submitted \
unless the tool result confirms it.
- ROMP run options are stored in `advanced_params`. Shared run options include observation \
overrides, wet/dry spell thresholds, masks, threshold files, and reference-model settings. Model \
evaluation dates are per-model options, not shared climatology options: set them with \
`advanced_params.per_model_params[model_id].start_date` and `.end_date`. Climatology baseline years \
are separate per-model options: `.start_year_clim` and `.end_year_clim`.
- When the user asks about a failed run, do not ask them to paste logs. Use `list_failed_jobs`, \
`get_job_info`, and `get_job_logs` to inspect the failure. Explain the cause and propose the smallest \
validated config or job-parameter change. Use `rerun_job` only after the user asks to rerun or confirms \
the fix.
- Use tools to fetch only the data needed for the question. Do not dump all available metrics \
into the response unprompted.
- Think before fetching: identify what data is required, then make targeted tool calls.
- If a question is ambiguous, ask one clarifying question rather than guessing.
- State uncertainty clearly. Do not overinterpret noisy or sparse metrics.
- Use `run_code` when the built-in metrics don't answer the question — e.g. computing a custom \
statistic, comparing distributions, cross-tabulating results, or producing a chart. The sandbox \
has xarray, numpy, scipy, pandas, and matplotlib. The NC files in `nc_dir` are the \
spatial_metrics_*.nc and e2s_spatial_metrics_*.nc output files. Always handle missing values \
(NaN) explicitly in your code.
- When a chart would communicate the result more clearly than a table or prose, produce one using \
matplotlib. Always use `matplotlib.use('Agg')` before importing pyplot, call \
`artifact = save_figure(fig, filename='plot.webp', format='webp')`, return it under \
`{'artifacts': [artifact]}`, and call `plt.close(fig)` after saving.
- Never manually base64-encode an image, never use `BytesIO` for chart transport, and never \
return keys like `image`, `image_data`, `figure`, or `figure_data`. If you want to return a \
chart, the only supported mechanism is `save_figure(...)` plus the `artifacts` list.

## Output style

- Do not narrate tool use. Never say "Let me fetch…", "I'll now retrieve…", or similar. \
Just call the tools and lead with findings.
- No sycophantic openers ("Great question!") or closing fluff ("I hope this helps!").
- Lead with the answer or key finding, then support it with data.
- Use markdown: bold for key numbers, tables for model comparisons, headers only for \
multi-section responses.
- Be concise. These are researchers who understand statistics — skip obvious interpretation \
and get to the insight.
- When uncertain about what a metric value means in context, say so explicitly."""


def _instructions_for_scope(scope: ChatScope) -> str:
    if not scope.job_ids:
        return SYSTEM_PROMPT
    ids_str = ", ".join(scope.job_ids)
    return (
        f"{SYSTEM_PROMPT}\n\nThis session is scoped to {scope.kind} `{scope.key}`. "
        f"Only use these job IDs unless the scope is explicitly changed: {ids_str}"
    )


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
        return {
            key: _trim_tool_content(item)
            for key, item in value.items()
            if key != "artifacts"
        }
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
    async def list_datasets(
        ctx: RunContext[ChatDeps], region: str | None = None
    ) -> dict:
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
                        current, chat_tools.validation_for_config(current)
                    ),
                }

        return await chat_tools.submit_benchmark_for_session(
            ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
        )

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
    async def get_job_logs(
        ctx: RunContext[ChatDeps], job_id: str, max_chars: int = 12000
    ) -> dict:
        """Fetch logs for a running or failed job so failures can be diagnosed without user copy/paste."""
        return await chat_tools.get_job_logs(
            job_id, max_chars, ctx.deps.user_id, ctx.deps.scope
        )

    @toolset.tool
    async def rerun_job(
        ctx: RunContext[ChatDeps], request: chat_tools.RerunJobRequest
    ) -> dict:
        """Clone and rerun an existing job, optionally overriding ROMP params with validated values."""
        return await chat_tools.rerun_job(request, ctx.deps.user_id, ctx.deps.scope)

    return toolset


def _metrics_toolset() -> FunctionToolset[ChatDeps]:
    toolset = FunctionToolset[ChatDeps](id="metrics")

    @toolset.tool
    async def get_job_metrics(ctx: RunContext[ChatDeps], job_id: str) -> dict:
        """Get aggregate spatial statistics for a completed job."""
        return await chat_tools.get_job_metrics(
            job_id, ctx.deps.user_id, ctx.deps.scope
        )

    @toolset.tool
    async def get_spatial_summary(
        ctx: RunContext[ChatDeps], request: chat_tools.SpatialMetricRequest
    ) -> dict:
        """Get the spatial distribution of a specific metric for a job."""
        return await chat_tools.get_spatial_summary(
            request, ctx.deps.user_id, ctx.deps.scope
        )

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
        async def run_code(
            ctx: RunContext[ChatDeps], request: chat_tools.JobCodeRequest
        ) -> dict:
            """Execute custom Python analysis code against the NC output files for a job."""
            return await chat_tools.run_code(
                request, ctx.deps.user_id, ctx.deps.scope, ctx.deps.session_id
            )

    return toolset


def _build_agent(scope: ChatScope):
    agent = Agent(
        _build_model(),
        output_type=[str, DeferredToolRequests],
        instructions=_instructions_for_scope(scope),
        deps_type=ChatDeps,
        toolsets=[
            _benchmark_toolset(),
            _job_toolset(),
            _metrics_toolset(),
            _analysis_toolset(),
        ],
        history_processors=[trim_chat_history],
    )

    return agent


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


def _tool_result_content(content: object) -> object:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content}
    return content


async def stream_response(
    message_history: list[ModelMessage],
    user_id: str,
    session_id: str,
    session_scope: ChatScope,
    *,
    latest_user_message: str | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
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
    agent = _build_agent(session_scope)
    turn = ChatTurn(
        id=new_turn_id(), role="assistant", content="", created_at=utc_now()
    )
    tool_calls_by_id: dict[str, ChatToolCall] = {}
    final_output: str | None = None
    final_messages: list[ModelMessage] = message_history
    just_finished_tool_call = False

    async for event in agent.run_stream_events(
        latest_user_message,
        message_history=message_history,
        deps=deps,
        deferred_tool_results=deferred_tool_results,
        conversation_id=session_id,
    ):
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            content = event.part.content
            if content:
                if (
                    just_finished_tool_call
                    and turn.content
                    and not turn.content[-1].isspace()
                ):
                    sep = "\n\n"
                    turn.content += sep
                    yield json.dumps(
                        {"type": "text_delta", "turn_id": turn.id, "content": sep}
                    )
                just_finished_tool_call = False
                turn.content += content
                yield json.dumps(
                    {"type": "text_delta", "turn_id": turn.id, "content": content}
                )
            continue

        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            content = event.delta.content_delta
            if not content:
                continue
            if (
                just_finished_tool_call
                and turn.content
                and not turn.content[-1].isspace()
            ):
                sep = "\n\n"
                turn.content += sep
                yield json.dumps(
                    {"type": "text_delta", "turn_id": turn.id, "content": sep}
                )
            just_finished_tool_call = False
            turn.content += content
            yield json.dumps(
                {"type": "text_delta", "turn_id": turn.id, "content": content}
            )
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
            yield json.dumps(
                {
                    "type": "tool_call",
                    "turn_id": turn.id,
                    "tool_call": tool_call.model_dump(mode="json"),
                }
            )
            continue

        if isinstance(event, FunctionToolResultEvent):
            result_part = event.result
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
            if isinstance(parsed_result, dict) and parsed_result.get(
                "benchmark_config"
            ):
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
            just_finished_tool_call = True
            continue

        if isinstance(event, AgentRunResultEvent):
            output = event.result.output
            final_messages = event.result.all_messages()
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
        yield json.dumps(
            {"type": "text_delta", "turn_id": turn.id, "content": final_output}
        )

    turn.content = _SANDBOX_IMAGE_RE.sub("", turn.content).strip()
    yield json.dumps(
        {
            "type": "done",
            "provider_state": serialize_model_messages(final_messages),
            "turn": turn.model_dump(mode="json"),
        }
    )
