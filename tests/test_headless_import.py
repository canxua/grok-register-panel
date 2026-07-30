# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_server_runtime_imports_without_starting_gui():
    import grok_register_ttk as panel

    assert callable(panel.load_config)
    assert callable(panel.run_registration_cli)
    assert isinstance(panel.config, dict)


if __name__ == "__main__":
    test_server_runtime_imports_without_starting_gui()
    print("OK headless import")
