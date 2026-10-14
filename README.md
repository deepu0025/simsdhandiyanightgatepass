# Gatehouse Event Ticket Operations

A standalone operations application, kept separate from the public ticket-booking website. It includes a Django REST API, role-based sessions, event/ticket records, QR/PDF ticket issuance, atomic check-in, an admin dashboard, a mobile scanner, staged CSV/XLSX imports, and on-spot registration.

Admins can manually check in a guest without a QR scan from the ticket register. A reason is required and recorded with the actor and outcome in check-in history and the audit log. This override can admit pending/unissued tickets, but cannot admit cancelled/revoked tickets or check in a ticket twice. Payment review happens in the spreadsheet; after an admin explicitly commits an import with a verified payment status, Gatehouse issues the QR/PDF. Scanning validates the issued ticket and its revocation/check-in state without rechecking payment.

## Project structure

- `backend/` — Django REST API, database models, admin interface, ticket services, and tests
- `client/` — React, Vite, TypeScript, Tailwind, admin and scanner experiences
- `.env.example` — local/deployment configuration template; contains no live credentials

## Requirements

- Python 3.12–3.14
- Node.js 20+
- PostgreSQL for production and concurrency validation

SQLite is used automatically for local development when `DATABASE_URL` is not set. It is not a substitute for production PostgreSQL locking tests.

## Local setup

1. Copy `.env.example` to `.env`. For a local SQLite run, leave `DATABASE_URL` blank.
2. In `backend/`, create/activate a virtual environment and install dependencies:

   ```powershell
   py -3.14 -m venv ..\.venv
   ..\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Create the schema, then create the configured administrator:

   ```powershell
   python manage.py migrate
   python manage.py bootstrap_admin
   python manage.py runserver 127.0.0.1:8001
   ```

   Set `BOOTSTRAP_ADMIN_EMAIL` and a unique `BOOTSTRAP_ADMIN_PASSWORD` in `.env` first. The command will not reset the password of an existing account unless `BOOTSTRAP_ADMIN_RESET_PASSWORD=true` is explicitly set.

4. In another terminal, install and start the frontend:

   ```powershell
   Set-Location ..\client
   npm install
   npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
   ```

5. Add an event in Django admin at `http://127.0.0.1:8001/django-admin/`, then sign in at `http://127.0.0.1:5174/`.

The Vite dev server proxies `/api` requests to the backend. These ports keep this operations app separate from a booking site that may already be using 5173/8000. Camera access requires HTTPS in deployment (localhost is allowed by browsers).

## Gmail OAuth email setup

SMTP is not used. Ticket emails go through the Gmail API using an OAuth refresh token:

1. In Google Cloud Console, create/select a project, enable **Gmail API**, and configure the OAuth consent screen. Add the account that will send tickets as a test user while the consent screen is in testing.
2. Create an OAuth client and copy its client ID and client secret into `GMAIL_OAUTH_CLIENT_ID` and `GMAIL_OAUTH_CLIENT_SECRET` in `.env`.
3. Obtain an offline refresh token for the sender account with the `https://www.googleapis.com/auth/gmail.send` scope. One option is Google's OAuth 2.0 Playground: enable its setting to use your own OAuth client credentials, authorize the sender account for the scope, exchange the authorization code, and copy the refresh token.
4. Set that refresh token in `GMAIL_OAUTH_REFRESH_TOKEN` and the authorized sending address in `GMAIL_SENDER_EMAIL`.
5. Restart the Django backend. An admin can use the mail action next to an issued ticket in the ticket register; the API records success/failure and does not mark mail sent until Gmail confirms delivery.

Keep OAuth client secrets and refresh tokens only in `.env` or a deployment secret manager; never paste or commit them. OAuth apps left in testing mode can have refresh tokens expire after seven days. Publish/configure the consent screen as required for ongoing use; Google verification may be required when requesting Gmail scopes for external users.

## Google Sheets synchronization setup

Payment status is maintained in Google Sheets and imported into Gatehouse automatically; Gatehouse does not independently approve payments. The continuous worker checks both `Online_Tickets` and `OnSpot_Tickets` every 30 seconds, validates changed snapshots, and commits them through the normal import path. Imported verified payment status or imported sent-email evidence can authorize ticket issuance. The worker does not send ticket emails. Sheets access and import failures do not block scanning or other operations.

