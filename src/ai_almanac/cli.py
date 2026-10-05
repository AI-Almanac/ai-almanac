"""ai-almanac CLI.

Entry point exposed via `pyproject.toml [project.scripts]` as `ai-almanac`.
"""

from __future__ import annotations

import os
import webbrowser
from contextlib import suppress
from typing import Annotated

import click
import typer

from ai_almanac import __version__
from ai_almanac.paths import data_root, ensure_layout, env_root, secrets_env_path

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
) -> None:
    """Boot the ai-almanac web server (FastAPI + bundled UI)."""
    from ai_almanac.settings import reload_settings, settings

    reload_settings()
    if settings.deployment_mode != "shared" and bind not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(
            f"refusing to bind to {bind!r}.\n"
            "ai-almanac currently supports local, single-user operation only.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

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
    )


env_app = typer.Typer(help="Manage the benchmark environment (pixi-backed).")
app.add_typer(env_app, name="env")


@env_app.command("prepare")
def env_prepare() -> None:
    """Install or update the benchmark, blending, and forecast environments.

    Also installs the local model server (and its weights) when the assistant
    is set to run on this machine.
    """
    import subprocess

    from ai_almanac.envs.manager import download_llm_model, ensure_env, llm_env_ready
    from ai_almanac.settings import reload_settings, settings

    reload_settings()
    include_llm = settings.llm_engine == "managed"
    try:
        benchmark_dir, blending_dir, forecast_dir = ensure_env(include_llm=include_llm)
        if include_llm and llm_env_ready():
            download_llm_model(settings.llm_model)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"benchmark env ready at {benchmark_dir}")
    typer.echo(f"blending env ready at {blending_dir}")
    if forecast_dir is not None:
        typer.echo(f"forecast env ready at {forecast_dir}")
    if include_llm and llm_env_ready():
        typer.echo(f"local model {settings.llm_model} ready")


@env_app.command("info")
def env_info() -> None:
    """Show installed package versions in the benchmark environment."""
    from ai_almanac.envs.manager import env_versions

    typer.echo(f"env root: {env_root()}")
    for name, version in env_versions().items():
        typer.echo(f"{name:20s} {version}")


llm_app = typer.Typer(help="Manage the local assistant model server (vLLM).")
app.add_typer(llm_app, name="llm")


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
            model_val = (
                DEFAULT_LOCAL_LLM_MODEL
                if yes
                else typer.prompt("Hugging Face model id", default=DEFAULT_LOCAL_LLM_MODEL)
            )
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
