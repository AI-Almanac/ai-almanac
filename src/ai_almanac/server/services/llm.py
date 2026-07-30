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

from . import chat_tools
from .benchmark_state import BenchmarkRunSpec
from .blend_state import BlendRunSpec
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
_llm_semaphores: dict[str, Semaphore] = {}
_llm_request_times: dict[str, deque[float]] = defaultdict(deque)
_llm_limit_lock = Lock()


@dataclass
class ChatDeps:
    user_id: str
    session_id: str
    scope: ChatScope


_PROMPT_DOMAIN = """You are an expert in AI weather prediction and monsoon onset forecasting, \
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

Two climatology baselines appear in blend results, and the user sees these names — use them \
and no others:

- **Traditional Climatology** (model id `unc_clim_raw`): built only from historical onset \
frequencies. Blend skill scores are measured against this baseline, so a score of zero \
matches Traditional Climatology and above zero beats it.
- **Conditional Climatology** (model id `clim_raw`): a stronger baseline that conditions the \
traditional climatological distribution on onset not having occurred yet by the forecast \
date. It is the more demanding reference, and it is scored as a member alongside the models \
rather than being the reference the scores are taken against.

The retired labels "Climatology (unconditional)" and a bare "Climatology" are ambiguous and \
no longer appear anywhere the user can see: the `unc_` prefix means unconditional rather \
than uncalibrated, and the bare label used to mean the conditional baseline. Do not use \
either, and translate them to the current names if the user does. Benchmark (ROMP) runs have \
a single climatology baseline, so "the climatology baseline" is unambiguous there.
"""


CAVEATS_HEADING = "## Interpreting results: caveats"

RESULT_INTERPRETATION_CAVEATS = f"""
{CAVEATS_HEADING}

The caveats below change what benchmark and blend numbers actually mean. They are not \
boilerplate — do not recite them on every answer, and never open with them. Raise the ones \
that bear on the results in front of you: when you report skill, compare models or windows, \
say a difference looks real, or the user asks how much to trust a number. Name the caveat, \
say why it applies here, and say which direction it biases the result.

- **Training overlap.** Skill is optimistic when the evaluation years overlap the years a \
model was trained or fine-tuned on. You do not have machine-readable training or fine-tuning \
periods for these models, so never state that a model was or was not trained on the \
evaluation years, and never invent dates. Raise overlap as something to check, ask the user \
if it matters to their conclusion, and treat any overlap they confirm as an upward bias on \
that model's apparent skill.
- **Pre-satellite era (1965-1978).** ERA5 is less reliable before the satellite era, so \
initial conditions drawn from 1965-1978 understate AI model skill. Skill that looks weak \
only over an evaluation period reaching into those years is suspect, and so is a ranking \
that flips when they are included.
- **Small samples.** Under roughly 10 test years the results are noisy in both directions — \
a model can look much better or much worse than it is. Fewer than 10 years cannot train a \
blending model at all: the fitted weights will not generalize and the resulting skill should \
not be presented as reliable. When the sample is that small, say the differences may not be \
real rather than ranking models confidently.
- **The trilemma.** These first three cannot all be avoided at once — buying more test years \
means overlapping model training years or reaching into the pre-satellite era. No \
configuration escapes all three, so never offer one. The benchmarking paper's approach was \
to benchmark over several periods (a short window clear of training years, a longer window \
that overlaps training, and a longer window reaching pre-satellite) and check whether the \
patterns held across them. Do not do this by default, but raise it when the user's \
conclusion depends on which years they picked.
- **ERA5 versus operational initial conditions.** These models are initialized from ERA5 \
reanalysis, not from the initial conditions that would be available in real time, so \
real-time performance may be worse than the numbers here. Flag this whenever results are \
being read as an estimate of operational skill.
- **Do not over-read the maps.** Per-grid-point maps are noisy at these sample sizes and \
significantly overstate real spatial differences. With few test years, do not narrate \
grid-point detail or call out individual cells as better or worse: describe the broad \
pattern, say the map overstates local contrast, and caution against reading it literally.
"""


