# Performance Review Assistant

A Streamlit app that guides employees at every level through a structured performance
review cycle: managers start reviews from a writeback table, employees and managers fill
in the same survey, they meet, both confirm, and HR can follow and correct everything.
Email notifications are queued in an outbox and can be sent through Zapier later.

## Quick start

1. **Clone**
   ```bash
   git clone <your-repo-url> perf_review
   cd perf_review
   ```
2. **Run setup** (creates `.venv`, installs requirements, creates `.env`, the database and demo data)
   - macOS / Linux: `scripts/setup.sh`
   - Windows: `scripts\setup.bat`
3. **Run the app**
   - macOS / Linux: `scripts/run_app.sh`
   - Windows: `scripts\run_app.bat`

The app opens at <http://localhost:8501>. Use **View as** in the sidebar to see it as any
employee (HR, C, B or A level). Needs Python 3.10 or newer.

### Reset the database

```bash
python manage.py reset-db --yes   # deletes data/app.db and recreates empty tables
python manage.py seed-demo        # reload the demo hierarchy and open a demo cycle
```

Stop the running app first, otherwise the database file may be locked (Windows).

### Switch NOTIFY_MODE

Notifications are always queued in the `notification_outbox` table. What happens next
is set in `.env`:

```ini
NOTIFY_MODE=dry_run          # default: messages are logged and marked "skipped"
NOTIFY_MODE=zapier           # POST each message as JSON to the webhook
ZAPIER_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/...
```

Then process the outbox with `python manage.py send-notifications`
(`--retry-failed` re-queues failed messages first).

## Commands

All tasks live in `manage.py` (run inside the virtual environment). Each is safe to re-run.

| Command | Purpose |
|---|---|
| `python manage.py init-db` | Create `data/app.db` and all tables (idempotent). `review_progress` stays empty. |
| `python manage.py import-employees --file data/employees.csv` | Validate and upsert the hierarchy; prints every problem. `--validate-only` writes nothing. |
| `python manage.py seed-demo` | Write a demo `employees.csv` (1 HR, 3 C, 8 B, 25 A) if none exists, import it, and open a demo cycle if there is none. `--force` overwrites the CSV, `--no-cycle` skips the cycle. |
| `python manage.py open-cycle --name "2026 Annual"` | Open a cycle (`--start`, `--due` as DD.MM.YYYY; default today + 6 weeks) and notify C-level managers. |
| `python manage.py close-cycle` | Close the open cycle. |
| `python manage.py reset-db --yes` | Delete and recreate the database. Refuses without `--yes`. |
| `python manage.py send-notifications` | Process the outbox in the configured mode. |
| `python manage.py run` | Start Streamlit. Extra arguments are passed on, e.g. `run --server.port 8502`. |

Tests and lint: `scripts/run_tests.sh` (or `.bat`) runs `ruff check`, `ruff format --check`
and `pytest`.

## Employee CSV

`data/employees.csv` with the columns
`employee_id, name, email, level, role_title, department, manager_id, is_hr_admin`.
Comma or semicolon separated (Excel exports work), UTF-8 or Windows-1252.

The import checks unique ids and emails, valid emails, that every `manager_id` exists,
that there are no reporting cycles, and that levels match reporting lines
(A → B → C; C reports to C or nobody; at least one HR admin, who must be level C).
All problems are listed with row and column, and nothing is imported until the file is
clean. Re-importing updates people by `employee_id`; people missing from the new file
are deactivated, not deleted, because their reviews remain.

## The Overview page

- **My review** (levels A and B): a step indicator and the next action, e.g. a button to
  complete the self-assessment, or who you're waiting for.
- **My team** (managers): every direct report in one writeback table. Tick **Initiate** to
  start a review; pick a **Meeting** date once the manager assessment is in. Completed steps
  show their date. Nothing is saved until you press **Save changes** and confirm.
- **All reviews** (HR): the same table for the whole company, with filters, plus an
  override panel that previews exactly what moving a review forward or back will change.

`st.data_editor` can only lock whole columns, so the Initiate and Meeting columns are
unlocked whenever at least one row may use them. Edits on rows where that step isn't
allowed are listed as "can't be saved" and never sent; the service checks every save again.

## The Review detail page

Pick any review you take part in (yours first). Links in notification emails
(`/review?review_id=…`) open the right review directly.

- **Filling in:** the survey is a form with **Save draft** and **Submit…**. Submitting asks
  for confirmation, because answers are read-only afterwards (enforced in the service).
- **Reviewer:** sees the submitted self-assessment read-only next to their own form.
- **Both sides:** from the meeting date (see `REVEAL_MANAGER_ANSWERS`) each question shows
  self and manager answers side by side.
- **Confirming:** from the meeting date each person ticks "I confirm this review was
  discussed"; the employee can add a comment. The second confirmation completes the review.
- **HR** sees everything, including a History panel with the audit log of that review.
  Skip-level managers see the progress but not the answers.

## The Admin page (HR only)

