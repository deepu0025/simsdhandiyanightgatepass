import logging
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import CheckIn, EmailDeliveryAttempt, Registration, SpreadsheetSyncJob, Ticket

logger = logging.getLogger(__name__)
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SHEET_COLUMNS = (
    "ticket_id",
    "ticket_number",
    "registration_id",
    "ticket_type",
    "name",
    "email",
    "phone",
    "amount_due",
    "payment_method",
    "upi_id",
    "transaction_id_utr",
    "payment_status",
    "verified_by",
    "verified_at",
    "ticket_status",
    "ticket_generated_at",
    "email_status",
    "email_sent_at",
    "scan_status",
    "checked_in_at",
    "checked_in_by",
    "created_at",
    "updated_at",
    "last_synced_at",
)
SHEET_NAMES = ("Online_Tickets", "OnSpot_Tickets")
SAFE_SYNC_ERROR = "Google Sheets sync failed; the job will be retried."


def sheets_configured():
    return bool(
        settings.GOOGLE_SHEETS_SPREADSHEET_ID.strip()
        and (
            settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON.strip()
            or settings.GOOGLE_SERVICE_ACCOUNT_FILE.strip()
        )
    )


def queue_ticket_sync(ticket_id):
    if not sheets_configured():
        return

    def enqueue():
        try:
            ticket = Ticket.objects.get(pk=ticket_id)
            ticket_was_issued = (
                ticket.status == Ticket.Status.ISSUED
                or (
                    ticket.status in {Ticket.Status.CANCELLED, Ticket.Status.REVOKED}
                    and ticket.generated_at is not None
                    and bool(ticket.qr_token_hash)
                )
            )
            if ticket_was_issued and ticket.is_eligible_for_issuance:
                SpreadsheetSyncJob.objects.create(ticket=ticket)
        except Exception as error:
            logger.error(
                "Could not queue Google Sheets sync for ticket %s (%s).",
                ticket_id,
                type(error).__name__,
            )

    transaction.on_commit(enqueue, robust=True)


def _service():
    service_account_json = settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON.strip()
    if service_account_json:
        try:
            service_account_info = json.loads(service_account_json)
        except json.JSONDecodeError as error:
            raise ValueError("Google service-account JSON is invalid.") from error
        if not isinstance(service_account_info, dict):
            raise ValueError("Google service-account JSON must be an object.")
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=[SHEETS_SCOPE],
        )
    else:
        credential_path = Path(settings.GOOGLE_SERVICE_ACCOUNT_FILE).expanduser()
        if not credential_path.is_absolute():
            credential_path = (settings.BASE_DIR.parent / credential_path).resolve()
        credentials = service_account.Credentials.from_service_account_file(
            str(credential_path),
            scopes=[SHEETS_SCOPE],
        )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _ensure_worksheets(service):
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID
    spreadsheet = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
        .execute()
    )
    existing = {
        sheet["properties"]["title"]: sheet["properties"]["sheetId"]
        for sheet in spreadsheet.get("sheets", [])
    }
    missing = [name for name in SHEET_NAMES if name not in existing]
    if missing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {"addSheet": {"properties": {"title": name}}}
                    for name in missing
                ]
            },
        ).execute()

    for name in SHEET_NAMES:
        current = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{name}'!A1:X1")
            .execute()
            .get("values", [])
        )
        if not current:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{name}'!A1:X1",
                valueInputOption="RAW",
                body={"values": [list(SHEET_COLUMNS)]},
            ).execute()
        elif current[0] != list(SHEET_COLUMNS):
            raise ValueError(f"The {name} worksheet has unexpected column headings.")


def read_worksheet_rows(worksheet):
    if worksheet not in SHEET_NAMES:
        raise ValueError("Choose Online_Tickets or OnSpot_Tickets.")
    service = _service()
    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
            range=f"'{worksheet}'!A:X",
        )
        .execute()
    )
    return response.get("values", [])


def _iso(value):
    return value.isoformat() if value else ""