_PROMPT_OPERATION = """
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
## Code execution

You have two tools for running Python when the built-in metrics don't answer the question — \
computing a custom statistic, comparing distributions, cross-tabulating results, or producing a chart:

- `run_code(job_id, code)` — runs against a completed job's NetCDF output files.
- `run_code_sandbox(code)` — runs with no data access, for self-contained computation or plotting \
from values you already have.

If these tools appear in your available tools, they work — call them. Do not tell the user you \
cannot execute code, and do not narrate fake attempts. Only if a tool result comes back with an \
`error` field saying the tool is unavailable or disabled should you relay that to the user; never \
retry the same call or invent a workaround.

Your `code` is NOT a top-level script. The harness imports it and then calls a function you must \
define, using its return value as the result. Bare trailing expressions, `print`, and `plt.show()` \
do nothing — only what the function returns is captured.

- For `run_code_sandbox`, define `def compute() -> dict:`.
- For `run_code`, define `def compute(nc_dir: str) -> dict:`. `nc_dir` is a directory path holding \
that job's output files — the `spatial_metrics_*.nc` and `e2s_spatial_metrics_*.nc` files. Open them \
with xarray. Available libraries: xarray, numpy, scipy, pandas, matplotlib. Always handle missing \
values (NaN) explicitly.

The returned dict is shown to you as the tool result. To return a chart, call `save_figure(fig, \
filename='plot.webp', format='webp', label='...')` — it is a builtin already in scope, so do not \
import or define it — and put its return value in an `artifacts` list. Use `matplotlib.use('Agg')` \
before importing pyplot and `plt.close(fig)` after saving. Never base64-encode an image, never use \
`BytesIO`, and never return keys like `image`, `image_data`, `figure`, or `figure_data`; \
`save_figure(...)` plus the `artifacts` list is the only supported mechanism.

Minimal chart example for `run_code`:

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import xarray as xr
from pathlib import Path

def compute(nc_dir: str) -> dict:
    ds = xr.open_dataset(next(Path(nc_dir).glob('spatial_metrics_*.nc')))
    fig, ax = plt.subplots()
    ds['mean_mae'].plot(ax=ax)
    artifact = save_figure(fig, filename='mae.webp', format='webp', label='Mean MAE')
    plt.close(fig)
    return {'median_mae': float(ds['mean_mae'].median()), 'artifacts': [artifact]}
```

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


SYSTEM_PROMPT = _PROMPT_DOMAIN + RESULT_INTERPRETATION_CAVEATS + _PROMPT_OPERATION


BLEND_GUIDANCE = """

This session is set up to configure a forecast blend. A blend combines several \
forecast models into one blended forecast and trains the combining weights \
against an observation dataset. Use `get_blend_config` / `update_blend_config` \
to build the configuration, `validate_blend_config` to check readiness, and \
`submit_blend` to train it once the user approves. Forecast models for a blend \
come from `list_blend_models` (data sources), not the benchmark model registry, \
and are scoped to the observation dataset's region. If the user has already run \
benchmarks in this session, read those results first (job metrics/summaries) and \
let the relative skill inform which models to include in the blend. After a blend \
finishes training, use `get_blend_results` to read its pooled per-model skill \
summary and explain how the blend compares to the individual models.

When proposing or discussing a blend configuration, repeat these cautions:

- Three or more members carries a high risk of overfitting under the current blending \
specification, and the risk grows as the number of training years falls. This is a warning, \
not a prohibition — the user may have a reason — but say it plainly when a proposed blend \
reaches three members instead of quietly adding them.
- Fewer than ten training years cannot train a reliable blend at all. Say so before \
submitting, not after the results come back.
- The interpretation caveats above apply to blend results too: trained weights inherit any \
training-year overlap and pre-satellite unreliability in the member models, and a blend's \
per-grid-point map overstates spatial differences at these sample sizes."""


