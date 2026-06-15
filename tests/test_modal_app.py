"""Modal app smoke test — guards the runner <-> app contract.

The Modal app (`modal/app.py`) is deployed separately and runs on Modal, so it
can't be exercised locally. This only verifies it loads and that the app/function
names the ModalRunner spawns actually exist, so a rename on either side fails
here instead of at deploy time.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ai_almanac.settings import settings

_APP_PATH = Path(__file__).parents[1] / "modal" / "app.py"


def _load_modal_app():
    spec = importlib.util.spec_from_file_location("almanac_modal_app", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_modal_app_matches_runner_configuration() -> None:
    module = _load_modal_app()
    assert module.app.name == settings.modal_app_name
    # The function the ModalRunner spawns must be defined on the app.
    assert hasattr(module, settings.modal_function_name)
