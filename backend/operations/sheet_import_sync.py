import csv
import hashlib
import io
import json
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from rest_framework.exceptions import ValidationError as APIValidationError

from .import_commit import commit_import_batch
from .imports import create_import_batch
from .models import Event, GoogleSheetImportState, Registration, Ticket, User
from .sheets_sync import SHEET_NAMES, read_worksheet_rows

logger = logging.getLogger(__name__)
SAFE_IMPORT_ERROR = "Automatic Google Sheets import failed; it will retry."


def _advance_changed_payment_timestamps(rows):
    if len(rows) < 2:
        return rows
    updated_rows = [list(row) for row in rows]
    headers = [str(value).strip().lower() for value in updated_rows[0]]
    if "ticket_id" not in headers or "payment_status" not in headers:
        return updated_rows
    ticket_id_index = headers.index("ticket_id")
    payment_status_index = headers.index("payment_status")
    if "updated_at" not in headers:
        headers.append("updated_at")
        updated_rows[0].append("updated_at")
    updated_at_index = headers.index("updated_at")

    for row in updated_rows[1:]:
        row.extend([""] * (len(headers) - len(row)))
        ticket_id = str(row[ticket_id_index]).strip()
        incoming_status = str(row[payment_status_index]).strip().upper()
        if not ticket_id:
            continue
        ticket = Ticket.objects.select_related("registration").filter(
            external_ticket_id=ticket_id
        ).first()
        if ticket is None:
            try:
                ticket = Ticket.objects.select_related("registration").filter(
                    pk=ticket_id
                ).first()
            except (ValidationError, ValueError, TypeError):
                continue
        if ticket is None or incoming_status == ticket.registration.imported_payment_status.upper():
            continue

        revised_at = timezone.now()
        for existing_timestamp in (
            ticket.source_updated_at,
            ticket.registration.source_updated_at,
        ):
            if existing_timestamp and revised_at <= existing_timestamp:
                revised_at = existing_timestamp + timedelta(microseconds=1)
        row[updated_at_index] = revised_at.isoformat()
    return updated_rows


def sync_google_sheet_import(worksheet):
    if worksheet not in SHEET_NAMES:
        raise ValueError("Unsupported Google worksheet.")

    events = list(Event.objects.filter(is_active=True).order_by("pk")[:2])
    if len(events) != 1:
        raise ValueError("Automatic import requires exactly one active event.")
    actor = User.objects.filter(
        role=User.Role.ADMIN,
        is_active=True,
    ).order_by("pk").first()
    if actor is None:
        raise ValueError("Automatic import requires an active administrator account.")

    rows = read_worksheet_rows(worksheet)
    if not rows:
        raise ValueError(f"The {worksheet} worksheet has no header row.")

    snapshot = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    content_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    state, _ = GoogleSheetImportState.objects.get_or_create(worksheet=worksheet)

    with transaction.atomic():
        state = GoogleSheetImportState.objects.select_for_update().get(pk=state.pk)
        if state.content_hash == content_hash:
            state.last_synced_at = timezone.now()
            state.last_error = ""
            state.save(update_fields=("last_synced_at", "last_error", "updated_at"))
            return {
                "worksheet": worksheet,
                "changed": False,
                **state.last_result,
            }

        if len(rows) == 1:
            result = {"created": 0, "updated": 0, "skipped": 0, "rejected": 0, "issued": 0}
        else:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerows(_advance_changed_payment_timestamps(rows))
            upload = SimpleUploadedFile(
                f"{worksheet}.csv",
                output.getvalue().encode("utf-8"),
                content_type="text/csv",
            )
            source = (
                Registration.Source.ONLINE
                if worksheet == "Online_Tickets"
                else Registration.Source.ON_SPOT
            )
            batch, _unknown_columns = create_import_batch(
                uploaded=upload,
                event=events[0],
                actor=actor,
                registration_source=source,
            )
            result = commit_import_batch(batch.pk, actor)
            result["batch_id"] = str(batch.pk)

        state.content_hash = content_hash
        state.last_synced_at = timezone.now()
        state.last_error = ""
        state.last_result = result
        state.save(
            update_fields=(
                "content_hash",
                "last_synced_at",
                "last_error",
                "last_result",
                "updated_at",
            )
        )
        return {"worksheet": worksheet, "changed": True, **result}


def mark_google_sheet_import_failed(worksheet, error):
    GoogleSheetImportState.objects.update_or_create(
        worksheet=worksheet,
        defaults={"last_error": SAFE_IMPORT_ERROR},
    )
    logger.warning(
        "Automatic Google Sheets import failed for %s (%s).",
        worksheet,
        type(error).__name__,
    )


def sync_google_sheet_imports():
    results = []
    for worksheet in SHEET_NAMES:
        try:
            results.append(sync_google_sheet_import(worksheet))
        except (HttpError, GoogleAuthError, OSError, ValueError, APIValidationError) as error:
            mark_google_sheet_import_failed(worksheet, error)
            results.append(
                {
                    "worksheet": worksheet,
                    "changed": False,
                    "error": SAFE_IMPORT_ERROR,
                }
            )
    return results
