# Gatehouse Production-Readiness QA Report

**Assessment date:** 2026-10-15
**Verdict:** **NOT READY** for production certification. The local regression suite passes, but PostgreSQL concurrency, event-scale load, real provider behavior, and offline workflows have not been validated. Offline-first operation is not implemented in the inspected client.

## A. Executive summary

The application has useful server-side protections: ticket numbers and idempotency keys are unique, on-spot registration is atomic, ticket issuance/check-in lock ticket rows, and the database permits only one accepted check-in per ticket. The scanner confirmation endpoint revalidates ticket state before it records entry. A PostgreSQL-only concurrency regression suite has been added, but it could not be executed here.

The existing scanner is online-dependent. The client has no service worker, IndexedDB queue, locally authoritative ticket cache, or offline registration reconciliation. Consequently, the required offline scan, registration, persistence, conflict-resolution, and recovery scenarios are unavailable and untested. Independently disconnected devices cannot globally prevent duplicate admissions without an authoritative shared system or an explicit allocation/conflict policy.

No dedicated staging PostgreSQL URL or local PostgreSQL service was available. No browser E2E runner is configured in the repository. A safe, read-only scanner-validation load script was added, but it could not be run without staging credentials and dedicated valid test tickets. No production or live Google Sheets/Gmail requests were made.

One verified observability defect was fixed: invalid QR validations in the scanner's two-step flow were not included in scan history. Failed validations are now recorded and covered by a regression test.

## B. Test environment

| Item | Observed |
|---|---|
| Repository revision | `390f540` (`Add on-spot ticket packages and quantity selector`); the working tree also contains uncommitted changes |
| Operating system | Windows |
| Local backend/frontend | `http://127.0.0.1:8001/api/health/` and `http://127.0.0.1:5174/` both returned HTTP 200 |
| Test database | Disposable Django SQLite test database; `DATABASE_URL` was explicitly cleared for the test run |
| PostgreSQL | No local server listening on port 5432; no PostgreSQL staging URL configured |
| Backend deployment | Repository config proxies Vercel `/api` requests to a Render hostname; no backend replica/load-balancer topology is declared in the repository |
| Background sync | Documented as one supervised Google Sheets worker; provider tests use mocks |
| Browser E2E/load tooling | No Playwright, Locust, or k6 setup found. A standard-library validation-only load script is available at `scripts/run_validation_load.py`. |
| Live data/provider usage | No live registration, check-in, Sheets write, Gmail send, or production test was performed |

Local `.env` contains provider settings, but these credentials were not read into this report or used for QA. The only live local service operation was a read-only health request.

## C. Executed verification

| Check | Result |
|---|---|
| `manage.py test operations.tests --verbosity 1` with SQLite | **Passed:** 61 discovered; 59 passed, 2 PostgreSQL-only tests skipped |
| Django system check | **Passed:** no issues |
| `makemigrations --check --dry-run` | **Passed:** no model changes pending a migration |
| Frontend `npm run build` | **Passed:** TypeScript check and Vite production build |
| `git diff --check` | **Passed** |
| Local backend health endpoint | **Passed:** HTTP 200 |
| Local frontend root | **Passed:** HTTP 200 |

The backend suite includes mocked spreadsheet/Gmail failure cases. These results do not establish real Google API availability, synchronization lag, or provider rate-limit behavior.

### Reusable load script

`scripts/run_validation_load.py` provides baseline, normal, peak, stress, soak, burst, and recovery presets for client count and duration, with client-count, duration, request-rate, and timeout overrides. These are steady validation workloads; the burst and recovery names do not implement a traffic ramp or recovery/fault-injection sequence. The script exercises only `POST /api/scanner/validate/` using preflight-verified unused test tickets; it does not check tickets in or create registrations. Output includes request/outcome/status counts and min/mean/p50/p95/p99/max latency. It requires explicit test-target opt-in, refuses the configured production hostname, blocks redirects, and requires HTTPS, extra confirmation, and a run ID for remote targets.

This script is not a substitute for the requested full workflow load tests: registration creation, check-in mutation, real four-scanner/two-desk browser sessions, database/resource telemetry, and network-failure injection remain unimplemented or unexecuted.

### Performance measurements

No load test was run. Throughput, request volume, p50/p95/p99/max latency, timeout/error rates under load, QR detection time, database query latency, CPU, memory, connection counts, lock waits, and queue growth are **not measured**. No performance target is claimed as achieved.

## D. Concurrency and data-integrity findings

### Verified from implementation and tests

- Ticket numbers, external ticket IDs, QR token hashes, and non-null registration idempotency keys have database uniqueness constraints.
- Accepted check-ins have a conditional unique constraint per ticket.
- The check-in service uses a transaction and `select_for_update()` on the ticket before changing its check-in state.
- The scanner check-in confirmation endpoint validates the credential and ticket state again before recording the check-in; a client-side validation result alone does not authorize entry.
- On-spot registration is transactional and obtains the offline ticket counter using a row lock. Existing operation tests cover sequential ticket IDs and idempotency behavior.
- Existing SQLite tests do **not** prove PostgreSQL row-lock behavior.