- **Cycles:** list with progress, open a new cycle (department managers are notified),
  close the open one. Both ask for confirmation first.
- **Employees:** upload a CSV; every problem is listed by row and column and nothing is
  imported until the file is clean. Download a template or the current hierarchy.
- **Audit log:** filter by action, person and review; download as CSV.
- **Notifications:** counts per status, the queued messages with their full text and the
  JSON payload Zapier would receive, and buttons to process the outbox or retry failed
  messages. In `zapier` mode, sending asks for confirmation because real emails go out.

Email templates live in `templates/<type>.txt.j2` (first line `Subject: …`, then the body).

## Process guide and Dashboard

- **Process guide** (everyone): the six steps, then what matters for you. Employees see
  what to expect as a reviewee, line managers see both roles (including the rule that their
  own review comes first), department managers and HR see a suggested timeline based on the
  cycle dates, and HR also sees its own tasks.
- **Dashboard** (department managers for their whole reporting area, HR company-wide):
  completion rate, reviews per status, overdue reviews after the due date, average self vs.
  manager rating per question, and a table per team; pick a team to drill down.
  Averages over fewer than 3 submitted answers are hidden (`MIN_GROUP_SIZE` in
  `services/dashboard_service.py`), and drafts never count.

## How a review works

Every review is a pair (reviewer = manager, reviewee = direct report). C→B and B→A
reviews run through the same code.

| Step | Who | Column filled |
|---|---|---|
| Initiated | Manager (B managers only once their own review is completed) | `initiated_at` |
| Self-assessment submitted | Reviewee | `self_submitted_at` |
| Manager assessment submitted | Reviewer | `manager_submitted_at` |
| Meeting scheduled | Reviewer (future date within the cycle; can be moved until it happens) | `meeting_at` |
| Acknowledged | Each party, from the meeting date | `reviewee_ack_at`, `reviewer_ack_at` |
| Completed | Automatic when both have confirmed | `completed_at` |

The status is never stored: `core.workflow.derive_status()` computes it from which
columns are filled. A row only exists once a review is initiated; everyone else shows
as "Not started". HR can move a review one step forward or back with a reason; going
back clears that step and all later ones and unlocks the matching answers.

## Architecture

```
ui → services → core
        ↓        ↑
        db ──────┘
```

- `core/`: pure Python domain (models, workflow and transition table, permissions,
  survey, hierarchy). No Streamlit, no SQL.
- `db/`: SQLite through SQLAlchemy; repositories are the only code that touches tables.
  Repositories return `core` dataclasses, so `db` imports `core` (never the reverse).
- `services/`: orchestration. `ReviewService.transition()` is the single workflow entry
  point and runs guards, row update, audit entry and notifications in one transaction.
- `ui/`: thin Streamlit pages and components. Login sits behind `ui/auth.py`'s
  `AuthProvider` so real authentication can replace "View as" without touching pages.
- `config/`: settings (`.env`, environment or `st.secrets`), `survey.yaml`, `texts_en.yaml`.

`tests/test_architecture.py` enforces these import rules.

### Changing the survey

Edit `config/survey.yaml`. If you change questions, bump `version` and copy the previous
file to `config/survey_archive/<old version>.yaml`. Each review stores the version it
started with, so older answers stay readable.

### Translating

All user-facing text is in `config/texts_en.yaml`. A German version is a copy named
`texts_de.yaml` with the same keys.

## Status of the build

| Milestone | Status |
|---|---|
| 1. Foundation: skeleton, CLI and scripts, schema, CSV import, seed data, workflow, `derive_status`, permissions, tests | Done |
| 2. Overview page: writeback table and "View as" | Done |
| 3. Review detail page | Done |
| 4. Notifications: outbox, templates, HR admin page | Done |
| 5. Process guide and dashboard | Done |

## Assumptions

Where the spec left room, these choices were made (each is also noted in the code):

- C-level managers and the HR Director are not reviewed; only one cycle is open at a time.
- HR may initiate a review on a manager's behalf; the B→A unlock rule still applies.
- HR overrides also work on closed cycles. "Advance" from *Meeting scheduled* fills the
  reviewee's confirmation first, then the reviewer's (which completes the review).
- Reverting *Initiated* removes the row (and its draft answers): the person is back to
  "Not started". The audit trail keeps the history.
- "From the meeting date" means the local calendar day (`APP_TIMEZONE`), both for
  revealing the manager's answers and for confirming. `REVEAL_MANAGER_ANSWERS` can switch
  the reveal to the meeting start time or to completion.
- Skip-level managers see the progress of reviews in their reporting line, not the answers.
- Only the reviewee's acknowledgement comment is stored.
- `review_progress` has one extra column, `survey_version`.
- `seed-demo` opens a demo cycle when the database has none, so the demo works at once.
- Dashboard averages are hidden for groups under 3 answers, so small teams stay anonymous.
- The suggested timeline splits the cycle evenly: started by 15 %, self-assessments by
  40 %, manager assessments by 60 %, meetings scheduled by 70 %, confirmed by the due date.
