"""ai-almanac CLI.

Entry point exposed via `pyproject.toml [project.scripts]` as `ai-almanac`.
"""

from __future__ import annotations

import shutil
import webbrowser
from contextlib import suppress
from typing import Annotated

import typer

from ai_almanac import __version__
from ai_almanac.paths import data_root, ensure_layout

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

    import uvicorn

    url = f"http://{bind if bind != '0.0.0.0' else 'localhost'}:{port}/"
    typer.echo(f"ai-almanac {__version__} serving at {url}")
    typer.echo(f"data dir: {data_root()}")

    if open_browser and not reload:
        with suppress(Exception):
            webbrowser.open(url)

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
    """Install or update the benchmark and blending environments."""
    if shutil.which("pixi") is None:
        typer.secho(
            "pixi is not installed. Install it from https://pixi.sh and re-run.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    from ai_almanac.envs.manager import ensure_env
    from ai_almanac.paths import blending_env_dir

    env_path = ensure_env()
    typer.echo(f"benchmark env ready at {env_path}")
    typer.echo(f"blending env ready at {blending_env_dir()}")


@env_app.command("info")
def env_info() -> None:
    """Show installed package versions in the benchmark environment."""
    from ai_almanac.envs.manager import env_versions

    for name, version in env_versions().items():
        typer.echo(f"{name:20s} {version}")


@app.command()
def reset(
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Required to actually wipe data."),
    ] = False,
) -> None:
    """Wipe the data directory (database, uploads, job outputs, benchmark env)."""
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
