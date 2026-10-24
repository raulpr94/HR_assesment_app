"""AdminService and upload import: HR-only, filters, outbox processing."""

from __future__ import annotations

import pytest

from core.errors import DomainError, ErrorCode
from core.models import NotificationStatus, Status
from core.permissions import Viewer
from services.clock import FixedClock
from services.container import AppServices
from tests.conftest import SMALL_CSV, drive


def viewer(services: AppServices, employee_id: str) -> Viewer:
    found = services.directory.viewer(employee_id)
    assert found is not None
    return found


@pytest.mark.parametrize("employee_id", ["C1", "B1", "A1"])
def test_everything_is_hr_only(services: AppServices, employee_id: str) -> None:
    not_hr = viewer(services, employee_id)
    admin = services.admin
    calls = [
        lambda: admin.cycles(not_hr),
        lambda: admin.audit(not_hr),
        lambda: admin.audit_actions(not_hr),
        lambda: admin.outbox(not_hr),
        lambda: admin.outbox_counts(not_hr),
        lambda: admin.outbox_message(not_hr, 1),
        lambda: admin.send_pending(not_hr),
    ]
    for call in calls:
        with pytest.raises(DomainError) as err:
            call()
        assert err.value.code is ErrorCode.HR_ONLY


def test_cycle_summaries(services: AppServices, clock: FixedClock) -> None:
    drive(services, clock, "C1", "B1", Status.COMPLETED)
    drive(services, clock, "C1", "B2", Status.INITIATED)
    (summary,) = services.admin.cycles(viewer(services, "H1"))
    assert (summary.eligible, summary.started, summary.completed) == (5, 2, 1)


def test_audit_filters_and_names(services: AppServices, clock: FixedClock) -> None:
    hr = viewer(services, "H1")
    drive(services, clock, "C1", "B1", Status.SELF_ASSESSMENT_SUBMITTED)
    everything = services.admin.audit(hr)
    assert {"open_cycle", "import_employees", "initiate", "submit_self"} <= {
        e["action"] for e in everything
    }
    by_carl = services.admin.audit(hr, actor_id="C1")
    assert by_carl and all(e["actor_name"] == "Carl Chef" for e in by_carl)
    only_submits = services.admin.audit(hr, actions=["submit_self"])
    assert [e["field_changed"] for e in only_submits] == ["self_submitted_at"]
    assert "initiate" in services.admin.audit_actions(hr)
    assert len(services.admin.audit(hr, limit=1)) == 1


def test_outbox_view_and_processing(services: AppServices) -> None:
    hr = viewer(services, "H1")
    assert services.admin.outbox_counts(hr) == {"pending": 2, "sent": 0, "failed": 0, "skipped": 0}
    rows = services.admin.outbox(hr, status=NotificationStatus.PENDING)
    message = services.admin.outbox_message(hr, rows[0]["id"])
    assert message is not None and message["payload"]["type"] == "cycle_started"
    assert services.admin.outbox_message(hr, 999) is None

    report = services.admin.send_pending(hr)  # dry run by default
    assert report.counts == {"skipped": 2}
    assert services.admin.outbox_counts(hr)["skipped"] == 2


def test_upload_import(bare_services: AppServices) -> None:
    raw = SMALL_CSV.encode("utf-8")
    _, report = bare_services.imports.validate_bytes(raw, "upload.csv")
    assert report.ok and report.total_rows == 7
    done = bare_services.imports.import_bytes(raw, "upload.csv")
    assert done.imported and done.inserted == 7

    again = bare_services.imports.import_bytes(raw, "upload.csv", actor_id="H1")
    assert again.updated == 7
    with pytest.raises(DomainError) as err:
        bare_services.imports.import_bytes(raw, "upload.csv", actor_id="C1")
    assert err.value.code is ErrorCode.HR_ONLY

    bad = bare_services.imports.import_bytes(b"employee_id,name\n1,x\n", "bad.csv")
    assert not bad.ok and not bad.imported


def test_windows_1252_upload(bare_services: AppServices) -> None:
    text = SMALL_CSV.replace("Hilde Hartmann", "Hilde Härtmann")
    _, report = bare_services.imports.validate_bytes(text.encode("cp1252"), "excel.csv")
    assert report.ok