1. In Google Cloud Console, enable **Google Sheets API** for the project.
2. Create a service account and download its JSON key. Store that file only on the backend host, outside source control (for example, under the ignored `backend/secrets/` folder); do not paste the JSON or private key into `.env`.
3. Create or choose the target spreadsheet and share it with the service account's `client_email`, granting Editor access.
4. Set `GOOGLE_SHEETS_SPREADSHEET_ID` to the ID between `/d/` and `/edit` in the spreadsheet URL. Provide the service-account key through `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` (for example, injected as a secret by the deployment platform) or set `GOOGLE_SERVICE_ACCOUNT_FILE` to a protected JSON file path (absolute, or relative to the repository root). Never check service-account JSON into source control.
5. Apply migrations and run one continuously supervised worker process:

   ```powershell
   python manage.py sync_google_sheets --watch --interval 30 --limit 100
   ```

   The interval must be between 30 and 60 seconds. Keep only one worker running. It records a content hash per worksheet, so unchanged snapshots do not create redundant import batches; provider failures are recorded in a sanitized form and retried on the next poll. The worker also performs an initial backfill of eligible issued tickets and processes outgoing sheet-sync jobs. Use a process supervisor in production and configure it to restart the worker if it exits. Keep the dashboard open to see the latest import status and automatically refreshed ticket list.

Automatic imports require exactly one active Gatehouse event because sheet rows do not contain an event ID, and require an active administrator account for audit attribution. If either requirement is not met, the worker records an error instead of importing into an arbitrary event. The backend service account reads the configured spreadsheet; no browser-side credential is used. Rows imported from `Online_Tickets` and `OnSpot_Tickets` retain those source categories for dashboard totals. The admin **Import data** page remains available for optional staged/manual imports and reviewing row errors; routine Google Sheets updates no longer require preview or commit.

## Initial API routes

| Route | Access | Purpose |
|---|---|---|
| `GET /api/health/` | Public | Liveness check |
| `GET /api/csrf/` | Public | Initialize session CSRF token |
| `POST /api/auth/login/`, `POST /api/auth/logout/`, `GET /api/auth/me/` | Session | Authentication |
| `GET /api/admin/dashboard/` | Admin | Live aggregate metrics |
| `GET/POST /api/admin/events/` | Admin | List events or create an active event from the application |
| `PATCH /api/admin/events/{id}/` | Admin | Activate or deactivate an event |
| `GET /api/admin/tickets/` | Admin | Ticket search and filtering |
| `POST /api/admin/registrations/on-spot/` | Admin | Idempotently record an on-spot registration; payment status is set in the spreadsheet and imported before issuance |
| `POST /api/admin/tickets/{id}/issue/` | Admin | Issue eligible ticket |
| `POST /api/admin/tickets/{id}/status/` | Admin | Cancel or revoke an issued ticket; body accepts `CANCELLED`/`REVOKED` plus an optional reason |
| `POST /api/admin/tickets/{id}/email/` | Admin | Send once, or manually retry a failed ticket email through Gmail OAuth |
| `POST /api/admin/tickets/{id}/manual-check-in/` | Admin | Record a manual check-in without a QR scan; requires a reason and is audited |
| `GET/POST /api/admin/users/` | Admin | List scanner accounts or create one assigned to a gate |
| `PATCH /api/admin/users/{id}/` | Admin | Reset a scanner password or enable/disable its account |
| `GET /api/tickets/{id}/pdf/` | Signed-in user | Download issued ticket PDF |
| `POST /api/scanner/scan/` | Admin or scanner | Validate credential and record check-in |
| `GET /api/scanner/history/` | Admin or scanner | View recent scans (scanners see their scans) |
| `POST /api/admin/import/preview/` | Admin | Validate and stage CSV/XLSX without changing ticket records |
| `POST /api/admin/import/{batch_id}/commit/` | Admin who created preview | Explicitly commit valid rows; request JSON must contain `{"confirm": true}` |
| `GET /api/admin/import/{batch_id}/errors.csv/` | Admin who created preview | Download row-level import errors |
| `POST /api/admin/import/google-sheets/preview/` | Admin | Read a configured worksheet into the staged, validation-first import flow |

