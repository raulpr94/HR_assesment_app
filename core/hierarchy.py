"""Reporting-line helpers and hierarchy validation. Pure Python."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from core.models import Employee, Level, ValidationIssue

# Who may manage whom (spec §2). Assumption: C-level managers report to another C
# (the HR Director) or to nobody; only C-level employees may have no manager.
ALLOWED_MANAGER_LEVEL: dict[Level, frozenset[Level]] = {
    Level.A: frozenset({Level.B}),
    Level.B: frozenset({Level.C}),
    Level.C: frozenset({Level.C}),
}


def children_map(employees: Iterable[Employee]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for emp in employees:
        if emp.manager_id:
            children.setdefault(emp.manager_id, []).append(emp.employee_id)
    return children


def descendants(children: Mapping[str, Sequence[str]], root_id: str) -> set[str]:
    """Everyone below ``root_id`` in the reporting line (not including root)."""
    found: set[str] = set()
    stack = list(children.get(root_id, ()))
    while stack:
        current = stack.pop()
        if current in found or current == root_id:
            continue
        found.add(current)
        stack.extend(children.get(current, ()))
    return found


def find_cycles(managers: Mapping[str, str | None]) -> list[list[str]]:
    """All reporting cycles, each as a list of ids (e.g. ['a', 'b', 'a'])."""
    cycles: list[list[str]] = []
    state: dict[str, int] = {}  # 1 = on current path, 2 = done
    for start in managers:
        if state.get(start) == 2:
            continue
        path: list[str] = []
        node: str | None = start
        while node is not None and node in managers and state.get(node) != 2:
            if state.get(node) == 1:
                cycle = path[path.index(node) :] + [node]
                cycles.append(cycle)
                break
            state[node] = 1
            path.append(node)
            node = managers.get(node)
        for visited in path:
            state[visited] = 2
    return cycles


def validate_hierarchy(
    employees: Sequence[Employee],
    rows: Mapping[str, int] | None = None,
    known_ids: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Check manager ids exist, no cycles, level consistency and an HR admin exists.

    ``rows`` maps employee_id to its row number in the source file, for messages.
    ``known_ids`` are ids present in the file but unparseable (already reported), so
    their reports don't get a second, misleading "manager does not exist" error.
    Assumes ids are already unique (checked while parsing).
    """
    rows = rows or {}
    known = set(known_ids)
    by_id = {e.employee_id: e for e in employees}
    issues: list[ValidationIssue] = []

    def issue(emp: Employee, message: str, column: str | None = None) -> None:
        issues.append(ValidationIssue(message, rows.get(emp.employee_id), emp.employee_id, column))

    for emp in employees:
        if emp.manager_id is None:
            if emp.level is not Level.C:
                issue(emp, f"Level {emp.level.value} employees must have a manager", "manager_id")
            continue
        if emp.manager_id == emp.employee_id:
            issue(emp, "An employee cannot be their own manager", "manager_id")
            continue
        manager = by_id.get(emp.manager_id)
        if manager is None:
            if emp.manager_id not in known:
                issue(emp, f"Manager {emp.manager_id} does not exist", "manager_id")
            continue
        allowed = ALLOWED_MANAGER_LEVEL[emp.level]
        if manager.level not in allowed:
            expected = "/".join(sorted(level.value for level in allowed))
            issue(
                emp,
                f"Level {emp.level.value} must report to level {expected}, "
                f"but manager {manager.employee_id} is level {manager.level.value}",
                "level",
            )

    managers = {e.employee_id: e.manager_id for e in employees if e.manager_id != e.employee_id}
    for cycle in find_cycles(managers):
        first = by_id[cycle[0]]
        issue(first, "Reporting cycle: " + " → ".join(cycle), "manager_id")

    for emp in employees:
        if emp.is_hr_admin and emp.level is not Level.C:
            issue(emp, "HR admins must be level C", "is_hr_admin")
    if employees and not any(e.is_hr_admin for e in employees):
        issues.append(ValidationIssue("No HR admin found (set is_hr_admin=true for one person)"))
    return issues
