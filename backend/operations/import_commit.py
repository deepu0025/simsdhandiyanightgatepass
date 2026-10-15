from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import datetime

from .models import (
    IMPORTED_VERIFIED_PAYMENT_STATUSES,
    AuditLog,
    ImportBatch,
    Registration,
    Ticket,
)
from .services import issue_ticket


def _timestamp(data, field):
    value = data.get("_timestamps", {}).get(field, "")
    return datetime.fromisoformat(value) if value else None


def _ticket_for_row(data):
    ticket = Ticket.objects.filter(external_ticket_id=data["ticket_id"]).first()
    if ticket:
        return ticket
    try:
        return Ticket.objects.filter(pk=data["ticket_id"]).first()
    except (ValidationError, ValueError, TypeError):
        return None


def _sync_imported_payment_status(registration, data):
    incoming_updated_at = _timestamp(data, "updated_at")
    if registration.source_updated_at and (
        not incoming_updated_at or incoming_updated_at <= registration.source_updated_at
    ):
        return

    imported_status = data.get("payment_status", "").strip().upper()
    if imported_status in IMPORTED_VERIFIED_PAYMENT_STATUSES:
        payment_status = Registration.PaymentStatus.VERIFIED
    elif imported_status == "REJECTED":
        payment_status = Registration.PaymentStatus.REJECTED
    else:
        payment_status = Registration.PaymentStatus.PENDING

    registration.payment_status = payment_status
    registration.imported_payment_status = imported_status[:40]
    registration.imported_verified_by = data.get("verified_by", "")[:160]
    registration.imported_verified_at = _timestamp(data, "verified_at")
    if incoming_updated_at:
        registration.source_updated_at = incoming_updated_at
    registration.save(
        update_fields=(
            "payment_status",
            "imported_payment_status",
            "imported_verified_by",
            "imported_verified_at",
            "source_updated_at",
            "updated_at",
        )
    )


def _create_imported_ticket(batch, row):
    data = row["data"]
    reg_external_id = data["_registration_external_id"]
    registration = Registration.objects.filter(external_id=reg_external_id).first()
    if registration and registration.event_id != batch.event_id:
        raise ValueError("registration_id is already associated with another event")

    amount_due = data.get("_amount_due") or None
    if registration is None:
        registration = Registration.objects.create(
            external_id=reg_external_id,
            event=batch.event,
            source=data.get("_registration_source", Registration.Source.IMPORT),
            buyer_name=data["name"][:160],
            buyer_email=data["email"],
            buyer_phone=data.get("phone", "")[:32],
            order_total=amount_due,
            payment_method=data.get("payment_method", "")[:40],
            upi_id=data.get("upi_id", "")[:120],
            transaction_id=data.get("transaction_id_utr", "")[:120],
            payment_status=Registration.PaymentStatus.PENDING,
        )
    elif registration.order_total is None and amount_due is not None:
        registration.order_total = amount_due
        registration.save(update_fields=("order_total", "updated_at"))
    _sync_imported_payment_status(registration, data)

    source_metadata = {
        field: data.get(field, "")
        for field in (
            "registration_id",
            "payment_status",
            "verified_by",
            "ticket_status",
            "email_status",
            "scan_status",
            "checked_in_at",
            "checked_in_by",
            "registration_desk",
            "email_sent_at",
            "created_at",
            "last_synced_at",
            "event_id",
            "event_name",
            "registration_created_at",
        )
    }
    ticket = Ticket.objects.create(
        external_ticket_id=data["ticket_id"],
        ticket_number=data["ticket_number"][:80],
        registration=registration,
        ticket_type=data["ticket_type"][:120],
        attendee_name=data["name"][:160],
        attendee_email=data["email"],
        attendee_phone=data.get("phone", "")[:32],
        status=Ticket.Status.PENDING,
        email_status=Ticket.EmailStatus.PENDING,
        source_ticket_generated_at=_timestamp(data, "ticket_generated_at"),
        imported_email_sent_at=_timestamp(data, "email_sent_at"),
        source_updated_at=_timestamp(data, "updated_at"),
        source_metadata=source_metadata,
    )
    return ticket


