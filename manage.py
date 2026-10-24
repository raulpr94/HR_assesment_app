#!/usr/bin/env python
"""All setup and maintenance tasks in one CLI.

    python manage.py init-db
    python manage.py import-employees --file data/employees.csv
    python manage.py seed-demo
    python manage.py open-cycle --name "2026 Annual"
    python manage.py close-cycle
    python manage.py reset-db --yes
    python manage.py send-notifications
    python manage.py run

Every command is safe to re-run and exits non-zero with a clear message on failure.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from config.settings import (
    DEFAULT_EMPLOYEES_CSV,
    PROJECT_ROOT,
    ConfigError,
    Settings,
    load_settings,
)
from core.errors import DomainError
from db.engine import create_schema, make_engine
from services.container import AppServices, build_services
from services.import_service import ImportReport, write_demo_csv

log = logging.getLogger("manage")

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


class CommandError(Exception):
    """A user-facing failure: the message is shown as-is."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---- helpers -------------------------------------------------------------------------


def _parse_date(value: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"{value!r} is not a date (use DD.MM.YYYY or YYYY-MM-DD)")


def _services(settings: Settings) -> AppServices:
    """Services for an existing, initialised database."""
    if not settings.db_path.exists():
        raise CommandError(
            f"Database not found at {settings.db_path}.\n"
            "Run `python manage.py init-db` first (the setup script does this for you)."
        )
    services = build_services(settings)
    if not services.schema_ready():
        raise CommandError(
            f"Database at {settings.db_path} is missing tables. Run `python manage.py init-db`."
        )
    return services


def _report_import(report: ImportReport) -> int:
    if report.ok:
        log.info(report.summary())
        return EXIT_OK
    log.error(report.summary())
    for issue in report.issues:
        log.error("  - %s", issue)
    return EXIT_ERROR


# ---- commands ------------------------------------------------------------------------


def cmd_init_db(args: argparse.Namespace, settings: Settings) -> int:
    existed = settings.db_path.exists()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    created = create_schema(engine)
    engine.dispose()
    if not existed:
        log.info("Created database %s with %d tables.", settings.db_path, len(created))
    elif created:
        log.info(
            "Database %s existed; added missing tables: %s", settings.db_path, ", ".join(created)
        )
    else:
        log.info("Database %s already exists and is up to date. Nothing to do.", settings.db_path)
    return EXIT_OK


def cmd_import_employees(args: argparse.Namespace, settings: Settings) -> int:
    path = Path(args.file)
    if not path.exists():
        raise CommandError(
            f"CSV file not found: {path}\n"
            "Create it with the columns employee_id, name, email, level, role_title, "
            "department, manager_id, is_hr_admin, or run `python manage.py seed-demo`."
        )
    services = _services(settings)
    if args.validate_only:
        _, report = services.imports.validate_file(path)
        if report.ok:
            log.info("%s is valid (%d employees). Nothing was imported.", path, report.total_rows)
            return EXIT_OK
        return _report_import(report)
    return _report_import(services.imports.import_file(path))


