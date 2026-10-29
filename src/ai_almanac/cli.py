"""ai-almanac CLI.

Entry point exposed via `pyproject.toml [project.scripts]` as `ai-almanac`.
"""

from __future__ import annotations

import os
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import click
import typer

from ai_almanac import __version__
from ai_almanac.paths import data_root, ensure_layout, env_root, secrets_env_path

# How long `serve` lets open responses finish on shutdown before cancelling
# them, leaving the rest of a container's stop grace period for the lifespan
# hook to put the local model to sleep.
SHUTDOWN_GRACE_SECONDS = 10

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local-first benchmarking platform for AI weather and climate models.",
)

db_app = typer.Typer(help="Manage the application database.")
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Upgrade the configured database to the latest schema."""
    from ai_almanac.server.app import _apply_migrations
    from ai_almanac.settings import reload_settings

    reload_settings()
    _apply_migrations()
    typer.echo("database upgraded")


sources_app = typer.Typer(help="Find and register observation and model data sources.")
app.add_typer(sources_app, name="sources")


@sources_app.command("discover")
def sources_discover(
    root: Annotated[
        Path | None,
        typer.Argument(help="Directory to scan (default: the dataset mount roots)."),
    ] = None,
    register: Annotated[
        bool,
        typer.Option(
            "--register",
            help="Register every ready source found that has a region and isn't registered yet.",
        ),
    ] = False,
    names: Annotated[
        Path | None,
        typer.Option(
            "--names",
            help=(
                "YAML file giving sources' names (and live forecast model links) by "
                "path relative to the scanned directory, e.g. deploy/spark-datasets.yaml."
            ),
        ),
    ] = None,
) -> None:
    """Find NetCDF data sources under a directory and show how each would register.

    Sources registered here are shared, as an administrator's are in the UI.
    """
    import asyncio

    from ai_almanac.server.app import _apply_migrations
    from ai_almanac.server.services.dataset_resolver import mount_roots
    from ai_almanac.settings import reload_settings

    reload_settings()
    roots = [root.expanduser().resolve()] if root else mount_roots()
    if not roots:
        typer.secho(
            "no dataset mount roots are configured; give a directory to scan",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    missing = [str(path) for path in roots if not path.is_dir()]
    if missing:
        typer.secho(f"not a directory: {', '.join(missing)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    overrides = _load_source_names(names) if names else {}
    _apply_migrations()
    asyncio.run(_discover_sources(roots, register, overrides))


_SOURCE_NAME_KEYS = frozenset({"name", "forecast_model_id", "obs_date_marks"})


def _load_source_names(path: Path) -> dict[str, dict]:
    """Read a `sources:` map of relative path -> {name, forecast_model_id,
    obs_date_marks}."""
    import yaml

    try:
        sources = (yaml.safe_load(path.read_text()) or {}).get("sources") or {}
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        typer.secho(f"cannot read {path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    for rel, entry in sources.items():
        valid = (
            isinstance(entry, dict)
            and not set(entry) - _SOURCE_NAME_KEYS
            and str(entry.get("name") or "").strip()
        )
        if not valid:
            typer.secho(
                f"{path}: {rel!r} needs a name, and only {sorted(_SOURCE_NAME_KEYS)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
    return {str(Path(rel)): entry for rel, entry in sources.items()}


def _apply_source_names(discovery, overrides: dict[str, dict]) -> list[str]:
    """Rename discovered sources the overrides list; return the listed paths not found."""
    found = set()
    for source in discovery.sources:
        path = Path(source.path)
        root = next((Path(r) for r in discovery.roots if path.is_relative_to(r)), None)
        rel = str(path.relative_to(root)) if root else None
        entry = overrides.get(rel) if rel else None
        if entry is None:
            continue
        found.add(rel)
        source.name = str(entry["name"]).strip()
        if source.kind == "model" and entry.get("forecast_model_id"):
            source.metadata = {**source.metadata, "forecast_model_id": entry["forecast_model_id"]}
        if source.kind == "obs" and entry.get("obs_date_marks"):
            source.metadata = {**source.metadata, "obs_date_marks": entry["obs_date_marks"]}
    return sorted(set(overrides) - found)


async def _discover_sources(
    roots: list[Path], register: bool, overrides: dict[str, dict] | None = None
) -> None:
    from ai_almanac.server import db
    from ai_almanac.server.services import data_sources, region_catalog, source_discovery

    try:
        await region_catalog.seed_packaged_regions()
        discovery = await source_discovery.discover_sources(roots)
        missing = _apply_source_names(discovery, overrides or {})
        typer.echo(f"scanned {', '.join(discovery.roots)}")
        for rel in missing:
            typer.secho(f"  named but not found: {rel}", fg=typer.colors.YELLOW)
        if discovery.truncated:
            typer.secho(
                f"stopped after {source_discovery.MAX_DIRECTORIES} directories; "
                "scan a narrower directory to see the rest",
                fg=typer.colors.YELLOW,
            )
        registered = 0
        for source in discovery.sources:
            metadata = source.metadata
            years = f"{metadata.get('start_year')}–{metadata.get('end_year')}"
            variable = metadata.get("obs_var") or metadata.get("model_var")
            pattern = metadata.get("obs_file_pattern") or metadata.get("file_pattern")
            typer.echo(
                f"  {source.status:<7} {source.kind:<5} {source.name}  "
                f"[{source.region or 'no region'}]  {source.path}  "
                f"{pattern} · {variable} · {years}"
            )
            if source.validation_error:
                typer.secho(f"          {source.validation_error}", fg=typer.colors.RED)
            if source.registered_as:
                typer.echo(f"          already registered as {source.registered_as!r}")
            elif register and source.status == "ready" and source.region:
                row = await data_sources.create_source(
                    kind=source.kind,
                    name=source.name,
                    path=source.path,
                    region=source.region,
                    metadata=metadata,
                    owner_id=None,
                    visibility="shared",
                )
                registered += 1
                typer.secho(f"          registered ({row['status']})", fg=typer.colors.GREEN)
            elif register and source.status == "ready":
                typer.secho(
                    "          not registered: no region matched; register it from the UI",
                    fg=typer.colors.YELLOW,
                )
        for skipped in discovery.skipped:
            typer.echo(f"  skipped {skipped.path}: {skipped.reason}")
        if not discovery.sources:
            typer.echo("no data sources found")
        elif register:
            typer.echo(f"registered {registered} source(s)")
        else:
            typer.echo("re-run with --register to register the ready sources")
    finally:
        # asyncio.run closes its loop on return; release pooled connections first.
        await db.engine.dispose()


@app.command("execute-job", hidden=True)
def execute_job_command(job_id: str) -> None:
    """Run a detached job supervisor."""
    from ai_almanac.server.services.job_manager import execute_job
    from ai_almanac.settings import reload_settings

    reload_settings()
    execute_job(job_id)


@app.command("run-job-workload", hidden=True)
def run_job_workload_command(job_id: str) -> None:
    """Run the computational child process supervised by execute-job."""
    from ai_almanac.server.services.job_workload import run_job_workload
    from ai_almanac.settings import reload_settings

    reload_settings()
    run_job_workload(job_id)


@app.command()
def serve(
    bind: Annotated[str, typer.Option(help="Host to bind to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
    reload: Annotated[bool, typer.Option(help="Enable auto-reload (dev).")] = False,
    token: Annotated[
        bool, typer.Option("--token/--no-token", help="Require a bearer token for all requests.")
    ] = False,
    allow_network_data_dir: Annotated[
        bool,
        typer.Option(
            "--allow-network-data-dir",
            envvar="AI_ALMANAC_ALLOW_NETWORK_DATA_DIR",
            help="Allow the data dir to be on a network filesystem (SQLite corruption risk).",
        ),
    ] = False,
    container: Annotated[
        bool,
        typer.Option(
            "--container",
            envvar="AI_ALMANAC_CONTAINER",
            help=(
                "Running inside a container whose port is published only on the host's "
                "loopback: allow binding all interfaces and always require an access token."
            ),
        ),
    ] = False,
) -> None:
    """Boot the ai-almanac web server (FastAPI + bundled UI)."""
    from ai_almanac.settings import reload_settings, settings

    reload_settings()
    if (
        settings.deployment_mode != "shared"
        and not container
        and bind not in ("127.0.0.1", "localhost", "::1")
    ):
        typer.secho(
            f"refusing to bind to {bind!r}.\n"
            "ai-almanac currently supports local, single-user operation only.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if container:
        # Read by the app's shutdown hook, which then stops the local model
        # rather than leaving it asleep for the container's end to kill.
        os.environ["AI_ALMANAC_CONTAINER"] = "1"

    ensure_layout()

    # 1. Network-FS check
    from ai_almanac.fscheck import network_fs_type

    fs = network_fs_type(data_root())
    if fs:
        db_url = settings.resolve_database_url()
        is_sqlite = db_url.startswith("sqlite")
        if is_sqlite and not allow_network_data_dir:
            typer.secho(
                f"data dir {data_root()} is on a network filesystem ({fs}).\n"
                "SQLite on network filesystems risks database corruption under concurrent writers.\n"
                "To proceed anyway, re-run with --allow-network-data-dir.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        typer.secho(
            f"warning: data dir {data_root()} is on a network filesystem ({fs}).",
            fg=typer.colors.YELLOW,
            err=True,
        )

    # 2. Secrets bootstrap (personal mode)
    if settings.deployment_mode == "personal":
        from ai_almanac.secrets_bootstrap import ensure_local_secrets

        if ensure_local_secrets():
            reload_settings()
            typer.echo(f"generated secrets at {secrets_env_path()}")

    # A published container port is reachable by every local user of the host,
    # so container mode never serves without a token; it persists across restarts.
    if container and settings.deployment_mode == "personal":
        from ai_almanac.secrets_bootstrap import ensure_access_token

        if ensure_access_token():
            reload_settings()

    # 3. Token handling
    import secrets as _secrets_mod

    effective_token = settings.serve_access_token
    if token and not effective_token:
        effective_token = _secrets_mod.token_urlsafe(24)
        os.environ["SERVE_ACCESS_TOKEN"] = effective_token
        reload_settings()

    url = f"http://{bind if bind != '0.0.0.0' else 'localhost'}:{port}/"
    open_url = f"{url}?token={effective_token}" if effective_token else url

    # 4. First-run setup check
    from ai_almanac.server.services.setup import get_or_create_bootstrap_token, setup_required

    setup_url: str | None = None
    if setup_required():
        bootstrap_token = get_or_create_bootstrap_token()
        setup_url = f"http://127.0.0.1:{port}/setup?token={bootstrap_token}"

    # 5. Startup print
    typer.echo(f"ai-almanac {__version__} serving at {url}")
    typer.echo(f"  data dir: {data_root()}")
    if env_root() != data_root():
        typer.echo(f"  env root: {env_root()}")
    if effective_token:
        typer.echo(f"  access token required — open: {open_url}")
    if setup_url:
        typer.echo(f"  first-run setup: {setup_url}")

    if open_browser and not reload:
        with suppress(Exception):
            webbrowser.open(setup_url if setup_url else open_url)

    import uvicorn

    uvicorn.run(
        "ai_almanac.server.app:app",
        host=bind,
        port=port,
        reload=reload,
        log_level="info",
        # Unbounded, shutdown waits for every open response, and a streaming
        # chat reply can outlast a container's stop grace period (60 s in
        # compose.spark.yaml): the lifespan hook that puts the local model to
        # sleep never ran, and the model server was SIGKILLed mid-reply.
        # Streams still open after this are cancelled, closing their requests.
        timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
    )


env_app = typer.Typer(help="Manage the benchmark environment (pixi-backed).")
app.add_typer(env_app, name="env")


@env_app.command("prepare")
def env_prepare(
    if_stale: Annotated[
        bool,
        typer.Option(
            "--if-stale",
            help="Only reinstall the environments this version of AI Almanac changed.",
        ),
    ] = False,
) -> None:
    """Install or update the benchmark, blending, and forecast environments.

    Also installs the local model server (and its weights) when the assistant
    is set to run on this machine.
    """
    import subprocess

    from ai_almanac.envs.manager import (
        download_llm_model,
        ensure_env,
        llm_env_ready,
        stale_envs,
    )
    from ai_almanac.settings import reload_settings, settings

    reload_settings()
    include_llm = settings.llm_engine == "managed"
    only = None
    if if_stale:
        only = stale_envs(include_llm=include_llm)
        if not only:
            typer.echo("workload environments are up to date")
            return
        typer.echo(f"updating the {', '.join(only)} environment(s) this version changed")

    def wanted(name: str) -> bool:
        return only is None or name in only

    try:
        benchmark_dir, blending_dir, forecast_dir = ensure_env(include_llm=include_llm, only=only)
        if include_llm and wanted("llm") and llm_env_ready():
            download_llm_model(settings.llm_model)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if wanted("benchmark"):
        typer.echo(f"benchmark env ready at {benchmark_dir}")
    if wanted("blending"):
        typer.echo(f"blending env ready at {blending_dir}")
    if forecast_dir is not None:
        typer.echo(f"forecast env ready at {forecast_dir}")
    if include_llm and wanted("llm") and llm_env_ready():
        typer.echo(f"local model {settings.llm_model} ready")


@env_app.command("check-speedups")
def env_check_speedups(
    model: Annotated[str, typer.Option(help="earth2studio model class to check.")] = "AIFS2ENS",
    steps: Annotated[int, typer.Option(help="Rollout steps compared.")] = 8,
    date: Annotated[str, typer.Option(help="Initial condition (UTC).")] = "2026-05-13T00:00",
) -> None:
    """Check a model's inference speed-ups in its forecast env.

    The exact ones bit for bit, the others against the forecast's chance
    spread. Run after the forecast environment is upgraded: a pass names the
    version to add to the speed-up module's VERIFIED. Uses the GPU.
    """
    from pathlib import Path

    import ai_almanac.envs as envs
    from ai_almanac.envs.manager import run_forecast
    from ai_almanac.paths import cache_dir
    from ai_almanac.settings import get_packaged_forecast_models, reload_settings

    reload_settings()
    entry = next(
        (
            m
            for m in get_packaged_forecast_models().get("models") or []
            if m.get("earth2studio_class") == model
        ),
        None,
    )
    if entry is None:
        typer.secho(f"No registered forecast model runs {model}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    env = os.environ.copy()
    # Kept, so a second check downloads no initial conditions.
    env["EARTH2STUDIO_DATA_CACHE"] = str(cache_dir() / "aifs-speedups-check")
    script = Path(envs.__file__).with_name("speedups_check.py")
    command = ["python", str(script), "--model", model, "--steps", str(steps), "--date", date]
    try:
        process = run_forecast(command, env=env, environment=entry.get("env") or "base")
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    assert process.stdout is not None
    for line in process.stdout:
        typer.echo(line, nl=False)
    raise typer.Exit(code=process.wait())


@env_app.command("info")
def env_info() -> None:
    """Show installed package versions in the benchmark environment."""
    from ai_almanac.envs.manager import env_versions

    typer.echo(f"env root: {env_root()}")
    for name, version in env_versions().items():
        typer.echo(f"{name:20s} {version}")


llm_app = typer.Typer(help="Manage the local assistant model server (vLLM).")
app.add_typer(llm_app, name="llm")


def _local_llm_model_lines() -> list[str]:
    from ai_almanac.envs.manager import llm_model_cached
    from ai_almanac.settings import get_local_llm_models

    lines = []
    for i, m in enumerate(get_local_llm_models(), start=1):
        tags = [f"{m['weights_gb']:.0f} GB"]
        if llm_model_cached(m["id"]):
            tags.append("downloaded")
        if not m["verified"]:
            tags.append("not yet tested on a Spark")
        lines.append(f"[{i}] {m['label']} ({', '.join(tags)})\n    {m['id']}")
    return lines


def _prompt_local_llm_model() -> str:
    """Pick a catalog model by number, or type any other Hugging Face id."""
    from ai_almanac.settings import DEFAULT_LOCAL_LLM_MODEL, get_local_llm_models

    models = get_local_llm_models()
    typer.echo("Local model:")
    for line in _local_llm_model_lines():
        typer.echo(f"  {line}")
    typer.echo(f"  [{len(models) + 1}] Other Hugging Face model")
    default = next(
        (str(i) for i, m in enumerate(models, start=1) if m["id"] == DEFAULT_LOCAL_LLM_MODEL), "1"
    )
    choice = typer.prompt(
        "Model",
        default=default,
        type=click.IntRange(1, len(models) + 1),
    )
    if choice <= len(models):
        return models[choice - 1]["id"]
    typer.secho(
        "set its tool-call parser in llm_serve_args, or the assistant's tools won't work",
        fg=typer.colors.YELLOW,
    )
    return typer.prompt("Hugging Face model id")


@llm_app.command("models")
def llm_models() -> None:
    """List the models the local model server has tested serve flags for."""
    from ai_almanac.settings import reload_settings, settings

    reload_settings()
    for line in _local_llm_model_lines():
        typer.echo(line)
    if settings.llm_engine == "managed":
        typer.echo(f"\ncurrent: {settings.llm_model}")


@llm_app.command("use")
def llm_use(
    model_id: Annotated[str, typer.Argument(help="Hugging Face model id (see `llm models`).")],
    download: Annotated[
        bool,
        typer.Option(
            "--download/--no-download",
            help="Fetch the weights now (default). Otherwise vLLM fetches them on first start.",
        ),
    ] = True,
) -> None:
    """Switch the assistant to a model served on this machine.

    A running `serve` picks the change up on the next chat turn, restarting the
    local model server with the new model.
    """
    import subprocess

    from ai_almanac.envs.manager import download_llm_model, llm_env_ready
    from ai_almanac.server.services.setup import save_llm
    from ai_almanac.settings import local_llm_model, reload_settings, settings

    reload_settings()
    entry = local_llm_model(model_id)
    if entry is None and not settings.llm_serve_args.strip():
        typer.secho(
            f"{model_id} is not in `ai-almanac llm models`; set its tool-call parser in "
            "llm_serve_args, or the assistant's tools won't work",
            fg=typer.colors.YELLOW,
        )
    elif entry is not None and settings.llm_serve_args.strip():
        typer.secho(
            "llm_serve_args is set and replaces this model's tested flags; clear it in "
            "Settings to use them",
            fg=typer.colors.YELLOW,
        )
    if entry is not None and not entry["verified"]:
        typer.secho(f"{model_id} has not been tested on a DGX Spark yet", fg=typer.colors.YELLOW)
    if download:
        if not llm_env_ready():
            typer.secho(
                "the local model server is not installed — run `ai-almanac env prepare`",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            download_llm_model(model_id)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    save_llm("", model_id, None, engine="managed")
    typer.echo(f"assistant will run {model_id} locally")


@llm_app.command("status")
def llm_status() -> None:
    """Show whether the local model server is off, loading, ready, or asleep."""
    from ai_almanac.server.services import local_llm
    from ai_almanac.settings import reload_settings

    reload_settings()
    typer.echo(local_llm.status())


@llm_app.command("stop")
def llm_stop() -> None:
    """Stop the local model server.

    `serve` leaves it asleep on shutdown so the next start can wake it instead
    of loading from scratch; this frees the rest of its memory.
    """
    from ai_almanac.server.services import local_llm
    from ai_almanac.settings import reload_settings

    reload_settings()
    local_llm.stop()
    typer.echo("local model server stopped")


@app.command()
def backup(
    dest: Annotated[str, typer.Option(help="Destination directory for backup files.")] = "",
) -> None:
    """Back up the database, config.yaml, and secrets.env to a timestamped directory."""
    import shutil
    import sqlite3
    from datetime import datetime

    from ai_almanac.settings import reload_settings, settings

    reload_settings()
    db_url = settings.resolve_database_url()
    if not db_url.startswith("sqlite"):
        typer.secho(
            "backup only supports SQLite databases. Use pg_dump for Postgres.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    dest_dir = (data_root() / "backups") if not dest else __import__("pathlib").Path(dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # DB path: strip sqlite+aiosqlite:/// prefix
    db_path_str = db_url.split("///", 1)[-1]
    db_backup = dest_dir / f"almanac-{stamp}.db"
    src = sqlite3.connect(db_path_str)
    dst = sqlite3.connect(str(db_backup))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    typer.echo(f"database → {db_backup}")

    config_src = data_root() / "config.yaml"
    if config_src.exists():
        config_dest = dest_dir / f"config-{stamp}.yaml"
        shutil.copy2(str(config_src), str(config_dest))
        typer.echo(f"config   → {config_dest}")

    secrets_src = secrets_env_path()
    if secrets_src.exists():
        secrets_dest = dest_dir / f"secrets-{stamp}.env"
        shutil.copy2(str(secrets_src), str(secrets_dest))
        secrets_dest.chmod(0o600)
        typer.echo(f"secrets  → {secrets_dest} (0600)")

    typer.echo("note: uploads and job outputs are not included in this backup.")


@app.command()
def init(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompts.")] = False,
    local_llm: Annotated[
        bool | None,
        typer.Option(
            "--local-llm/--no-local-llm",
            help=(
                "Run the assistant's model on this machine with a bundled vLLM "
                "(default with --yes: when an NVIDIA GPU is detected on Linux)."
            ),
        ),
    ] = None,
    llm_base_url: Annotated[str, typer.Option(help="OpenAI-compatible base URL.")] = "",
    llm_model: Annotated[
        str,
        typer.Option(help="Model name; with --local-llm, the Hugging Face model id to serve."),
    ] = "",
    llm_api_key: Annotated[str, typer.Option(help="API key (sealed in DB overlay).")] = "",
    dataset_mount_roots: Annotated[
        str, typer.Option(help="Comma-separated dataset mount root paths.")
    ] = "",
    output_dir: Annotated[str, typer.Option(help="Job output directory.")] = "",
    prepare_envs: Annotated[
        bool | None,
        typer.Option(
            "--prepare-envs/--no-prepare-envs",
            help="Install environments (default: yes).",
        ),
    ] = None,
    include_forecast: Annotated[
        bool | None,
        typer.Option(
            "--include-forecast/--no-include-forecast",
            help="Include forecast envs (default: yes when an NVIDIA GPU is detected).",
        ),
    ] = None,
    skip_llm_test: Annotated[
        bool, typer.Option("--skip-llm-test", help="Save LLM config without testing.")
    ] = False,
    if_needed: Annotated[
        bool,
        typer.Option(
            "--if-needed", help="Do nothing if setup is already complete (for scripted installs)."
        ),
    ] = False,
) -> None:
    """Interactive first-run configuration (headless-friendly with --yes)."""
    import asyncio

    from ai_almanac.secrets_bootstrap import ensure_local_secrets
    from ai_almanac.server.app import _apply_migrations
    from ai_almanac.server.services.setup import (
        detect_platform,
        finish_setup,
        grandfather_existing_install,
        probe_gpu,
        save_llm,
        save_storage,
        setup_required,
        test_llm_connection,
    )
    from ai_almanac.settings import DEFAULT_LOCAL_LLM_MODEL, reload_settings

    reload_settings()
    ensure_layout()
    _apply_migrations()
    if ensure_local_secrets():
        reload_settings()

    grandfather_existing_install()
    reload_settings()

    if not setup_required():
        if if_needed:
            typer.echo("setup already complete")
            return
        if yes:
            typer.echo("setup already complete — re-running with --yes.")
        else:
            typer.confirm("Setup already complete. Re-run anyway?", abort=True)

    # System info
    plat = detect_platform()
    gpu = probe_gpu()
    typer.echo(f"platform : {plat.get('platform')} ({plat.get('machine')})")
    typer.echo(f"gpu      : {gpu['name'] if gpu else 'none detected'}")
    typer.echo(f"data dir : {data_root()}")
    typer.echo("")

    # Storage
    out_dir_val = output_dir.strip() or None
    mounts_val = [r.strip() for r in dataset_mount_roots.split(",") if r.strip()]
    if not yes:
        out_dir_val = (
            typer.prompt("Job output directory (leave blank for default)", default="") or None
        )
        mounts_input = typer.prompt(
            "Dataset mount roots (comma-separated, blank for none)", default=""
        )
        mounts_val = [r.strip() for r in mounts_input.split(",") if r.strip()]
    save_storage(out_dir_val, mounts_val if mounts_val else None)
    typer.echo("storage settings saved")

    # LLM
    base_url_val = llm_base_url.strip()
    model_val = llm_model.strip()
    key_val = llm_api_key.strip() or None

    can_run_local = gpu is not None and str(plat.get("platform", "")).startswith("linux")
    if local_llm is None:
        if base_url_val:
            local_llm = False
        elif yes:
            local_llm = can_run_local
        else:
            choice = typer.prompt(
                "Assistant model — [1] run a model on this machine, "
                "[2] connect to an existing server",
                default="1" if can_run_local else "2",
                type=click.Choice(["1", "2"]),
                show_choices=False,
            )
            local_llm = choice == "1"

    if local_llm:
        if not model_val:
            model_val = DEFAULT_LOCAL_LLM_MODEL if yes else _prompt_local_llm_model()
        # No connection test: the server is installed below and started by
        # `serve`, which reports when it's ready.
        save_llm("", model_val, None, engine="managed")
        typer.echo(f"assistant will run {model_val} locally")
    else:
        if not base_url_val and not yes:
            base_url_val = typer.prompt("LLM base URL", default="http://localhost:11434/v1")
        if not model_val and not yes:
            model_val = typer.prompt("LLM model name")
        if not key_val and not yes:
            key_val = typer.prompt("LLM API key (blank if not required)", default="") or None

        if base_url_val and model_val:
            if not skip_llm_test:
                typer.echo("testing LLM connection…")
                result = asyncio.run(test_llm_connection(base_url_val, model_val, key_val))
                if not result.ok:
                    typer.secho(f"LLM test failed: {result.error}", fg=typer.colors.RED, err=True)
                    if yes:
                        raise typer.Exit(code=1)
                    typer.confirm("Save anyway?", abort=True)
            save_llm(base_url_val, model_val, key_val)
            typer.echo("LLM settings saved")
        else:
            typer.secho(
                "skipping LLM configuration (no URL/model provided)", fg=typer.colors.YELLOW
            )

    # Env prepare — on by default so init leaves a runnable install behind.
    if prepare_envs is None:
        if yes:
            prepare_envs = True
        else:
            prepare_envs = typer.confirm("Install benchmark environments now?", default=True)

    if prepare_envs:
        import subprocess

        from ai_almanac.envs.manager import download_llm_model, ensure_env, llm_env_ready

        if include_forecast is None:
            has_gpu = gpu is not None
            if yes:
                include_forecast = has_gpu
            else:
                include_forecast = typer.confirm(
                    "Install forecast environments (large download, needs an NVIDIA GPU)?",
                    default=has_gpu,
                )
        if not include_forecast:
            typer.echo(
                "skipping forecast environments"
                + ("" if gpu else " (no GPU detected; pass --include-forecast to force)")
            )

        def _progress(evt: object) -> None:
            from ai_almanac.envs.manager import EnvProgressEvent

            if isinstance(evt, EnvProgressEvent):
                if evt.kind == "line" and evt.line:
                    typer.echo(f"  {evt.line}")
                elif evt.kind in (
                    "phase_started",
                    "phase_finished",
                    "phase_failed",
                    "phase_skipped",
                ):
                    typer.echo(f"▸ [{evt.phase}] {evt.detail or evt.kind}")

        try:
            benchmark_dir, blending_dir, forecast_dir = ensure_env(
                progress=_progress, include_forecast=include_forecast, include_llm=bool(local_llm)
            )
            typer.echo(f"benchmark env ready at {benchmark_dir}")
            typer.echo(f"blending env ready at {blending_dir}")
            if forecast_dir:
                typer.echo(f"forecast env ready at {forecast_dir}")
            if local_llm and llm_env_ready():
                typer.echo(f"downloading {model_val} (tens of GB on first run)…")
                download_llm_model(model_val, progress=_progress)
                typer.echo(f"local model {model_val} ready")
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            if yes:
                typer.secho("continuing despite env error", fg=typer.colors.YELLOW)
            else:
                typer.confirm("Environment install failed. Continue to finish setup?", abort=True)

    elif local_llm:
        typer.secho(
            "the local model is not installed yet — run `ai-almanac env prepare` before serving",
            fg=typer.colors.YELLOW,
        )

    # Finish
    finish_setup()
    typer.secho("✓ setup complete", fg=typer.colors.GREEN)
    typer.echo(
        f"next step: {typer.style('ai-almanac serve', fg=typer.colors.BRIGHT_WHITE, bold=True)}"
    )


@app.command()
def reset(
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Required to actually wipe data."),
    ] = False,
) -> None:
    """Wipe the data directory (database, uploads, job outputs, benchmark env).

    When AI_ALMANAC_ENV_ROOT is set to a separate path, the shared environments
    are NOT deleted — only the data directory is removed.
    """
    root = data_root()
    if not confirm:
        typer.secho(
            f"would delete {root} — re-run with --confirm to proceed.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    if root.exists():
        import shutil as _sh

        _sh.rmtree(root)
        typer.echo(f"removed {root}")
    else:
        typer.echo(f"{root} does not exist; nothing to do.")


@app.command()
def version() -> None:
    """Print the installed ai-almanac version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
