"""Command line: ``netmon run``, ``import``, ``export``, ``set-password``."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from netmon import __version__


def default_data_dir() -> Path:
    """Where the database and log live unless --data-dir / NETMON_DATA_DIR says otherwise."""
    if os.environ.get("NETMON_DATA_DIR"):
        return Path(os.environ["NETMON_DATA_DIR"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "netmon"


def _setup_logging(data_dir: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    logfile = RotatingFileHandler(data_dir / "netmon.log", maxBytes=2_000_000, backupCount=3,
                                  encoding="utf-8")
    logfile.setFormatter(fmt)
    root.addHandler(logfile)
    # httpx logs every request URL at INFO, and webhook/Telegram URLs contain secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _open_db(data_dir: Path):
    from netmon.db import Database

    data_dir.mkdir(parents=True, exist_ok=True)
    return Database(data_dir / "netmon.db")


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    from netmon.api import create_app

    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(data_dir)
    app = create_app(data_dir)

    shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "::") else args.host
    url = f"http://{shown_host}:{args.port}"
    log = logging.getLogger("netmon")
    log.info("Network Health Monitor %s - dashboard at %s (data in %s)", __version__, url,
             data_dir)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.info("Listening on %s: other computers on the network can reach the dashboard",
                 args.host)
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS",
                                                                      "127.0.0.1"))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from netmon.importer import import_monitors

    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    db = _open_db(args.data_dir)
    try:
        result = import_monitors(db, data)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"Imported {result['imported']} monitor(s), skipped {result['skipped']} already present.")
    for error in result["errors"]:
        print(f"  not imported - {error}", file=sys.stderr)
    print("Restart netmon if it is running so the new monitors are picked up.")
    return 1 if result["errors"] else 0


def cmd_export(args: argparse.Namespace) -> int:
    from netmon.importer import export_monitors

    db = _open_db(args.data_dir)
    try:
        text = json.dumps(export_monitors(db), indent=2)
    finally:
        db.close()
    if args.file:
        Path(args.file).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.file}")
    else:
        print(text)
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    from netmon.auth import Auth

    password = getpass.getpass("New admin password: ")
    if len(password) < 8:
        print("The password must be at least 8 characters.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("The passwords do not match.", file=sys.stderr)
        return 1
    db = _open_db(args.data_dir)
    try:
        Auth(db).set_password(password)
    finally:
        db.close()
    print("Admin password updated. Everyone has been signed out.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netmon",
        description="Network Health Monitor - uptime monitoring for small networks.",
    )
    parser.add_argument("--version", action="version", version=f"netmon {__version__}")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(),
                        help="folder for the database and log (default: %(default)s)")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the monitor and dashboard (default)")
    run.add_argument("--host", default=os.environ.get("NETMON_HOST", "127.0.0.1"),
                     help="address to listen on; use 0.0.0.0 to share on the network "
                          "(default: %(default)s)")
    run.add_argument("--port", type=int, default=int(os.environ.get("NETMON_PORT", "8080")),
                     help="port to listen on (default: %(default)s)")
    run.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    run.set_defaults(func=cmd_run)

    imp = sub.add_parser("import", help="add monitors from a netmon export or a v1 config.json")
    imp.add_argument("file")
    imp.set_defaults(func=cmd_import)

    exp = sub.add_parser("export", help="write all monitor definitions as JSON")
    exp.add_argument("file", nargs="?", help="output file (default: print to the screen)")
    exp.set_defaults(func=cmd_export)

    pw = sub.add_parser("set-password", help="set or reset the admin password")
    pw.set_defaults(func=cmd_set_password)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args([*(argv if argv is not None else sys.argv[1:]), "run"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