The scanner response is determined by the backend. PostgreSQL row locks and a conditional unique constraint protect the successful check-in path. Client-side state is not used to determine ticket validity. Imported QR contents are never used as credentials; qualifying rows receive a new random Gatehouse QR.

The admin **Events** page can create and activate events without Django admin or a server shell; new events appear in the import and on-spot registration selectors. The admin **Gate scanners** page provides four mobile-friendly scanner panels. Create one unique login per gate; scanner passwords must be at least 12 characters, are stored as password hashes, and can be reset by an administrator. A scanner account is bound to one gate on the server, so a client cannot change its check-in gate by altering the request. Existing unassigned scanner accounts can be assigned to an open gate before scanning.

## Spreadsheet schema

The preview endpoint accepts UTF-8 CSV and XLSX using the supplied columns:

`ticket_id, ticket_number, registration_id, ticket_type, name, email, phone, amount_due, payment_method, upi_id, transaction_id_utr, payment_status, verified_by, verified_at, ticket_status, ticket_generated_at, email_status, email_sent_at, scan_status, checked_in_at, checked_in_by, created_at, updated_at, last_synced_at`

Preview validates up to 10,000 rows in a file no larger than 10 MB, detects duplicate ticket IDs/numbers, and shows row-level errors before commit. Import is a separate explicit action; commits are idempotent and report created, updated, skipped, rejected, and issued counts. Existing issued tickets and QR credentials are preserved, and source rows older than a ticket's recorded source timestamp are skipped. Payment is not approved or rejected inside Gatehouse: the sheet is authoritative. After an admin confirms a staged import, `PAID`, `CAPTURED`, or `VERIFIED` from a new or newer row is recorded as imported verification and can issue that ticket; `REJECTED`, `FAILED`, `PENDING`, and blank statuses do not authorize issuance. A row with `email_status=SENT` and a valid `email_sent_at` remains an alternate authorization. Gatehouse generates a fresh random QR credential and PDF; it never trusts the imported QR or sends an email automatically. Download the generated error report from the preview result.

The on-spot registration screen is admin-only. Each request must send an `Idempotency-Key`; retries with the same key and same details return the existing registration and ticket, while a changed request is rejected. On-spot records remain pending and unissued until the matching row is updated in the spreadsheet and explicitly imported with an authorized status. The form displays the stable ticket ID to use in the sheet. PDF download and optional email actions are available after issuance.

## Security and deployment notes

- Change `DJANGO_SECRET_KEY` and set `DJANGO_DEBUG=false` in production.
- Use PostgreSQL, HTTPS, restricted `DJANGO_ALLOWED_HOSTS`/CORS, secure cookie settings, and persistent private media storage.
- Ticket PDFs contain private attendee information and are served only through an authenticated endpoint.
- QR credentials are cryptographically random; only SHA-256 digests are stored in the database.
- Only administrators can cancel or revoke an issued ticket. The change is audited, the credential digest is retained but scanner validation rejects it, and the PDF endpoint stops serving the revoked/cancelled ticket.
- Payment decisions are maintained only in the spreadsheet. A staged import must be explicitly committed; imported `PAID`, `CAPTURED`, or `VERIFIED` status authorizes issuance of that ticket. Alternatively, an explicitly committed row with both `email_status=SENT` and `email_sent_at` authorizes that ticket only. Gatehouse generates a new QR/PDF and never trusts the imported QR. Sending the new pass by email is a separate action.
- Ticket delivery uses the Gmail API with OAuth 2.0 refresh-token credentials; SMTP passwords and app passwords are not used. Configure `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN`, and the authorized `GMAIL_SENDER_EMAIL` in the ignored `.env` file. Grant the `https://www.googleapis.com/auth/gmail.send` scope to the OAuth consent.
- To configure Gmail OAuth, create a Google Cloud project, enable the Gmail API, configure an OAuth consent screen and OAuth client, and authorize the sender account with offline access and the `gmail.send` scope. Store the resulting client ID, client secret, and refresh token only in `.env` or a deployment secret store. In OAuth consent-screen testing mode, refresh tokens may expire after seven days; configure/publish the consent screen as appropriate before relying on delivery. Google may require app verification for external users requesting Gmail scopes.
- Email attempts record `PROCESSING`, `SENT`, or `FAILED`; provider errors are sanitized, and a delivery failure does not invalidate an issued ticket. Already-sent or in-progress sends are rejected to avoid duplicate delivery. Automatic background retries are not configured; an admin can manually retry failed delivery from the ticket register.
- Google Sheets synchronization is configured independently of ticket operations. Never paste service-account credentials into source control.
- With the operations API or database unavailable, a scan cannot be confirmed. Gate staff must not admit an attendee based only on a local browser result.