### Added but not executed

Two tests in `backend/operations/tests.py` are restricted to PostgreSQL:

1. Four scanner requests synchronized against one ticket; assert exactly one accepted transition and three `ALREADY_USED` outcomes.
2. Two desk registrations started concurrently; assert both succeed and receive distinct ticket numbers.

They were skipped on the available SQLite test database. Re-run these only with a **dedicated, disposable PostgreSQL test database**, never a production database.

### Not tested

- Concurrent requests across four actual scanner browser sessions and two desk browser sessions.
- PostgreSQL concurrent check-in and registration behavior.
- Cross-worker or cross-instance behavior, including a worker/service failure and recovery.
- Idempotency behavior after a client timeout occurring after a database commit.
- Query plans, deadlocks, connection exhaustion, lock wait duration, or connection-pool behavior.
- Distributed rate-limit consistency. Django REST Framework throttling is configured, but no shared cache/backend is configured in the inspected settings.

## E. Offline, recovery, and synchronization

| Area | Finding |
|---|---|
| PWA/service worker | No service worker or PWA registration was found in the client. |
| Local queue/cache | No IndexedDB schema or offline event queue was found. |
| Offline scan | Not supported by the inspected client; scanner calls the backend for validation/check-in. |
| Offline registration | No durable offline registration queue or replay/reconciliation flow was found. |
| Offline duplicate conflict | No client-side offline acceptance or reconciliation policy exists to test. Independent disconnected scanners cannot guarantee global duplicate prevention. |
| Network-failure recovery | No browser/proxy fault-injection tests were run. |
| Google Sheets | Import and sync failure/retry logic has automated tests with mocked APIs. No real API latency, quota, delayed-write, or conflict test was run. |
| Sheets worker | Documentation specifies a single supervised worker. Multi-worker claiming and recovery under real PostgreSQL were not load-tested. |
| Sync impact on registration | On-spot spreadsheet processing is initiated from an `on_commit` callback in the registration request path. Provider latency could therefore extend the request; this was not benchmarked. |
| Scan-to-Sheets path | Check-in queues synchronization after commit when Sheets is configured. Actual queue delay and eventual completion were not measured. |

## F. Defects and remediation

### Invalid scanner attempts missing from history

- **Severity:** Medium (operations/audit visibility)
- **Reproduction:** Use the scanner UI's `/scanner/validate/` call with an invalid QR. The two-step client stops after validation and does not call `/scanner/check-in/`. Previously, invalid QR validation did not create a history row.
- **Root cause:** The validation endpoint only recorded failed attempts for manual-ID lookups, not QR lookups.
- **Fix:** Record all failed validation attempts, preserving the submitted method and authenticated scanner/gate attribution.
- **Regression test:** `test_invalid_ticket_validation_is_recorded_for_scanner_history`.
- **Retest:** Included in the passing SQLite operations suite.

### Offline-first operation absent

- **Severity:** High / blocking for the requested offline acceptance criteria
- **Finding:** There is no offline cache, service worker, durable queue, or conflict reconciliation implementation.
- **Fix:** Not implemented in this QA pass. Designing an offline admission policy changes security and admission semantics; disconnected scanners cannot know whether another device admitted the same ticket.
- **Retest:** Not applicable; offline scenarios remain unexecuted.

## G. Gap analysis and remaining tests

The following scenarios are **not passed**; they were not run:

- Baseline, normal, peak, stress, soak, burst, and recovery load profiles from the supplied prompt.
- Request throughput/latency profiles for validation, check-in, registration, lookup, dashboard, and Sheets sync.
- Four real scanner and two registration browser sessions.
- Network disconnect, latency, packet loss, DNS failure, HTTP 500, timeout-after-commit, browser restart, and queued-event recovery.
- Offline duplicate conflict and offline registration reconciliation.
- Real PostgreSQL concurrency suite, multi-instance routing/failure, database resource monitoring, and connection-pool limits.
- Live Google Sheets API concurrency, quota, stale-write, timeout-after-write, and synchronization-lag scenarios.
- Live Gmail delivery (not necessary for admission correctness, but not tested here).
- Security review against a staging deployment and unauthorized request matrix beyond existing automated permissions tests.

## H. Final verdict and safe next step

**NOT READY** for production readiness certification under the supplied acceptance criteria. The local automated suite is green, but it does not substitute for PostgreSQL concurrency, real-browser E2E, or measured load testing. The absence of offline support is a direct blocker for the requested offline-first acceptance criteria.

To continue safely, configure a dedicated staging environment with a disposable PostgreSQL database, seeded test-only scanner/desk accounts and ticket records, and a non-production spreadsheet. Run the PostgreSQL-only tests and the specified browser/load/network scenarios against that environment. Do not point test or load tools at the production host or production database.
