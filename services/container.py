"""Wires settings, storage, clock and services together, in one place."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings
from core.survey import SurveyRegistry
from db.engine import create_schema, make_engine, make_session_factory, schema_is_ready
from services.admin_service import AdminService
from services.clock import Clock, SystemClock
from services.cycle_service import CycleService
from services.dashboard_service import DashboardService
from services.directory_service import DirectoryService
from services.import_service import ImportService
from services.notifications.base import NotificationSender, ProcessReport, process_outbox
from services.notifications.factory import build_sender
from services.notifications.renderer import NotificationQueue, NotificationRenderer
from services.overview_service import OverviewService
from services.review_detail_service import ReviewDetailService
from services.review_service import ReviewService


@dataclass
class AppServices:
    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    clock: Clock
    surveys: SurveyRegistry
    queue: NotificationQueue
    reviews: ReviewService
    cycles: CycleService
    imports: ImportService
    directory: DirectoryService
    overview: OverviewService
    details: ReviewDetailService
    admin: AdminService
    dashboard: DashboardService

    def schema_ready(self) -> bool:
        return schema_is_ready(self.engine)

    def sender(self) -> NotificationSender:
        return build_sender(self.settings)

    def send_notifications(
        self,
        *,
        limit: int = 100,
        retry_failed: bool = False,
        sender: NotificationSender | None = None,
    ) -> ProcessReport:
        return process_outbox(
            self.sessions,
            sender or self.sender(),
            self.clock,
            limit=limit,
            retry_failed=retry_failed,
        )


def build_services(
    settings: Settings,
    *,
    engine: Engine | None = None,
    clock: Clock | None = None,
    ensure_schema: bool = False,
) -> AppServices:
    engine = engine or make_engine(settings.db_url)
    if ensure_schema:
        create_schema(engine)
    sessions = make_session_factory(engine)
    clock = clock or SystemClock()
    surveys = SurveyRegistry.from_paths(settings.survey_path, settings.survey_archive_dir)
    queue = NotificationQueue(NotificationRenderer(settings.templates_dir), settings)
    return AppServices(
        settings=settings,
        engine=engine,
        sessions=sessions,
        clock=clock,
        surveys=surveys,
        queue=queue,
        reviews=ReviewService(sessions, surveys, queue, clock, settings),
        cycles=CycleService(sessions, queue, clock, settings),
        imports=ImportService(sessions, clock),
        directory=DirectoryService(sessions),
        overview=OverviewService(sessions, clock, settings),
        details=ReviewDetailService(sessions, surveys, clock, settings),
        dashboard=DashboardService(sessions, surveys, clock, settings),
        admin=AdminService(sessions, clock, settings, lambda: build_sender(settings)),
    )
