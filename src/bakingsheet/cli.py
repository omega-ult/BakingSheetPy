"""Command-line interface: ``bakingsheet convert <config.yaml>``.

Loads a YAML config, bakes the schema from the configured import source, runs
PostLoad (incl. reference validation), and exports JSON to each configured
output directory. Exits non-zero on any error.

By default, relative ``import.path`` / ``outputs[].path`` are resolved against
the config file's directory (so ``bakingsheet convert path/to/bake.yaml`` works
from any cwd), and the config file's directory is added to ``sys.path`` so the
schema module can be imported. Pass ``--root <dir>`` to override the base dir,
or ``--no-chdir`` to resolve paths against the current cwd instead.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import build_exporters, build_importer, load_config, load_container


def _resolve(p: str, root: Path) -> str:
    """Resolve ``p`` against ``root`` if relative, else return as-is."""
    return str(Path(p)) if Path(p).is_absolute() else str((root / p).resolve())


def run(config_path: str, verify: bool = False, root: str | None = None, chdir: bool = True) -> int:
    """Run the full bake -> post_load -> store pipeline from a config file.

    Args:
        config_path: path to the YAML config.
        verify: run asset verifiers.
        root: base directory for relative paths. Defaults to the config file's
            directory when ``chdir`` is True, else the current cwd.
        chdir: when True (default), relative paths resolve against the config
            file's directory and that directory is prepended to ``sys.path``.
    """
    cfg = load_config(config_path)

    if chdir:
        base = Path(root) if root else Path(config_path).resolve().parent
    else:
        base = Path(root) if root else Path.cwd()
    base = base.resolve()

    # make the schema module importable from the config's directory
    base_str = str(base)
    if base_str not in sys.path:
        sys.path.insert(0, base_str)

    # resolve relative paths against base
    if cfg.import_.path:
        cfg.import_.path = _resolve(cfg.import_.path, base)
    if cfg.import_.google and cfg.import_.google.credentials:
        cfg.import_.google.credentials = _resolve(cfg.import_.google.credentials, base)
    for o in cfg.outputs:
        o.path = _resolve(o.path, base)

    container = load_container(cfg)
    importer = build_importer(cfg)
    exporters = build_exporters(cfg)

    if not container.bake(importer):
        for e in container.logger.errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not container.store(exporters):
        for e in container.logger.errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if container.logger.has_error:
        for e in container.logger.errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # report
    for name in container.get_sheet_properties():
        sheet = container.find(name)
        if sheet is None:
            continue
        print(f"Exported sheet '{name}' ({len(sheet)} rows)")
    for o in cfg.outputs:
        print(f"  -> {o.path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bakingsheet", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="Bake sheets from a config and export JSON")
    conv.add_argument("config", help="Path to YAML config file")
    conv.add_argument("--verify", action="store_true", help="Run asset verifiers")
    conv.add_argument(
        "--root",
        default=None,
        help="Base directory for relative paths (default: config file's dir).",
    )
    conv.add_argument(
        "--no-chdir",
        action="store_true",
        help="Resolve relative paths against the current cwd instead of the "
        "config file's directory, and do not modify sys.path.",
    )

    args = parser.parse_args(argv)
    if args.command == "convert":
        return run(args.config, verify=args.verify, root=args.root, chdir=not args.no_chdir)
    return 1


if __name__ == "__main__":
    sys.exit(main())
