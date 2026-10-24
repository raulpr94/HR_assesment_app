"""CSV import of the employee hierarchy, and demo data generation."""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from core.errors import DomainError, ErrorCode
from core.hierarchy import validate_hierarchy
from core.models import AuditEntry, Employee, Level, ValidationIssue
from db.repositories import Repositories
from services.clock import Clock

log = logging.getLogger(__name__)

COLUMNS = (
    "employee_id",
    "name",
    "email",
    "level",
    "role_title",
    "department",
    "manager_id",
    "is_hr_admin",
)
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")
TRUE_VALUES = {"true", "1", "yes", "y", "ja", "x"}
FALSE_VALUES = {"false", "0", "no", "n", "nein", ""}


@dataclass
class ImportReport:
    file: Path
    issues: list[ValidationIssue] = field(default_factory=list)
    total_rows: int = 0
    inserted: int = 0
    updated: int = 0
    deactivated: int = 0
    imported: bool = False

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        if not self.ok:
            return f"{len(self.issues)} problem(s) in {self.file.name}; nothing was imported."
        return (
            f"Imported {self.total_rows} employees from {self.file.name}: "
            f"{self.inserted} new, {self.updated} updated, {self.deactivated} deactivated."
        )


@dataclass
class ParsedCsv:
    employees: list[Employee] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)
    unparsed_ids: set[str] = field(default_factory=set)
    total_rows: int = 0


def _detect_delimiter(text: str) -> str:
    """Comma by default; semicolon for files saved by German Excel."""
    header = text.splitlines()[0] if text else ""
    return ";" if header.count(";") > header.count(",") else ","


def parse_employees_csv(text: str) -> ParsedCsv:
    """Parse and validate CSV text. Collects every problem instead of stopping at the first."""
    result = ParsedCsv()
    reader = csv.DictReader(io.StringIO(text), delimiter=_detect_delimiter(text))
    header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        result.issues.append(
            ValidationIssue(
                f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(COLUMNS)}"
            )
        )
        return result
    reader.fieldnames = header

    seen_ids: dict[str, int] = {}
    seen_emails: dict[str, int] = {}
    for line_no, raw in enumerate(reader, start=2):  # row 1 is the header
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        result.total_rows += 1

        def problem(message: str, column: str, _line: int = line_no, _row: dict = row) -> None:
            result.issues.append(
                ValidationIssue(message, _line, _row.get("employee_id") or None, column)
            )

        emp_id = row["employee_id"]
        if not emp_id:
            problem("employee_id is empty", "employee_id")
        elif emp_id in seen_ids:
            problem(f"Duplicate employee_id (first seen in row {seen_ids[emp_id]})", "employee_id")
        else:
            seen_ids[emp_id] = line_no

        if not row["name"]:
            problem("name is empty", "name")

        email = row["email"].lower()
        if not EMAIL_RE.match(email):
            problem(f"Invalid email address {row['email']!r}", "email")
        elif email in seen_emails:
            problem(f"Duplicate email (first seen in row {seen_emails[email]})", "email")
        else:
            seen_emails[email] = line_no

        level_raw = row["level"].upper()
        level = Level(level_raw) if level_raw in Level.__members__ else None
        if level is None:
            problem(f"level must be A, B or C (got {row['level']!r})", "level")

        hr_raw = row["is_hr_admin"].lower()
        if hr_raw not in TRUE_VALUES | FALSE_VALUES:
            problem(
                f"is_hr_admin must be true or false (got {row['is_hr_admin']!r})", "is_hr_admin"
            )

        if emp_id and seen_ids.get(emp_id) == line_no and level is not None:
            result.employees.append(
                Employee(
                    employee_id=emp_id,
                    name=row["name"],
                    email=email,
                    level=level,
                    role_title=row["role_title"],
                    department=row["department"],
                    manager_id=row["manager_id"] or None,
                    is_hr_admin=hr_raw in TRUE_VALUES,
                )
            )
            result.rows[emp_id] = line_no
        elif emp_id:
            result.unparsed_ids.add(emp_id)

    if result.total_rows == 0:
        result.issues.append(ValidationIssue("The file contains no employees"))
        return result
    result.issues.extend(
        validate_hierarchy(result.employees, result.rows, known_ids=result.unparsed_ids)
    )
    result.issues.sort(key=lambda i: (i.row or 0, i.field or ""))
    return result


