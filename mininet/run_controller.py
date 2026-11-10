"""Minimal os-ken manager shim -- the Track B replacement for ``ryu-manager``.

Ryu 4.34 does not build on Python 3.12 (it imports the removed
``distutils`` module). os-ken is the OpenStack Neutron fork of Ryu with
the same module layout and event API, but it does not ship a
``ryu-manager`` CLI. This file is the ~30-line equivalent: it just
forwards into ``os_ken.base.app_manager.AppManager.run_apps``.

Usage:

    cd /home/mcshekar/workbench/whitepaper
    MODE=static     .venv-ryu/bin/python mininet/run_controller.py \
        --observe-links mininet/ryu_app.py
    MODE=predictive .venv-ryu/bin/python mininet/run_controller.py \
        --observe-links mininet/ryu_app.py

``--observe-links`` adds ``os_ken.topology.switches`` to the app list so
that the ``EventSwitchEnter`` topology events fire and
``get_switch`` / ``get_link`` return useful data inside ``ryu_app.py``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_controller.py",
        description="Minimal os-ken manager shim (replacement for ryu-manager).",
    )
    p.add_argument(
        "--observe-links", action="store_true",
        help="Load os_ken.topology.switches so EventSwitchEnter fires "
             "and get_link/get_switch return data.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG logging.",
    )
    p.add_argument(
        "apps", nargs="+",
        help="One or more os-ken apps to load. May be either Python "
             "module paths (e.g. mininet.ryu_app) or file paths (e.g. "
             "mininet/ryu_app.py). Mixing is fine.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # AppManager.run_apps() drives os-ken's own cfg initialisation; we
    # only need to set up basic logging here. The controller binds to
    # the os-ken default port 6633, which matches mininet/topology.py's
    # RemoteController.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )

    # Build the app list. The topology service is needed for
    # --observe-links to make EventSwitchEnter useful.
    app_lists: list[str] = list(args.apps)
    if args.observe_links:
        app_lists.append("os_ken.topology.switches")

    # Resolve file-path arguments to be relative to the project root so
    # `python mininet/run_controller.py mininet/ryu_app.py` works from
    # any cwd inside the project (typical: workspace root).
    project_root = Path(__file__).resolve().parent.parent
    resolved: list[str] = []
    for app in app_lists:
        if app.endswith(".py"):
            p = Path(app)
            if not p.is_absolute():
                p = (Path.cwd() / app).resolve()
            if not p.exists():
                # Fall back to project-root relative
                alt = (project_root / app).resolve()
                if alt.exists():
                    p = alt
            resolved.append(str(p))
        else:
            resolved.append(app)

    logging.getLogger().info("Loading apps: %s", resolved)

    from os_ken.base.app_manager import AppManager
    AppManager.run_apps(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