def _prompt_with_caveats(override: str) -> str:
    """Keep the result-interpretation caveats even when an admin replaces the prompt.

    An admin override is a full replacement of the built-in prompt, so a custom prompt
    would otherwise silently drop the statistical cautions. Skip re-appending them when
    the override already carries the caveats section — the settings UI pre-fills the
    textarea with the built-in prompt, so most overrides are edits of it.

    The heading match ignores case and punctuation so that retouching it does not
    silently ship two copies of the block on every request. Retitling the heading
    outright still duplicates; the settings UI shows the effective prompt.
    """
    if _heading_key(CAVEATS_HEADING) in _heading_key(override):
        return override
    return f"{override}\n{RESULT_INTERPRETATION_CAVEATS}"


def _heading_key(text: str) -> str:
    return re.sub(r"[^a-z]+", " ", text.lower()).strip()


# Scope values are interpolated into the system prompt, so anything that could
# read as a new instruction (newlines, backticks, markdown headings) must not
# survive. Ids and keys are opaque handles; this is deliberately narrow.
_SAFE_SCOPE_TOKEN = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")


def _safe_scope_token(value: str) -> str:
    return value if _SAFE_SCOPE_TOKEN.match(value) else "(unrecognized)"


def _instructions_for_scope(scope: ChatScope) -> str:
    # `settings` is the hot-reloaded singleton (see settings.reload_settings),
    # so this picks up admin edits without a per-message DB read.
    override = settings.chat_system_prompt.strip()
    prompt = _prompt_with_caveats(override) if override else SYSTEM_PROMPT
    if scope.kind == "blend_setup":
        prompt += BLEND_GUIDANCE
    if not scope.job_ids:
        return prompt
    ids_str = ", ".join(_safe_scope_token(job_id) for job_id in scope.job_ids)
    return (
        f"{prompt}\n\nThis session is scoped to {scope.kind} "
        f"`{_safe_scope_token(scope.key)}`. "
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


def _build_agent(scope: ChatScope, model=None):
    agent = Agent(
        model or _build_model(),
        output_type=[str, DeferredToolRequests],
        instructions=_instructions_for_scope(scope),
        deps_type=ChatDeps,
        toolsets=[
            _benchmark_toolset(),
            _blend_toolset(),
            _job_toolset(),
            _metrics_toolset(),
            _analysis_toolset(),
        ],
        capabilities=[ProcessHistory(trim_chat_history)],
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
) -> AsyncIterator[str]:
    semaphore = await _acquire_llm_slot(user_id)
    started = time.perf_counter()
    failure_category: str | None = None
    try:
        async for event in _stream_response_unlimited(
            message_history,
            user_id,
            session_id,
            session_scope,
            latest_user_message=latest_user_message,
            deferred_tool_results=deferred_tool_results,
        ):
            yield event
    except Exception as exc:
        failure_category = type(exc).__name__
        raise
    finally:
        semaphore.release()
        from ai_almanac.server.db import get_db
        from ai_almanac.server.services.events import usage

        async with get_db() as conn:
            await usage(
                conn,
                "llm.request",
                user_id=user_id,
                resource_type="chat_session",
                resource_id=session_id,
                quantity=1,
                metadata={
                    "latency_ms": int((time.perf_counter() - started) * 1000),
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
    model = None
    if settings.deployment_mode == "shared":
        from .llm_profiles import resolve_llm_for_user

        profile = await resolve_llm_for_user(user_id)
        if profile.provider_type == "pydantic-ai":
            if ":" not in profile.model_name:
                raise RuntimeError("Profile model name must include a Pydantic AI provider prefix")
            model = profile.model_name
        elif profile.provider_type == "openai-compatible":
            from openai import AsyncOpenAI

            if not profile.base_url:
                raise RuntimeError("The selected provider has no base URL")
            client = AsyncOpenAI(
                base_url=profile.base_url,
                api_key=profile.api_key,
                timeout=settings.llm_timeout_seconds,
            )
            model = OpenAIChatModel(
                profile.model_name, provider=OpenAIProvider(openai_client=client)
            )
        else:
            raise RuntimeError(f"Unsupported provider type: {profile.provider_type}")
    agent = _build_agent(session_scope, model)
    turn = ChatTurn(id=new_turn_id(), role="assistant", content="", created_at=utc_now())
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
    yield json.dumps(
        {
            "type": "done",
            "provider_state": serialize_model_messages(final_messages),
            "turn": turn.model_dump(mode="json"),
        }
    )
