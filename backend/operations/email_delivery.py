import base64
import logging
from email.message import EmailMessage
from email.utils import formataddr

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import AuditLog, EmailDeliveryAttempt, Ticket, User
from .sheets_sync import queue_ticket_sync

logger = logging.getLogger(__name__)
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SAFE_DELIVERY_ERROR = "Email delivery failed. Check the Gmail OAuth configuration and provider status."


class EmailDeliveryError(Exception):
    """A safe-to-display email delivery error."""


class EmailDeliveryConflict(EmailDeliveryError):
    """The ticket already has a send in progress or has already been sent."""


def _oauth_credentials():
    values = (
        settings.GMAIL_OAUTH_CLIENT_ID,
        settings.GMAIL_OAUTH_CLIENT_SECRET,
        settings.GMAIL_OAUTH_REFRESH_TOKEN,
        settings.GMAIL_SENDER_EMAIL,
    )
    if not all(value.strip() for value in values):
        raise EmailDeliveryError("Gmail API OAuth is not configured. Set the Gmail OAuth values in .env.")
    credentials = Credentials(
        token=None,
        refresh_token=settings.GMAIL_OAUTH_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GMAIL_OAUTH_CLIENT_ID,
        client_secret=settings.GMAIL_OAUTH_CLIENT_SECRET,
        scopes=[GMAIL_SEND_SCOPE],
    )
    credentials.refresh(Request())
    return credentials


def _message_for_ticket(ticket):
    if ticket.status != Ticket.Status.ISSUED or not ticket.pdf:
        raise EmailDeliveryError("Only issued tickets with a generated PDF can be emailed.")
    event = ticket.registration.event
    message = EmailMessage()
    message["To"] = ticket.attendee_email
    message["From"] = formataddr((settings.GMAIL_SENDER_NAME, settings.GMAIL_SENDER_EMAIL))
    message["Subject"] = f"Your {event.name} ticket — {ticket.ticket_number}"
    message.set_content(
        f"Hello {ticket.attendee_name},\n\n"
        f"Your ticket for {event.name} is attached.\n"
        f"Ticket number: {ticket.ticket_number}\n"
        f"Ticket type: {ticket.ticket_type}\n"
        f"Venue: {event.venue}\n"
        f"{event.instructions}\n\n"
        "Please keep the attached QR ticket private and present it at the entrance.\n"
    )
    with ticket.pdf.open("rb") as pdf_file:
        message.add_attachment(
            pdf_file.read(),
            maintype="application",
            subtype="pdf",
            filename=f"{ticket.ticket_number}.pdf",
        )
    return message


def _send_with_gmail_api(ticket):
    credentials = _oauth_credentials()
    message = _message_for_ticket(ticket)
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    gmail.users().messages().send(userId="me", body={"raw": encoded_message}).execute()


def send_ticket_email(ticket_id, actor: User):
    with transaction.atomic():
        ticket = (
            Ticket.objects.select_for_update()
            .select_related("registration__event")
            .get(pk=ticket_id)
        )
        if ticket.email_status == Ticket.EmailStatus.PROCESSING:
            raise EmailDeliveryConflict("An email attempt is already in progress for this ticket.")
        if ticket.email_status == Ticket.EmailStatus.SENT:
            raise EmailDeliveryConflict("This ticket email was already sent.")
        attempt = EmailDeliveryAttempt.objects.create(
            ticket=ticket,
            status=Ticket.EmailStatus.PROCESSING,
        )
        AuditLog.objects.create(
            actor=actor,
            action="TICKET_EMAIL_REQUESTED",
            object_type="Ticket",
            object_id=str(ticket.pk),
            details={"attempt_id": attempt.pk},
        )
        ticket.email_status = Ticket.EmailStatus.PROCESSING
        ticket.save(update_fields=("email_status", "updated_at"))

    try:
        _send_with_gmail_api(ticket)
    except (EmailDeliveryError, GoogleAuthError, HttpError, OSError, ValueError) as error:
        attempt.status = Ticket.EmailStatus.FAILED
        attempt.error_message = SAFE_DELIVERY_ERROR
        attempt.save(update_fields=("status", "error_message"))
        Ticket.objects.filter(pk=ticket.pk).update(
            email_status=Ticket.EmailStatus.FAILED,
            updated_at=timezone.now(),
        )
        queue_ticket_sync(ticket.pk)
        logger.warning(
            "Gmail API ticket delivery failed for ticket %s (%s).",
            ticket.pk,
            type(error).__name__,
        )
        if isinstance(error, EmailDeliveryError):
            raise
        raise EmailDeliveryError(SAFE_DELIVERY_ERROR) from None
    except Exception as error:
        attempt.status = Ticket.EmailStatus.FAILED
        attempt.error_message = SAFE_DELIVERY_ERROR
        attempt.save(update_fields=("status", "error_message"))
        Ticket.objects.filter(pk=ticket.pk).update(
            email_status=Ticket.EmailStatus.FAILED,
            updated_at=timezone.now(),
        )
        queue_ticket_sync(ticket.pk)
        logger.error(
            "Unexpected Gmail API delivery failure for ticket %s (%s).",
            ticket.pk,
            type(error).__name__,
        )
        raise EmailDeliveryError(SAFE_DELIVERY_ERROR) from None

    attempt.status = Ticket.EmailStatus.SENT
    attempt.save(update_fields=("status",))
    Ticket.objects.filter(pk=ticket.pk).update(
        email_status=Ticket.EmailStatus.SENT,
        updated_at=timezone.now(),
    )
    queue_ticket_sync(ticket.pk)
    return attempt
