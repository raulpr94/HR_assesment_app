"""manage.py commands: safe to re-run, clear failures."""

from __future__ import annotations

from pathlib import Path

import pytest

import manage


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERF_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("NOTIFY_MODE", "dry_run")
    monkeypatch.setenv("ZAPIER_WEBHOOK_URL", "")
    return tmp_path


def run(*args: str) -> int:
    return manage.main(list(args))


def test_commands_need_an_initialised_database(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("send-notifications") == manage.EXIT_ERROR
    assert "Run `python manage.py init-db` first" in capsys.readouterr().err


def test_full_setup_sequence_is_rerunnable(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = str(cli_env / "employees.csv")
    assert run("init-db") == 0
    assert run("init-db") == 0
    assert "already exists" in capsys.readouterr().err
    assert run("seed-demo", "--file", csv_path) == 0
    assert run("seed-demo", "--file", csv_path) == 0
    err = capsys.readouterr().err
    assert "Keeping existing" in err
    assert "not opening another" in err
    assert run("import-employees", "--file", csv_path, "--validate-only") == 0
    assert run("send-notifications") == 0
    assert run("send-notifications") == 0
    assert "No pending notifications" in capsys.readouterr().err


def test_import_reports_missing_and_invalid_files(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("init-db")
    assert run("import-employees", "--file", str(cli_env / "missing.csv")) == 1
    assert "CSV file not found" in capsys.readouterr().err
    bad = cli_env / "bad.csv"
    bad.write_text("employee_id,name\n1,x\n", encoding="utf-8")
    assert run("import-employees", "--file", str(bad)) == 1
    assert "Missing column(s)" in capsys.readouterr().err


def test_cycles(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run("init-db")
    run("seed-demo", "--file", str(cli_env / "e.csv"), "--no-cycle")
    assert (
        run("open-cycle", "--name", "2026 Annual", "--start", "01.10.2026", "--due", "2026-11-30")
        == 0
    )
    assert run("open-cycle", "--name", "Second") == 1
    assert "Close the open cycle" in capsys.readouterr().err
    assert run("close-cycle") == 0
    assert run("close-cycle") == 0  # nothing open: fine
    assert run("open-cycle", "--name", "2026 Annual") == 1  # name taken


def test_reset_db_requires_yes(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run("init-db")
    assert run("reset-db") == manage.EXIT_USAGE
    assert "--yes" in capsys.readouterr().err
    assert (cli_env / "cli.db").exists()
    assert run("reset-db", "--yes") == 0
    assert (cli_env / "cli.db").exists()


def test_bad_config_is_reported(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NOTIFY_MODE", "pigeon")
    assert run("init-db") == manage.EXIT_USAGE
    assert "Configuration problem" in capsys.readouterr().err
