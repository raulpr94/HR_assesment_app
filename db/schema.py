"""Table definitions (SQLAlchemy Core). All datetimes are naive UTC."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

employees = Table(
    "employees",
    metadata,
    Column("employee_id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("email", String(320), nullable=False, unique=True),
    Column("level", String(1), nullable=False),
    Column("role_title", String(200), nullable=False, default=""),
    Column("department", String(200), nullable=False, default=""),
    # Not a FK: the import validates managers itself and upserts in any order.
    Column("manager_id", String(64), nullable=True, index=True),
    Column("is_hr_admin", Boolean, nullable=False, default=False),
    # Employees missing from a re-import are deactivated, never deleted (reviews refer to them).
    Column("active", Boolean, nullable=False, default=True),
    Column("updated_at", DateTime, nullable=True),
)

review_cycles = Table(
    "review_cycles",
    metadata,
    Column("cycle_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False, unique=True),
    Column("start_date", Date, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_by", String(64), nullable=True),
    Column("created_at", DateTime, nullable=True),
    Column("closed_at", DateTime, nullable=True),
)

# The writeback table. Starts empty; a row appears only when a review is initiated.
# Status is NOT stored: core.workflow.derive_status() computes it from these columns.
review_progress = Table(
    "review_progress",
    metadata,
    Column("review_id", Integer, primary_key=True, autoincrement=True),
    Column("cycle_id", Integer, ForeignKey("review_cycles.cycle_id"), nullable=False),
    Column("reviewer_id", String(64), ForeignKey("employees.employee_id"), nullable=False),
    Column("reviewee_id", String(64), ForeignKey("employees.employee_id"), nullable=False),
    # Extra column (spec §4): survey version used, so old answers stay interpretable.
    Column("survey_version", String(32), nullable=False, default=""),
    Column("initiated_at", DateTime, nullable=True),
    Column("initiated_by", String(64), nullable=True),
    Column("self_submitted_at", DateTime, nullable=True),
    Column("manager_submitted_at", DateTime, nullable=True),
    Column("meeting_at", DateTime, nullable=True),
    Column("meeting_set_by", String(64), nullable=True),
    Column("reviewee_ack_at", DateTime, nullable=True),
    Column("reviewer_ack_at", DateTime, nullable=True),
    Column("completed_at", DateTime, nullable=True),
    Column("updated_at", DateTime, nullable=True),
    Column("updated_by", String(64), nullable=True),
    UniqueConstraint("cycle_id", "reviewee_id", name="uq_progress_cycle_reviewee"),
    Index("ix_progress_cycle_reviewer", "cycle_id", "reviewer_id"),
)

survey_answers = Table(
    "survey_answers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "review_id",
        Integer,
        ForeignKey("review_progress.review_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("respondent_role", String(16), nullable=False),
    Column("question_id", String(64), nullable=False),
    Column("value", Text, nullable=True),
    Column("is_draft", Boolean, nullable=False, default=True),
    Column("submitted_at", DateTime, nullable=True),
    Column("updated_at", DateTime, nullable=True),
    UniqueConstraint("review_id", "respondent_role", "question_id", name="uq_answer"),
)

review_comments = Table(
    "review_comments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "review_id",
        Integer,
        ForeignKey("review_progress.review_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("employee_id", String(64), nullable=False),
    Column("comment", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

# No FK on review_id: audit entries must survive when a review row is reverted away.
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime, nullable=False, index=True),
    Column("actor_id", String(64), nullable=False),
    Column("review_id", Integer, nullable=True, index=True),
    Column("action", String(64), nullable=False),
    Column("field_changed", String(64), nullable=True),
    Column("old_value", Text, nullable=True),
    Column("new_value", Text, nullable=True),
    Column("reason", Text, nullable=True),
)

notification_outbox = Table(
    "notification_outbox",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(64), nullable=False),
    Column("recipient_email", String(320), nullable=False),
    Column("subject", String(500), nullable=False),
    Column("body", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("status", String(16), nullable=False, index=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("sent_at", DateTime, nullable=True),
)
