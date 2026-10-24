"""Repositories for survey answers and review comments."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from core.models import Answer, RespondentRole
from db.schema import review_comments, survey_answers


class AnswerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, review_id: int, role: RespondentRole) -> dict[str, Answer]:
        rows = self.session.execute(
            select(survey_answers).where(
                survey_answers.c.review_id == review_id,
                survey_answers.c.respondent_role == role.value,
            )
        ).mappings()
        return {
            r["question_id"]: Answer(
                question_id=r["question_id"],
                value=r["value"],
                is_draft=bool(r["is_draft"]),
                submitted_at=r["submitted_at"],
            )
            for r in rows
        }

    def values(self, review_id: int, role: RespondentRole) -> dict[str, str | None]:
        return {qid: a.value for qid, a in self.get(review_id, role).items()}

    def is_locked(self, review_id: int, role: RespondentRole) -> bool:
        return any(not a.is_draft for a in self.get(review_id, role).values())

    def save_drafts(
        self,
        review_id: int,
        role: RespondentRole,
        values: Mapping[str, str | None],
        now: datetime,
    ) -> None:
        """Upsert draft answers. Refuses to touch submitted (locked) answers."""
        existing = self.get(review_id, role)
        if any(not a.is_draft for a in existing.values()):
            raise PermissionError(f"answers for review {review_id} ({role.value}) are locked")
        for question_id, value in values.items():
            if question_id in existing:
                self.session.execute(
                    update(survey_answers)
                    .where(
                        survey_answers.c.review_id == review_id,
                        survey_answers.c.respondent_role == role.value,
                        survey_answers.c.question_id == question_id,
                    )
                    .values(value=value, updated_at=now)
                )
            else:
                self.session.execute(
                    insert(survey_answers).values(
                        review_id=review_id,
                        respondent_role=role.value,
                        question_id=question_id,
                        value=value,
                        is_draft=True,
                        updated_at=now,
                    )
                )

    def lock(self, review_id: int, role: RespondentRole, now: datetime) -> None:
        self.session.execute(
            update(survey_answers)
            .where(
                survey_answers.c.review_id == review_id,
                survey_answers.c.respondent_role == role.value,
            )
            .values(is_draft=False, submitted_at=now)
        )

    def unlock(self, review_id: int, role: RespondentRole) -> None:
        self.session.execute(
            update(survey_answers)
            .where(
                survey_answers.c.review_id == review_id,
                survey_answers.c.respondent_role == role.value,
            )
            .values(is_draft=True, submitted_at=None)
        )

    def submitted_for(
        self, review_ids: list[int]
    ) -> list[tuple[int, RespondentRole, str, str | None]]:
        """Submitted (locked) answers of many reviews: (review_id, role, question_id, value)."""
        if not review_ids:
            return []
        rows = self.session.execute(
            select(
                survey_answers.c.review_id,
                survey_answers.c.respondent_role,
                survey_answers.c.question_id,
                survey_answers.c.value,
            ).where(
                survey_answers.c.review_id.in_(review_ids),
                survey_answers.c.is_draft.is_(False),
            )
        ).all()
        return [(r, RespondentRole(role), q, v) for r, role, q, v in rows]


class CommentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, review_id: int, employee_id: str, comment: str, now: datetime) -> None:
        self.session.execute(
            insert(review_comments).values(
                review_id=review_id, employee_id=employee_id, comment=comment, created_at=now
            )
        )

    def list_for_review(self, review_id: int) -> list[dict[str, object]]:
        rows = self.session.execute(
            select(review_comments)
            .where(review_comments.c.review_id == review_id)
            .order_by(review_comments.c.created_at)
        ).mappings()
        return [dict(r) for r in rows]

    def delete_for_review(self, review_id: int) -> None:
        self.session.execute(
            delete(review_comments).where(review_comments.c.review_id == review_id)
        )
