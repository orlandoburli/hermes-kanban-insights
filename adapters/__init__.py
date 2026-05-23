"""
Usage Report — Cost Adapter Protocol

Each adapter is a Python module in the ``adapters/`` directory that
exposes an ``Adapter`` class implementing ``CostAdapter``.

The plugin auto-discovers all adapters, uses the first one whose
``is_available()`` returns True. Users can add new backends by
dropping a new module in this directory.
"""

from __future__ import annotations

import logging
from typing import Optional

from .adapters_base import CostAdapter, CostData

log = logging.getLogger(__name__)


def discover_adapters() -> list[CostAdapter]:
    """Scan the adapters directory and return instantiated adapters."""
    import importlib
    import inspect
    import os
    import sys

    adapters_dir = os.path.dirname(__file__)
    results = []

    # Ensure the base module is importable by loaded adapters
    base_path = os.path.join(adapters_dir, "adapters_base.py")
    if os.path.exists(base_path) and "usage_report_adapter_base" not in sys.modules:
        base_spec = importlib.util.spec_from_file_location(
            "usage_report_adapter_base", base_path
        )
        if base_spec and base_spec.loader:
            base_mod = importlib.util.module_from_spec(base_spec)
            sys.modules["usage_report_adapter_base"] = base_mod
            # Also make it importable as "adapters_base" (the name opencode.py uses)
            sys.modules["adapters_base"] = base_mod
            base_spec.loader.exec_module(base_mod)

    for fname in sorted(os.listdir(adapters_dir)):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        modname = fname[:-3]
        try:
            # Load the module from the adapters directory using its file path
            spec = importlib.util.spec_from_file_location(
                f"usage_report_adapter_{modname}",
                os.path.join(adapters_dir, fname),
            )
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            # Make the parent package referenceable so `from . import` works
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)

            if hasattr(mod, "Adapter"):
                inst = mod.Adapter()
                results.append(inst)
                log.info("Discovered cost adapter: %s (%s)", inst.name, modname)
        except Exception as exc:
            log.warning("Failed to load adapter %s: %s", modname, exc)

    return results


def pick_adapter() -> Optional[CostAdapter]:
    """Return the first available adapter, or None."""
    for a in discover_adapters():
        try:
            if a.is_available():
                log.info("Using cost adapter: %s", a.name)
                return a
        except Exception:
            continue
    log.warning("No cost adapter available")
    return None