def decode_csv(raw: bytes) -> str:
    """Decode CSV bytes as UTF-8 (with or without BOM), falling back to Windows-1252."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def read_csv_text(path: Path) -> str:
    return decode_csv(path.read_bytes())


class ImportService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock) -> None:
        self._sessions = session_factory
        self.clock = clock

    # ---- validation (writes nothing) ---------------------------------------------------

    def validate_bytes(self, raw: bytes, name: str) -> tuple[ParsedCsv, ImportReport]:
        report = ImportReport(file=Path(name))
        parsed = parse_employees_csv(decode_csv(raw))
        report.issues = parsed.issues
        report.total_rows = parsed.total_rows
        return parsed, report

    def validate_file(self, path: Path) -> tuple[ParsedCsv, ImportReport]:
        if not path.exists():
            return ParsedCsv(), ImportReport(path, [ValidationIssue(f"File not found: {path}")])
        if path.is_dir():
            message = f"{path} is a folder, not a CSV file"
            return ParsedCsv(), ImportReport(path, [ValidationIssue(message)])
        parsed, report = self.validate_bytes(path.read_bytes(), str(path))
        return parsed, report

    # ---- import (all-or-nothing) -------------------------------------------------------

    def import_file(self, path: Path, actor_id: str | None = None) -> ImportReport:
        """Validate, then upsert all rows in one transaction (all-or-nothing).

        Employees no longer in the file are deactivated, not deleted, because existing
        reviews refer to them.
        """
        parsed, report = self.validate_file(path)
        return self._import(parsed, report, actor_id)

    def import_bytes(self, raw: bytes, name: str, actor_id: str | None = None) -> ImportReport:
        """Same as import_file, for an uploaded file. A given actor must be an HR admin."""
        parsed, report = self.validate_bytes(raw, name)
        return self._import(parsed, report, actor_id)

    def _import(
        self, parsed: ParsedCsv, report: ImportReport, actor_id: str | None
    ) -> ImportReport:
        if not report.ok:
            return report
        now = self.clock.now()
        with self._sessions.begin() as session:
            repos = Repositories.for_session(session)
            if actor_id is not None:
                actor = repos.employees.get(actor_id)
                if actor is None or not actor.is_hr_admin:
                    raise DomainError(ErrorCode.HR_ONLY)
            report.inserted, report.updated = repos.employees.upsert_many(parsed.employees, now)
            report.deactivated = repos.employees.deactivate_missing(
                [e.employee_id for e in parsed.employees], now
            )
            repos.audit.add(
                AuditEntry(
                    action="import_employees",
                    field_changed="employees",
                    new_value=(
                        f"{report.file.name}: {report.inserted} new, {report.updated} updated, "
                        f"{report.deactivated} deactivated"
                    ),
                ),
                actor_id=actor_id or "system",
                review_id=None,
                now=now,
            )
        report.imported = True
        log.debug(report.summary())
        return report


# ---- demo data ---------------------------------------------------------------------

_FIRST = [
    "Anna",
    "Ben",
    "Clara",
    "David",
    "Elif",
    "Felix",
    "Greta",
    "Hannes",
    "Ines",
    "Jan",
    "Katrin",
    "Lukas",
    "Mira",
    "Noah",
    "Olga",
    "Paul",
    "Rosa",
    "Samir",
    "Tanja",
    "Umut",
    "Vera",
    "Wim",
    "Yara",
    "Zoe",
    "Arne",
    "Bianca",
    "Carlos",
    "Dana",
    "Emil",
    "Farah",
    "Gustav",
    "Hanna",
    "Igor",
]
_LAST = [
    "Becker",
    "Schmidt",
    "Novak",
    "Okafor",
    "Yilmaz",
    "Hansen",
    "Rossi",
    "Fischer",
    "Kowalski",
    "Weber",
    "Nguyen",
    "Wagner",
    "Petrov",
    "Kaya",
    "Lindqvist",
    "Hoffmann",
    "Costa",
    "Braun",
    "Ivanova",
    "Klein",
    "Mueller",
    "Sato",
    "Wolf",
    "Ozturk",
    "Lang",
    "Berger",
    "Santos",
    "Frank",
    "Haas",
    "Morales",
    "Engel",
    "Jung",
    "Seidel",
]
_DEPARTMENTS = [
    ("C001", "Martin Keller", "Head of Sales", "Sales", ["B001", "B002", "B003"]),
    ("C002", "Aylin Demir", "Head of Engineering", "Engineering", ["B004", "B005", "B006"]),
    ("C003", "Jonas Richter", "Head of Operations", "Operations", ["B007", "B008"]),
]
_TEAM_SIZES = [4, 3, 3, 3, 3, 3, 3, 3]  # 25 level-A employees across the 8 B managers


def _email(name: str) -> str:
    return name.lower().replace(" ", ".") + "@example.com"


def demo_employees() -> list[dict[str, str]]:
    """1 HR Director, 3 C, 8 B and 25 A employees with consistent reporting lines."""
    rows: list[dict[str, str]] = [
        {
            "employee_id": "H001",
            "name": "Helena Vogt",
            "email": _email("Helena Vogt"),
            "level": "C",
            "role_title": "HR Director",
            "department": "People & Culture",
            "manager_id": "",
            "is_hr_admin": "true",
        }
    ]
    people = iter(f"{f} {last}" for f, last in zip(_FIRST, _LAST, strict=True))
    b_index = 0
    for c_id, c_name, c_title, dept, b_ids in _DEPARTMENTS:
        rows.append(
            {
                "employee_id": c_id,
                "name": c_name,
                "email": _email(c_name),
                "level": "C",
                "role_title": c_title,
                "department": dept,
                "manager_id": "H001",
                "is_hr_admin": "false",
            }
        )
        for b_id in b_ids:
            b_name = next(people)
            rows.append(
                {
                    "employee_id": b_id,
                    "name": b_name,
                    "email": _email(b_name),
                    "level": "B",
                    "role_title": f"Team Lead {dept}",
                    "department": dept,
                    "manager_id": c_id,
                    "is_hr_admin": "false",
                }
            )
            for _ in range(_TEAM_SIZES[b_index]):
                a_name = next(people)
                a_id = f"A{sum(1 for r in rows if r['level'] == 'A') + 1:03d}"
                rows.append(
                    {
                        "employee_id": a_id,
                        "name": a_name,
                        "email": _email(a_name),
                        "level": "A",
                        "role_title": f"{dept} Specialist",
                        "department": dept,
                        "manager_id": b_id,
                        "is_hr_admin": "false",
                    }
                )
            b_index += 1
    return rows


def write_demo_csv(path: Path, *, overwrite: bool = False) -> bool:
    """Write the demo CSV. Returns False (and leaves the file alone) if it exists."""
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(demo_employees())
    return True
