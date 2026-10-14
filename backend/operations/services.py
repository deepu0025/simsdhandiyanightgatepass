import hashlib
import io
import secrets

import qrcode
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .models import AuditLog, CheckIn, Event, Ticket
from .sheets_sync import queue_ticket_sync


def _build_pdf(ticket, credential):
    event = ticket.registration.event
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"Event ticket {ticket.ticket_number}")
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(48, 740, event.name[:70])
    pdf.setFont("Helvetica", 12)
    pdf.drawString(48, 700, f"Attendee: {ticket.attendee_name[:80]}")
    pdf.drawString(48, 678, f"Ticket: {ticket.ticket_number}")
    pdf.drawString(48, 656, f"Type: {ticket.ticket_type[:80]}")
    pdf.drawString(48, 634, f"Venue: {event.venue[:100]}")
    pdf.drawString(48, 612, f"Date: {timezone.localtime(event.starts_at).strftime('%d %b %Y, %I:%M %p')}")
    if event.instructions:
        text = pdf.beginText(48, 575)
        text.setFont("Helvetica", 10)
        for line in event.instructions.splitlines()[:8]:
            text.textLine(line[:120])
        pdf.drawText(text)
    qr = qrcode.make(credential)
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, format="PNG")
    pdf.drawImage(ImageReader(io.BytesIO(qr_buffer.getvalue())), 400, 500, width=145, height=145)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(48, 70, "Present this ticket at the entrance. Keep the QR code private.")
    pdf.save()
    return buffer.getvalue()


@transaction.atomic
def issue_ticket(ticket_id, actor=None):
    ticket = Ticket.objects.select_for_update().select_related("registration__event").get(pk=ticket_id)
    if ticket.status in {Ticket.Status.CANCELLED, Ticket.Status.REVOKED}:
        raise ValueError("Cancelled or revoked tickets cannot be reissued.")
    if ticket.status == Ticket.Status.ISSUED or ticket.qr_token_hash:
        if ticket.status == Ticket.Status.ISSUED and ticket.pdf and ticket.qr_token_hash:
            return ticket, None
        raise ValueError("This ticket was previously issued; its QR credential cannot be rotated.")
    if not ticket.is_eligible_for_issuance:
        raise ValueError(
            "Import a verified payment status or qualifying sent-email evidence from the spreadsheet before issuing this ticket."
        )
    credential = secrets.token_urlsafe(32)
    ticket.qr_token_hash = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    ticket.status = Ticket.Status.ISSUED
    ticket.generated_at = timezone.now()
    ticket.pdf.save(
        f"{ticket.ticket_number}.pdf",
        ContentFile(_build_pdf(ticket, credential)),
        save=False,
    )
    ticket.save(update_fields=("qr_token_hash", "status", "generated_at", "pdf", "updated_at"))
    AuditLog.objects.create(
        actor=actor,
        action="TICKET_ISSUED",
        object_type="Ticket",
        object_id=str(ticket.pk),
        details=(
            {
                "authorization_basis": "IMPORTED_PAYMENT_STATUS",
                "payment_status": ticket.registration.imported_payment_status,
                "verified_by": ticket.registration.imported_verified_by,
                "verified_at": (
                    ticket.registration.imported_verified_at.isoformat()
                    if ticket.registration.imported_verified_at
                    else ""
                ),
            }
            if ticket.has_imported_payment_verification
            else {
                "authorization_basis": "IMPORTED_EMAIL_SENT",
                "imported_email_sent_at": ticket.imported_email_verification_timestamp,
            }
        ),
    )
    queue_ticket_sync(ticket.pk)
    return ticket, credential


def _find_ticket_by_identifier(identifier, *, for_update=False):
    if not isinstance(identifier, str):
        return None
    value = identifier.strip()
    if not value:
        return None
    tickets = Ticket.objects.select_related("registration")
    if for_update:
        tickets = tickets.select_for_update()
    for field in ("ticket_number", "external_ticket_id"):
        ticket = tickets.filter(**{field: value}).first()
        if ticket:
            return ticket
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    try:
        return tickets.get(qr_token_hash=digest)
    except Ticket.DoesNotExist:
        return None


def validate_ticket_credential(credential):
    ticket = _find_ticket_by_identifier(credential)
    if ticket is None:
        return "INVALID", None

    if ticket.status not in {Ticket.Status.ISSUED}:
        return "CANCELLED", ticket
    if ticket.checked_in_at:
        return "ALREADY_USED", ticket
    if not ticket.is_eligible_for_issuance:
        return "PAYMENT_NOT_VERIFIED", ticket
    return "VALID", ticket


def _record_scan_attempt(scanner, credential, *, result=None, ticket=None, gate="", method=CheckIn.Method.QR, reason=""):
    if result is None:
        result = CheckIn.Result.INVALID
    normalized_gate = (gate or "")[:100]
    normalized_reason = reason[:500]
    payload = {
        "scanner": scanner,
        "gate": normalized_gate,
        "result": result,
        "method": method,
    }
    if ticket is not None:
        payload["ticket"] = ticket
    if normalized_reason:
        payload["reason"] = normalized_reason
    return CheckIn.objects.create(**payload)


@transaction.atomic
def check_in_ticket(credential, scanner, gate=""):
    ticket = _find_ticket_by_identifier(credential, for_update=True)
    if ticket is None:
        _record_scan_attempt(scanner, credential, result=CheckIn.Result.INVALID, gate=gate)
        return CheckIn.Result.INVALID, None
    if ticket.status != Ticket.Status.ISSUED:
        result = CheckIn.Result.CANCELLED
    elif ticket.checked_in_at:
        result = CheckIn.Result.ALREADY_USED
    else:
        result = CheckIn.Result.ACCEPTED
        ticket.checked_in_at = timezone.now()
        ticket.checked_in_by = scanner
        ticket.save(update_fields=("checked_in_at", "checked_in_by", "updated_at"))
    _record_scan_attempt(scanner, credential, ticket=ticket, result=result, gate=gate)
    if result == CheckIn.Result.ACCEPTED:
        queue_ticket_sync(ticket.pk)
    return result, ticket


@transaction.atomic
def manual_check_in_ticket(ticket_id, actor, reason):
    ticket = (
        Ticket.objects.select_for_update()
        .select_related("registration")
        .get(pk=ticket_id)
    )
    if ticket.checked_in_at:
        result = CheckIn.Result.ALREADY_USED
    elif ticket.status in {Ticket.Status.CANCELLED, Ticket.Status.REVOKED}:
        result = CheckIn.Result.CANCELLED
    else:
        result = CheckIn.Result.ACCEPTED
        ticket.checked_in_at = timezone.now()
        ticket.checked_in_by = actor
        ticket.save(update_fields=("checked_in_at", "checked_in_by", "updated_at"))

    CheckIn.objects.create(
        ticket=ticket,
        scanner=actor,
        gate="Manual",
        result=result,
        method=CheckIn.Method.MANUAL,
        reason=reason,
    )
    AuditLog.objects.create(
        actor=actor,
        action="TICKET_MANUAL_CHECK_IN" if result == CheckIn.Result.ACCEPTED
        else "TICKET_MANUAL_CHECK_IN_REJECTED",
        object_type="Ticket",
        object_id=str(ticket.pk),
        details={
            "reason": reason,
            "result": result,
            "ticket_status": ticket.status,
            "payment_status": ticket.registration.payment_status,
        },
    )
    if result == CheckIn.Result.ACCEPTED:
        queue_ticket_sync(ticket.pk)
    return result, ticket
