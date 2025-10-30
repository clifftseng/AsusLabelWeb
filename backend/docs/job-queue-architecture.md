# Stateful Job Queue Architecture

This document captures the target architecture for the asynchronous job processing
pipeline that powers the ASUS Label analysis workflow. It combines the original
design proposal from the product owner with additional reliability, observability,
and multi-user considerations.

## High-Level Flow

1. **Submission (Front-end)**  
   Users submit analysis jobs from the existing UI. Each request contains the
   input root path and a list of PDF files selected for analysis.

2. **Job Creation (Backend API)**  
   The `/api/jobs` endpoint persists a job record and creates a dedicated working
   directory under `<JOB_STORAGE_ROOT>/<job_id>`. Input files are copied into the
   `input/` subfolder inside the job directory. The API returns immediately with
   the `job_id` and an initial status of `queued`.

3. **Background Processing (Worker Service)**  
   A background worker (or a pool of workers) continuously dequeues pending jobs
   ordered by creation time. For each job it performs the current pipeline:
   - detect format hints
   - convert PDFs to images
   - call Azure Document Intelligence
   - call Azure OpenAI for enrichment
   Intermediate assets are written inside the job directory using the structure
   described below.

4. **Status & Results Exposure**  
   The backend exposes REST endpoints plus a Server-Sent Events (SSE) channel to
   publish job state changes. The front-end dashboard polls the list endpoint and
   subscribes to SSE for near real-time updates. Each job exposes metadata, the
   current file being processed, a link to the Excel report, and log locations.

5. **Retention & Cleanup**  
   A scheduled cleaner removes heavy artifacts (PDF originals, rendered images)
   for completed jobs after a configurable grace period while keeping the
   structured results and metadata. Failed jobs are retained longer for
   debugging. Logs and Excel reports follow configurable retention windows.

## Domain Concepts

| Concept | Description |
| --- | --- |
| **Job** | Represents a single user submission. Immutable identity, mutable state machine. |
| **Task** | A unit of work within the pipeline (format detection, DI call, GPT call). Used for logging and telemetry. |
| **Worker** | A background process that claims jobs, updates their status, and executes the pipeline. Multiple workers can run concurrently. |
| **Queue** | A persistent store that tracks job metadata, status, progress, and ordering. Backed by SQLite for now with an easy path to switch to Postgres/Redis. |
| **Event Log** | Append-only log entries associated with a job. Used to display status messages in the UI and for auditability. |

### Job States

```
draft (internal) -> queued -> running -> (completed | failed | cancelled)
                                   \-> retrying (transient, automatically transitions back to running)
```

- `queued`: waiting for a worker to claim it.
- `running`: claimed by exactly one worker, currently processing.
- `retrying`: temporary state when the worker retries after a recoverable error.
- `completed`: processing finished successfully and outputs are ready for download.
- `failed`: unrecoverable error. Manual intervention may be required.
- `cancelled`: cancelled by the user or by a timeout guard.

State transitions are persisted transactionally with optimistic locking to avoid
double-processing in multi-worker deployments.

### File-System Layout

Each job uses a dedicated directory with predictable subfolders:

```
<JOB_STORAGE_ROOT>/<job_id>/
├── input/                # Original PDFs copied from user path
├── working/              # Intermediate assets (images, JSON payloads)
├── output/
│   ├── report.xlsx       # Final Excel report
│   └── summary.json      # Machine-readable summary for API responses
├── logs/
│   ├── worker.log        # Pipeline log for this job
│   └── events.jsonl      # Structured status events
└── status.json           # Current status snapshot for debugging
```

When cleanup runs, the `input/` and `working/` directories are deleted for
completed or cancelled jobs that exceed the retention window. The `output/`
directory remains until it expires or is manually removed.

## Persistence Layer

- **Queue database:** `sqlite:///backend/job_queue.db` (configurable via `JOB_QUEUE_URL`).
  We rely on SQLite WAL mode and serialized transactions to avoid writer contention.
  The repository interface makes it straightforward to swap to Postgres or Redis later.
- **File metadata:** `status.json` plus the queue record keep status in sync.
- **Logs:** structured JSON line files stored per job. Aggregated metrics are derived
  from the queue database (`processing_time_sec`, `retry_count`, etc.).

## Concurrency & Reliability

- Workers use `SELECT ... FOR UPDATE`-like semantics implemented with SQLite
  `UPDATE ... WHERE status='queued' ORDER BY created_at LIMIT 1` guarded by row
  version numbers. Only one worker can claim a job due to the version check.
- Heartbeat timestamps provide detection for stuck jobs. A monitor task requeues
  jobs if the owning worker stops updating `heartbeat_at` within
  `JOB_STUCK_TIMEOUT`.
- `retry_count` and `retry_backoff` configuration guard against runaway retries.
  Failed jobs surface the error message and stack trace in the UI.
- `job_events` table records user-triggered cancellations and system events.

## API Surface (Draft)

| Method | Path | Description |
| --- | --- | --- |
| `POST /api/jobs` | Create job, copy files, returns `job_id`. |
| `GET /api/jobs` | Paginated list of jobs (filters by owner, status). |
| `GET /api/jobs/{job_id}` | Detailed status with results, links, timeline. |
| `POST /api/jobs/{job_id}/cancel` | Request cancellation. |
| `GET /api/jobs/{job_id}/download` | Download Excel output when ready. |
| `GET /api/job-stream` | SSE channel streaming status updates. |

Existing endpoints (`/api/list-pdfs`, legacy `/api/analyze/*`) remain temporarily
but will be deprecated once the new flow is fully integrated.

## Configurable Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `JOB_QUEUE_URL` | `sqlite:///backend/job_queue.db` | Queue persistence URL. |
| `JOB_STORAGE_ROOT` | `backend/job_runs` | Root directory for job folders. |
| `JOB_MAX_WORKERS` | `2` | Max concurrent worker tasks per process. |
| `JOB_HEARTBEAT_SEC` | `15` | Frequency workers update heartbeat. |
| `JOB_STUCK_TIMEOUT_SEC` | `300` | Requeue jobs with stale heartbeat. |
| `JOB_CLEANUP_AFTER_SEC` | `43200` | Cleanup completed job inputs after 12h. |
| `JOB_FAILED_RETENTION_SEC` | `172800` | Retain failed jobs for 48h. |
| `JOB_SSE_RETRY_MS` | `5000` | Retry interval for SSE clients. |
| `JOB_EXPORT_TIMEZONE` | `UTC` | Timezone for timestamps in Excel/export. |

All settings are loaded through `settings.py` via `python-dotenv` and documented
in `docs/runbook.md`.

## Testing Strategy

- **Unit tests:** cover job domain logic (state transitions, version checks,
  retry policy) and repository behavior with SQLite in-memory databases.
- **Integration tests:** exercise REST endpoints + worker loop using temporary
  directories (pytest fixtures) and fake analysis pipelines.
- **End-to-end (frontend):** React component tests mock SSE events and verify
  the dashboard renders updates, pagination, and download links.
- **Load / resilience tests:** CLI script to enqueue many jobs, simulate worker
  crashes, and ensure recovery + cleanup behave correctly.

## Open Questions / Next Iterations

- Should we emit notifications (email/Teams/Slack) when jobs finish or fail?
- Do we need role-based permissions to prevent users from reading other jobs?
- How should we version analysis outputs if the pipeline logic changes?
- Would Redis Streams or Azure Storage Queue provide better horizontal scaling
  once traffic grows?

These can be addressed in follow-up sprints once the core architecture is stable.

