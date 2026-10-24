"""Repository classes: the only code that talks to the database."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from db.repositories.answers import AnswerRepository, CommentRepository
from db.repositories.audit import AuditRepository
from db.repositories.cycles import CycleRepository
from db.repositories.employees import EmployeeRepository
from db.repositories.outbox import OutboxRepository
from db.repositories.progress import ProgressRepository


@dataclass(frozen=True)
class Repositories:
    """All repositories bound to one session, i.e. one transaction."""

    session: Session
    employees: EmployeeRepository
    cycles: CycleRepository
    progress: ProgressRepository
    answers: AnswerRepository
    comments: CommentRepository
    audit: AuditRepository
    outbox: OutboxRepository

    @classmethod
    def for_session(cls, session: Session) -> Repositories:
        return cls(
            session=session,
            employees=EmployeeRepository(session),
            cycles=CycleRepository(session),
            progress=ProgressRepository(session),
            answers=AnswerRepository(session),
            comments=CommentRepository(session),
            audit=AuditRepository(session),
            outbox=OutboxRepository(session),
        )


__all__ = [
    "AnswerRepository",
    "AuditRepository",
    "CommentRepository",
    "CycleRepository",
    "EmployeeRepository",
    "OutboxRepository",
    "ProgressRepository",
    "Repositories",
]