def cmd_seed_demo(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    path = Path(args.file)
    if write_demo_csv(path, overwrite=args.force):
        log.info("Wrote demo hierarchy to %s (1 HR, 3 C, 8 B, 25 A).", path)
    else:
        log.info("Keeping existing %s (use --force to overwrite it with demo data).", path)
    code = _report_import(services.imports.import_file(path))
    if code != EXIT_OK or args.no_cycle:
        return code
    # Assumption: the demo is only useful with a cycle, so seed-demo opens one when the
    # database has no cycle at all. Re-running never opens a second one.
    if services.cycles.list_cycles():
        log.info("A review cycle already exists; not opening another.")
    else:
        cycle, queued = services.cycles.open_cycle(f"Demo cycle {services.cycles.today().year}")
        log.info("Opened demo cycle %r (%d notifications queued).", cycle.name, queued)
    return EXIT_OK


def cmd_open_cycle(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    cycle, queued = services.cycles.open_cycle(args.name, args.start, args.due, actor_id=args.actor)
    log.info(
        "Opened cycle %r (%s – %s). Queued %d notification(s) for C-level managers.",
        cycle.name,
        cycle.start_date.strftime("%d.%m.%Y"),
        cycle.due_date.strftime("%d.%m.%Y"),
        queued,
    )
    return EXIT_OK


def cmd_close_cycle(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    cycle_id = args.id
    if cycle_id is None:
        current = services.cycles.get_open_cycle()
        if current is None:
            log.info("No open cycle. Nothing to do.")
            return EXIT_OK
        cycle_id = current.cycle_id
    cycle = services.cycles.close_cycle(cycle_id, actor_id=args.actor)
    log.info("Closed cycle %r.", cycle.name)
    return EXIT_OK


def cmd_reset_db(args: argparse.Namespace, settings: Settings) -> int:
    if not args.yes:
        raise CommandError(
            f"This deletes {settings.db_path} and every review in it.\n"
            "Re-run with --yes to confirm: python manage.py reset-db --yes",
            EXIT_USAGE,
        )
    for suffix in ("", "-wal", "-shm", "-journal"):
        target = Path(f"{settings.db_path}{suffix}")
        try:
            target.unlink(missing_ok=True)
        except PermissionError as exc:
            raise CommandError(
                f"Could not delete {target}: it is in use. Stop the running app and try again."
            ) from exc
    log.info("Deleted %s.", settings.db_path)
    return cmd_init_db(args, settings)


def cmd_send_notifications(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    report = services.send_notifications(limit=args.limit, retry_failed=args.retry_failed)
    if report.processed == 0:
        log.info("No pending notifications.")
        return EXIT_OK
    counts = ", ".join(f"{n} {status}" for status, n in sorted(report.counts.items()))
    log.info(
        "Processed %d notification(s) in %s mode: %s.",
        report.processed,
        settings.notify_mode.value,
        counts,
    )
    return EXIT_ERROR if report.counts.get("failed") else EXIT_OK


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    _services(settings)  # fail early with a clear message if the DB isn't set up
    command = [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app.py"), *args.extra]
    log.info("Starting the app: %s", " ".join(command))
    try:
        return subprocess.call(command, cwd=PROJECT_ROOT)
    except KeyboardInterrupt:
        return EXIT_OK


# ---- argument parsing ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py", description="Performance Review Assistant: setup and maintenance."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("init-db", help="create the database and tables (idempotent)")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("import-employees", help="validate and upsert the employee CSV")
    p.add_argument("--file", default=str(DEFAULT_EMPLOYEES_CSV), help="CSV path")
    p.add_argument("--validate-only", action="store_true", help="check the file, write nothing")
    p.set_defaults(func=cmd_import_employees)

    p = sub.add_parser("seed-demo", help="generate a demo employees.csv and import it")
    p.add_argument("--file", default=str(DEFAULT_EMPLOYEES_CSV), help="CSV path")
    p.add_argument("--force", action="store_true", help="overwrite an existing CSV")
    p.add_argument("--no-cycle", action="store_true", help="don't open a demo review cycle")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("open-cycle", help="open a review cycle and notify C-level managers")
    p.add_argument("--name", required=True, help='e.g. "2026 Annual"')
    p.add_argument("--start", type=_parse_date, help="start date (default: today)")
    p.add_argument("--due", type=_parse_date, help="due date (default: start + 6 weeks)")
    p.add_argument("--actor", help="employee id of the HR admin opening it (optional)")
    p.set_defaults(func=cmd_open_cycle)

    p = sub.add_parser("close-cycle", help="close the open review cycle")
    p.add_argument("--id", type=int, help="cycle id (default: the open cycle)")
    p.add_argument("--actor", help="employee id of the HR admin closing it (optional)")
    p.set_defaults(func=cmd_close_cycle)

    p = sub.add_parser("reset-db", help="delete and recreate the database (needs --yes)")
    p.add_argument("--yes", action="store_true", help="confirm deleting all data")
    p.set_defaults(func=cmd_reset_db)

    p = sub.add_parser("send-notifications", help="process the notification outbox")
    p.add_argument("--limit", type=int, default=100, help="max messages per run")
    p.add_argument("--retry-failed", action="store_true", help="re-queue failed messages first")
    p.set_defaults(func=cmd_send_notifications)

    p = sub.add_parser("run", help="start the Streamlit app (extra options go to streamlit)")
    p.set_defaults(func=cmd_run, extra=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        if args.command != "run":
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        args.extra = unknown  # e.g. run --server.port 8502
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s" if not args.verbose else "%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    try:
        settings = load_settings(use_streamlit_secrets=False)
        return int(args.func(args, settings))
    except ConfigError as exc:
        log.error("Configuration problem: %s\nCheck your .env file (see .env.example).", exc)
        return EXIT_USAGE
    except CommandError as exc:
        log.error("%s", exc)
        return exc.exit_code
    except DomainError as exc:
        log.error("%s", exc)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