def _update_imported_ticket(ticket, row):
    data = row["data"]
    if ticket.external_ticket_id and ticket.external_ticket_id != data["ticket_id"]:
        raise ValueError("ticket_id already belongs to a different ticket")
    incoming_updated_at = _timestamp(data, "updated_at")
    database_updated_at = ticket.source_updated_at or ticket.updated_at
    if not incoming_updated_at or (
        database_updated_at and incoming_updated_at <= database_updated_at
    ):
        return False

    if not ticket.external_ticket_id:
        ticket.external_ticket_id = data["ticket_id"][:255]
    ticket.attendee_name = data["name"][:160]
    ticket.attendee_email = data["email"]
    ticket.attendee_phone = data.get("phone", "")[:32]
    ticket.ticket_type = data["ticket_type"][:120]
    ticket.source_updated_at = incoming_updated_at
    ticket.source_ticket_generated_at = _timestamp(data, "ticket_generated_at")
    ticket.imported_email_sent_at = _timestamp(data, "email_sent_at")
    ticket.source_metadata = {
        **ticket.source_metadata,
        "payment_status": data.get("payment_status", ""),
        "ticket_status": data.get("ticket_status", ""),
        "email_status": data.get("email_status", ""),
        "scan_status": data.get("scan_status", ""),
        "checked_in_at": data.get("checked_in_at", ""),
        "checked_in_by": data.get("checked_in_by", ""),
        "registration_desk": data.get("registration_desk", ""),
        "last_synced_at": data.get("last_synced_at", ""),
        "event_id": data.get("event_id", ""),
        "event_name": data.get("event_name", ""),
        "registration_created_at": data.get("registration_created_at", ""),
    }
    ticket.save(
        update_fields=(
            "attendee_name",
            "external_ticket_id",
            "attendee_email",
            "attendee_phone",
            "ticket_type",
            "source_updated_at",
            "source_ticket_generated_at",
            "imported_email_sent_at",
            "source_metadata",
            "updated_at",
        )
    )
    _sync_imported_payment_status(ticket.registration, data)
    return True


@transaction.atomic
def commit_import_batch(batch_id, actor):
    batch = ImportBatch.objects.select_for_update().select_related("event").get(pk=batch_id)
    if batch.requested_by_id != actor.pk:
        raise PermissionError("This import preview belongs to a different administrator.")
    if batch.status == ImportBatch.Status.COMMITTED:
        return batch.result
    if batch.expires_at <= timezone.now():
        raise ValueError("This import preview expired. Upload the spreadsheet again.")

    result = {"created": 0, "updated": 0, "skipped": 0, "rejected": 0, "issued": 0}
    for row in batch.rows:
        if row["action"] == "SKIP":
            result["skipped"] += 1
            continue
        if row["action"] == "REJECT" or row["errors"]:
            result["rejected"] += 1
            continue
        try:
            with transaction.atomic():
                ticket = _ticket_for_row(row["data"])
                row_applied = False
                if ticket:
                    if ticket.external_ticket_id and ticket.external_ticket_id != row["data"]["ticket_id"]:
                        raise ValueError("ticket_id already belongs to a different ticket")
                    if ticket.ticket_number != row["data"]["ticket_number"]:
                        raise ValueError("ticket_id already belongs to a different ticket_number")
                    if ticket.registration.event_id != batch.event_id:
                        raise ValueError("ticket_id is already associated with another event")
                    changed = _update_imported_ticket(ticket, row)
                    result["updated" if changed else "skipped"] += 1
                    row_applied = changed
                else:
                    ticket = _create_imported_ticket(batch, row)
                    result["created"] += 1
                    row_applied = True
                if row_applied and ticket.has_imported_email_verification:
                    if not ticket.source_metadata.get("gatehouse_email_verification_at"):
                        ticket.source_metadata = {
                            **ticket.source_metadata,
                            "gatehouse_email_verification_at": (
                                ticket.imported_email_sent_at.isoformat()
                                if ticket.imported_email_sent_at
                                else ""
                            ),
                        }
                        ticket.save(update_fields=("source_metadata", "updated_at"))
                if row_applied and ticket.status == Ticket.Status.PENDING and ticket.is_eligible_for_issuance:
                    issue_ticket(ticket.pk, actor=actor)
                    result["issued"] += 1
        except (ValueError, IntegrityError) as error:
            row["action"] = "REJECT"
            row["errors"].append(str(error) if isinstance(error, ValueError) else "ticket identifiers conflict with an existing record")
            result["rejected"] += 1

    batch.rows = batch.rows
    batch.status = ImportBatch.Status.COMMITTED
    batch.result = result
    batch.save(update_fields=("rows", "status", "result", "updated_at"))
    AuditLog.objects.create(
        actor=actor,
        action="SPREADSHEET_IMPORT_COMMITTED",
        object_type="ImportBatch",
        object_id=str(batch.pk),
        details=result,
    )
    return result
