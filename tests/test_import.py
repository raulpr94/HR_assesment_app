"""CSV validation and upsert."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from db.repositories import Repositories
from services.container import AppServices
from services.import_service import demo_employees, parse_employees_csv, write_demo_csv
from tests.conftest import SMALL_CSV

HEADER = "employee_id,name,email,level,role_title,department,manager_id,is_hr_admin\n"
HR_ROW = "H1,Hilde,hilde@example.com,C,HR Director,People,,true\n"


def issues_for(body: str) -> list[str]:
    return [str(i) for i in parse_employees_csv(HEADER + HR_ROW + body).issues]


def test_valid_file_has_no_issues() -> None:
    parsed = parse_employees_csv(SMALL_CSV)
    assert parsed.issues == [] and parsed.total_rows == 7


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "C1,Carl,carl@example.com,C,,,H1,false\nC1,Copy,copy@example.com,C,,,H1,false\n",
            "Duplicate employee_id",
        ),
        ("C1,Carl,not-an-email,C,,,H1,false\n", "Invalid email"),
        ("C1,Carl,hilde@example.com,C,,,H1,false\n", "Duplicate email"),
        ("C1,Carl,carl@example.com,C,,,NOPE,false\n", "Manager NOPE does not exist"),
        ("C1,Carl,carl@example.com,X,,,H1,false\n", "level must be A, B or C"),
        ("C1,Carl,carl@example.com,C,,,H1,maybe\n", "is_hr_admin must be true or false"),
        ("C1,Carl,carl@example.com,C,,,C1,false\n", "cannot be their own manager"),
        (",Nobody,nobody@example.com,C,,,H1,false\n", "employee_id is empty"),
        ("C1,,carl@example.com,C,,,H1,false\n", "name is empty"),
        # level consistency
        ("A1,Anna,anna@example.com,A,,,H1,false\n", "Level A must report to level B"),
        (
            "C1,Carl,carl@example.com,C,,,H1,false\nB1,Berta,berta@example.com,B,,,C1,false\n"
            "B2,Bodo,bodo@example.com,B,,,B1,false\n",
            "Level B must report to level C",
        ),
        ("B1,Berta,berta@example.com,B,,,,false\n", "Level B employees must have a manager"),
        (
            "C1,Carl,carl@example.com,C,,,H1,false\nB1,Berta,berta@example.com,B,,,C1,true\n",
            "HR admins must be level C",
        ),
        # reporting cycle: C1 → C2 → C1
        (
            "C1,Carl,carl@example.com,C,,,C2,false\nC2,Cleo,cleo@example.com,C,,,C1,false\n",
            "Reporting cycle",
        ),
    ],
)
def test_validation_errors(body: str, expected: str) -> None:
    found = issues_for(body)
    assert any(expected in issue for issue in found), found


def test_issues_name_row_and_column() -> None:
    issue = parse_employees_csv(HEADER + HR_ROW + "C1,Carl,bad,C,,,H1,false\n").issues[0]
    assert (issue.row, issue.employee_id, issue.field) == (3, "C1", "email")
    assert str(issue).startswith("[row 3, id C1, column email]")


def test_unparseable_manager_is_not_reported_twice() -> None:
    found = issues_for(
        "B1,Berta,berta@example.com,Q,,,H1,false\nA1,Anna,anna@example.com,A,,,B1,false\n"
    )
    assert any("level must be" in i for i in found)
    assert not any("Manager B1 does not exist" in i for i in found)


def test_missing_columns_and_empty_file() -> None:
    assert "Missing column(s): is_hr_admin" in str(
        parse_employees_csv(
            "employee_id,name,email,level,role_title,department,manager_id\n"
        ).issues[0]
    )
    assert "no employees" in str(parse_employees_csv(HEADER).issues[0])


def test_no_hr_admin_is_an_error() -> None:
    text = HEADER + "C1,Carl,carl@example.com,C,,,,false\n"
    assert any("No HR admin" in str(i) for i in parse_employees_csv(text).issues)


def test_semicolon_and_bom_are_accepted(bare_services: AppServices, tmp_path: Path) -> None:
    path = tmp_path / "excel.csv"
    path.write_bytes(("﻿" + SMALL_CSV.replace(",", ";")).encode("utf-8"))
    report = bare_services.imports.import_file(path)
    assert report.ok, report.issues
    assert report.inserted == 7


def test_missing_file_is_a_clear_error(bare_services: AppServices, tmp_path: Path) -> None:
    report = bare_services.imports.import_file(tmp_path / "nope.csv")
    assert not report.ok and "File not found" in str(report.issues[0])


def test_invalid_file_imports_nothing(bare_services: AppServices, tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(SMALL_CSV + "Z1,Zed,bad-email,A,,,B1,false\n", encoding="utf-8")
    report = bare_services.imports.import_file(path)
    assert not report.ok and not report.imported
    with bare_services.sessions() as session:
        assert Repositories.for_session(session).employees.count() == 0


def test_reimport_upserts_and_deactivates(services: AppServices, tmp_path: Path) -> None:
    rows = list(csv.DictReader(SMALL_CSV.splitlines()))
    rows = [r for r in rows if r["employee_id"] != "A3"]  # A3 left the company
    rows[1]["name"] = "Carl Neu"  # C1 renamed
    # A1 and A2 swap email addresses in the same import
    a1 = next(r for r in rows if r["employee_id"] == "A1")
    a2 = next(r for r in rows if r["employee_id"] == "A2")
    a1["email"], a2["email"] = a2["email"], a1["email"]
    rows.append(
        {
            "employee_id": "A4",
            "name": "New Person",
            "email": "ali@example.com",
            "level": "A",
            "role_title": "",
            "department": "Sales",
            "manager_id": "B2",
            "is_hr_admin": "false",
        }
    )  # reuses the leaver's address
    path = tmp_path / "v2.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report = services.imports.import_file(path)
    assert report.ok, report.issues
    assert (report.inserted, report.updated, report.deactivated) == (1, 6, 1)
    with services.sessions() as session:
        repo = Repositories.for_session(session).employees
        assert repo.get("C1").name == "Carl Neu"  # type: ignore[union-attr]
        assert repo.get("A1").email == "arne@example.com"  # type: ignore[union-attr]
        assert repo.get("A3").active is False  # type: ignore[union-attr]
        assert "A3" not in {e.employee_id for e in repo.list()}


def test_demo_data_shape_and_validity(tmp_path: Path) -> None:
    rows = demo_employees()
    levels = [r["level"] for r in rows]
    assert (
        sum(r["is_hr_admin"] == "true" for r in rows),
        levels.count("C"),
        levels.count("B"),
        levels.count("A"),
    ) == (1, 4, 8, 25)  # 4 C = HR + 3 managers
    path = tmp_path / "demo.csv"
    assert write_demo_csv(path) is True
    assert write_demo_csv(path) is False  # never overwrites by default
    assert parse_employees_csv(path.read_text(encoding="utf-8")).issues == []