def _ticket_row(ticket, synced_at):
    registration = ticket.registration
    latest_email = (
        EmailDeliveryAttempt.objects.filter(
            ticket=ticket,
            status=Ticket.EmailStatus.SENT,
        )
        .order_by("-attempted_at")
        .first()
    )
    checkin = CheckIn.objects.filter(
        ticket=ticket,
        result=CheckIn.Result.ACCEPTED,
    ).order_by("-scanned_at").first()
    values = {
        "ticket_id": ticket.external_ticket_id or str(ticket.pk),
        "ticket_number": ticket.ticket_number,
        "registration_id": registration.external_id or str(registration.pk),
        "ticket_type": ticket.ticket_type,
        "name": ticket.attendee_name,
        "email": ticket.attendee_email,
        "phone": ticket.attendee_phone,
        "amount_due": str(registration.order_total) if registration.order_total is not None else "",
        "payment_method": registration.payment_method,
        "upi_id": registration.upi_id,
        "transaction_id_utr": registration.transaction_id,
        "payment_status": (
            registration.imported_payment_status or Registration.PaymentStatus.PENDING
        ),
        "verified_by": registration.imported_verified_by,
        "verified_at": _iso(registration.imported_verified_at),
        "ticket_status": ticket.status,
        "ticket_generated_at": _iso(ticket.generated_at),
        "email_status": (
            ticket.email_status
            if latest_email
            else (
                Ticket.EmailStatus.SENT
                if ticket.has_imported_email_verification
                else ticket.email_status
            )
        ),
        "email_sent_at": _iso(
            latest_email.attempted_at
            if latest_email
            else ticket.imported_email_sent_at
        ) or ticket.imported_email_verification_timestamp,
        "scan_status": "CHECKED_IN" if checkin else "NOT_SCANNED",
        "checked_in_at": _iso(checkin.scanned_at if checkin else None),
        "checked_in_by": checkin.scanner.email if checkin and checkin.scanner else "",
        "created_at": _iso(ticket.created_at),
        "updated_at": _iso(ticket.updated_at),
        "last_synced_at": _iso(synced_at),
    }
    return [values[column] for column in SHEET_COLUMNS]


def sync_ticket(ticket_id):
    ticket = (
        Ticket.objects.select_related("registration__event")
        .get(pk=ticket_id)
    )
    if (
        ticket.status
        not in {Ticket.Status.ISSUED, Ticket.Status.CANCELLED, Ticket.Status.REVOKED}
        or not ticket.is_eligible_for_issuance
    ):
        return False

    service = _service()
    _ensure_worksheets(service)
    worksheet = (
        "OnSpot_Tickets"
        if ticket.registration.source == Registration.Source.ON_SPOT
        else "Online_Tickets"
    )
    spreadsheet_id = settings.GOOGLE_SHEETS_SPREADSHEET_ID
    rows = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{worksheet}'!A:A")
        .execute()
        .get("values", [])
    )
    stable_id = ticket.external_ticket_id or str(ticket.pk)
    row_index = next(
        (
            index
            for index, row in enumerate(rows[1:], start=2)
            if row and row[0] == stable_id
        ),
        None,
    )
    synced_at = timezone.now()
    values = _ticket_row(ticket, synced_at)
    if row_index is None:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{worksheet}'!A:X",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()
    else:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{worksheet}'!A{row_index}:X{row_index}",
            valueInputOption="RAW",
            body={"values": [values]},
        ).execute()

    Ticket.objects.filter(pk=ticket.pk).update(last_synced_at=synced_at)
    return True


def _claim_next_job():
    with transaction.atomic():
        stale_cutoff = timezone.now() - timedelta(minutes=10)
        SpreadsheetSyncJob.objects.filter(
            status=SpreadsheetSyncJob.Status.PROCESSING,
            updated_at__lt=stale_cutoff,
        ).update(
            status=SpreadsheetSyncJob.Status.FAILED,
            last_error=SAFE_SYNC_ERROR,
            next_attempt_at=timezone.now(),
        )
        job = (
            SpreadsheetSyncJob.objects.select_for_update()
            .filter(
                status=SpreadsheetSyncJob.Status.PENDING,
                next_attempt_at__isnull=True,
            )
            .order_by("created_at")
            .first()
        )
        if job is None:
            job = (
                SpreadsheetSyncJob.objects.select_for_update()
                .filter(
                    status=SpreadsheetSyncJob.Status.FAILED,
                    next_attempt_at__lte=timezone.now(),
                )
                .order_by("created_at")
                .first()
            )
        if job is not None:
            job.status = SpreadsheetSyncJob.Status.PROCESSING
            job.attempts += 1
            job.next_attempt_at = None
            job.save(update_fields=("status", "attempts", "next_attempt_at", "updated_at"))
        return job


def process_sync_jobs(limit=100):
    processed = 0
    for _ in range(limit):
        job = _claim_next_job()
        if job is None:
            break
        try:
            sync_ticket(job.ticket_id)
        except Exception as error:
            http_status = error.resp.status if isinstance(error, HttpError) else None
            retryable = http_status is None or http_status in {408, 429} or http_status >= 500
            job.status = SpreadsheetSyncJob.Status.FAILED
            job.last_error = SAFE_SYNC_ERROR
            if retryable:
                retry_seconds = min(30 * (2 ** min(job.attempts - 1, 7)), 3600)
                job.next_attempt_at = timezone.now() + timedelta(seconds=retry_seconds)
            else:
                job.next_attempt_at = None
            job.save(
                update_fields=("status", "last_error", "next_attempt_at", "updated_at")
            )
            logger.warning(
                "Google Sheets sync failed for ticket %s (%s).",
                job.ticket_id,
                type(error).__name__,
            )
        else:
            job.status = SpreadsheetSyncJob.Status.SUCCEEDED
            job.last_error = ""
            job.completed_at = timezone.now()
            job.save(
                update_fields=("status", "last_error", "completed_at", "updated_at")
            )
        processed += 1
    return processed