## Production deployment checklist

1. Provision PostgreSQL and private persistent media/object storage. Set `DATABASE_URL`, `MEDIA_ROOT`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, `DJANGO_ALLOWED_HOSTS`, `FRONTEND_URL`, and `CORS_ALLOWED_ORIGINS` as deployment secrets/configuration. HTTPS redirect and one-year HSTS default on when debug is off; enable `DJANGO_TRUST_X_FORWARDED_PROTO=true` only when a trusted reverse proxy terminates HTTPS and overwrites `X-Forwarded-Proto`. Do not enable HSTS subdomains or preload until every relevant subdomain is HTTPS-only and the domain owner explicitly chooses that policy. Set `APP_TIME_ZONE` to the event's timezone. Never deploy the development SQLite database.
2. Provision Gmail OAuth and Google Sheets credentials only if those integrations are needed. Store all OAuth values and the service-account key in the deployment secret manager or protected backend file mount.
3. On Render, set **Root Directory** to `backend`, **Build Command** to `pip install -r requirements.txt`, and **Start Command** to `python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`. Gunicorn is included in `backend/requirements.txt`; it belongs in the start command, not the build command. Deploy the latest `main` commit so the service includes the current event API. For Vercel, either import the repository root (the root `vercel.json` builds `client/` and proxies `/api/*` to Render) or set Vercel's project root to `client` (then `client/vercel.json` supplies the proxy and SPA fallback). If the Render hostname changes, update both rewrite destinations. Set Render `DJANGO_ALLOWED_HOSTS` to the Render hostname; Render's `RENDER_EXTERNAL_HOSTNAME` is also added to Django's allowed hosts automatically. Set Render `FRONTEND_URL` and `CORS_ALLOWED_ORIGINS` to the exact Vercel site origin so Django accepts session-authenticated CSRF-protected requests from the deployed frontend. Build the client with `npm ci` and `npm run build`. Run Django behind a production WSGI/ASGI server; do not use `manage.py runserver`.
4. Run `python manage.py check --deploy`, then `python manage.py migrate`. Create the first administrator with `BOOTSTRAP_ADMIN_EMAIL` and a strong unique `BOOTSTRAP_ADMIN_PASSWORD`; remove bootstrap secrets after provisioning.
5. Configure one supervised `sync_google_sheets --watch --interval 30` worker if Sheets is enabled. Verify service-account access, exactly one active event, an active admin account, and the two worksheet headers. Sanitized Sheets failures are shown on the admin dashboard; Gmail and Sheets provider failures must not affect scanner decisions.
6. Set up encrypted, access-controlled backups for PostgreSQL and persistent ticket PDFs. Test restoration of both together, since PDFs are referenced by database rows. Define retention and recovery-time objectives before accepting live bookings.
7. Verify HTTPS, secure cookies, allowed hosts/CORS, staff role boundaries, PDF authorization, OAuth scopes, logs, database backups, and a real PostgreSQL concurrent-scan test before opening gates.

Required application configuration is `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL` (PostgreSQL in production), `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS`, and persistent `MEDIA_ROOT`; `APP_TIME_ZONE` should be set correctly. Admin bootstrap variables are required only during initial provisioning. Gmail requires its four OAuth/sender values. Sheets requires `GOOGLE_SHEETS_SPREADSHEET_ID` and either `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` or `GOOGLE_SERVICE_ACCOUNT_FILE`.

## Checks

From `backend/`: `python manage.py test operations`

From `client/`: `npm run typecheck` and `npm run build`

Concurrency guarantees must additionally be exercised against PostgreSQL. SQLite cannot validate row-level locking semantics.

## Current implementation stage

This is a working local-development stage, not yet the complete production system in the build brief. Automatic email retry workers, a cloud object-storage adapter (a shared private persistent `MEDIA_ROOT` is supported), backup/restore automation, and PostgreSQL concurrency testing remain to be completed before production deployment.
