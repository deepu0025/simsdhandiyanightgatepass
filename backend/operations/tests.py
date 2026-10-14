import base64
import hashlib
import os
import tempfile
from decimal import Decimal
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from io import BytesIO
from unittest.mock import MagicMock
from unittest.mock import patch

from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    AuditLog,
    CheckIn,
    Event,
    GoogleSheetImportState,
    ImportBatch,
    Registration,
    SpreadsheetSyncJob,
    Ticket,
    User,
)
from .email_delivery import (
    EmailDeliveryError,
    _message_for_ticket,
    _send_with_gmail_api,
    send_ticket_email,
)
from .services import check_in_ticket, issue_ticket, manual_check_in_ticket
from .sheets_sync import (
    SAFE_SYNC_ERROR,
    SHEET_COLUMNS,
    _ticket_row,
    process_sync_jobs,
    read_worksheet_rows,
    sync_ticket,
)


class BootstrapAdminCommandTests(TestCase):
    def test_bootstrap_creates_configured_admin_and_is_idempotent(self):
        password = "b2dc7a8e1f0c4d5a8b3e6f9c2a4d7e0f"
        with patch.dict(
            os.environ,
            {
                "BOOTSTRAP_ADMIN_EMAIL": "admin@sims.in",
                "BOOTSTRAP_ADMIN_PASSWORD": password,
                "BOOTSTRAP_ADMIN_RESET_PASSWORD": "false",
            },
        ):
            call_command("bootstrap_admin", verbosity=0)
            admin = User.objects.get(email="admin@sims.in")
            self.assertEqual(admin.role, User.Role.ADMIN)
            self.assertTrue(admin.is_superuser)
            self.assertTrue(admin.check_password(password))

            admin.set_password("Manually-changed-password-123!")
            admin.save(update_fields=("password",))
            call_command("bootstrap_admin", verbosity=0)

        admin.refresh_from_db()
        self.assertTrue(admin.check_password("Manually-changed-password-123!"))


class TicketOperationsTests(TestCase):
    def setUp(self):
        self.tmp_media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.tmp_media.name)
        self.settings_override.enable()
        self.admin = User.objects.create_user(
            "admin@example.test", "Secure-test-password-123!", name="Admin", role=User.Role.ADMIN
        )
        self.scanner = User.objects.create_user(
            "scanner@example.test",
            "Secure-test-password-123!",
            name="Scanner",
            role=User.Role.SCANNER,
            gate="GATE_4",
        )
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(
            name="Test Night", starts_at=start, ends_at=start + timedelta(hours=3), venue="Hall"
        )
        self.registration = Registration.objects.create(
            event=self.event,
            source=Registration.Source.ON_SPOT,
            buyer_name="A. Guest",
            buyer_email="guest@example.test",
            payment_method="CASH",
            payment_status=Registration.PaymentStatus.PAID,
            imported_payment_status="VERIFIED",
            created_by=self.admin,
        )
        self.ticket = Ticket.objects.create(
            ticket_number="EVT-0001",
            registration=self.registration,
            ticket_type="General",
            attendee_name="A. Guest",
            attendee_email="guest@example.test",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.tmp_media.cleanup()

    def test_issue_generates_pdf_and_random_qr_credential_only_once(self):
        ticket, credential = issue_ticket(self.ticket.pk, self.admin)
        self.assertEqual(ticket.status, Ticket.Status.ISSUED)
        self.assertTrue(ticket.pdf.name.endswith(".pdf"))
        self.assertEqual(ticket.qr_token_hash, hashlib.sha256(credential.encode()).hexdigest())
        same_ticket, no_new_credential = issue_ticket(ticket.pk, self.admin)
        self.assertEqual(same_ticket.pk, ticket.pk)
        self.assertIsNone(no_new_credential)

    def test_unpaid_registration_cannot_issue(self):
        self.registration.payment_status = Registration.PaymentStatus.PENDING
        self.registration.imported_payment_status = ""
        self.registration.save(update_fields=("payment_status", "imported_payment_status"))
        with self.assertRaisesMessage(ValueError, "Import a verified payment status"):
            issue_ticket(self.ticket.pk, self.admin)

    def test_local_payment_status_does_not_authorize_ticket_issuance(self):
        self.registration.payment_status = Registration.PaymentStatus.PAID
        self.registration.imported_payment_status = ""
        self.registration.save(update_fields=("payment_status", "imported_payment_status"))
        with self.assertRaisesMessage(ValueError, "Import a verified payment status"):
            issue_ticket(self.ticket.pk, self.admin)
        sheet_row = dict(zip(SHEET_COLUMNS, _ticket_row(self.ticket, timezone.now())))
        self.assertEqual(sheet_row["payment_status"], Registration.PaymentStatus.PENDING)

    def test_scanner_does_not_recheck_payment_after_ticket_is_issued(self):
        _ticket, credential = issue_ticket(self.ticket.pk, self.admin)
        self.registration.imported_payment_status = ""
        self.registration.payment_status = Registration.PaymentStatus.PENDING
        self.registration.save(update_fields=("imported_payment_status", "payment_status"))
        result, scanned_ticket = check_in_ticket(credential, self.scanner)
        self.assertEqual(result, CheckIn.Result.ACCEPTED)
        self.assertEqual(scanned_ticket.pk, self.ticket.pk)

    def test_revoked_or_partially_issued_ticket_cannot_rotate_qr(self):
        self.ticket.status = Ticket.Status.REVOKED
        self.ticket.save(update_fields=("status",))
        with self.assertRaisesMessage(ValueError, "cannot be reissued"):
            issue_ticket(self.ticket.pk, self.admin)

    def test_ticket_email_uses_oauth_sender_and_records_sent_attempt(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        with patch("operations.email_delivery._send_with_gmail_api") as send:
            attempt = send_ticket_email(ticket.pk, self.admin)

        ticket.refresh_from_db()
        self.assertEqual(ticket.email_status, Ticket.EmailStatus.SENT)
        self.assertEqual(attempt.status, Ticket.EmailStatus.SENT)
        self.assertEqual(ticket.email_attempts.count(), 1)
        send.assert_called_once()
        with self.assertRaisesMessage(EmailDeliveryError, "already sent"):
            send_ticket_email(ticket.pk, self.admin)
        self.assertEqual(ticket.email_attempts.count(), 1)

    def test_ticket_email_failure_is_recorded_without_leaking_provider_details(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        with patch(
            "operations.email_delivery._send_with_gmail_api",
            side_effect=RuntimeError("private OAuth response body"),
        ):
            with self.assertRaisesMessage(EmailDeliveryError, "Email delivery failed"):
                send_ticket_email(ticket.pk, self.admin)

        ticket.refresh_from_db()
        attempt = ticket.email_attempts.get()
        self.assertEqual(ticket.email_status, Ticket.EmailStatus.FAILED)
        self.assertEqual(attempt.status, Ticket.EmailStatus.FAILED)
        self.assertNotIn("private OAuth response body", attempt.error_message)

    @override_settings(
        GMAIL_SENDER_EMAIL="tickets@example.test",
        GMAIL_SENDER_NAME="Test Operations",
    )
    def test_ticket_email_contains_pdf_and_ticket_instructions(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        message = _message_for_ticket(ticket)
        self.assertEqual(message["To"], ticket.attendee_email)
        self.assertIn(ticket.ticket_number, message["Subject"])
        self.assertIn("Test Night", message.get_body(preferencelist=("plain",)).get_content())
        self.assertEqual(message.get_payload()[1].get_content_type(), "application/pdf")

    @override_settings(
        GMAIL_SENDER_EMAIL="tickets@example.test",
        GMAIL_SENDER_NAME="Test Operations",
    )
    def test_gmail_api_receives_oauth_message_with_pdf_attachment(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        oauth_credentials = object()
        with (
            patch("operations.email_delivery.build") as gmail,
            patch("operations.email_delivery._oauth_credentials", return_value=oauth_credentials),
        ):
            _send_with_gmail_api(ticket)

        gmail.assert_called_once_with(
            "gmail",
            "v1",
            credentials=oauth_credentials,
            cache_discovery=False,
        )
        send_call = gmail.return_value.users.return_value.messages.return_value.send
        send_call.assert_called_once()
        raw = send_call.call_args.kwargs["body"]["raw"]
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        message = BytesParser(policy=policy.default).parsebytes(decoded)
        self.assertEqual(message["To"], ticket.attendee_email)
        self.assertEqual(message.get_body(("plain",)).get_content_type(), "text/plain")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")

    @override_settings(
        GMAIL_OAUTH_CLIENT_ID="",
        GMAIL_OAUTH_CLIENT_SECRET="",
        GMAIL_OAUTH_REFRESH_TOKEN="",
        GMAIL_SENDER_EMAIL="",
    )
    def test_ticket_email_reports_unconfigured_oauth_without_faking_delivery(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        with self.assertRaisesMessage(EmailDeliveryError, "Gmail API OAuth is not configured"):
            send_ticket_email(ticket.pk, self.admin)

        ticket.refresh_from_db()
        self.assertEqual(ticket.email_status, Ticket.EmailStatus.FAILED)
        self.assertEqual(ticket.email_attempts.get().status, Ticket.EmailStatus.FAILED)

    def test_ticket_email_request_requires_admin(self):
        client = APIClient()
        client.force_authenticate(self.scanner)
        response = client.post(f"/api/admin/tickets/{self.ticket.pk}/email/")
        self.assertEqual(response.status_code, 403)

    def test_only_one_scan_succeeds(self):
        ticket, credential = issue_ticket(self.ticket.pk, self.admin)
        first, _ = check_in_ticket(credential, self.scanner, "North")
        second, _ = check_in_ticket(credential, self.scanner, "South")
        self.assertEqual(first, CheckIn.Result.ACCEPTED)
        self.assertEqual(second, CheckIn.Result.ALREADY_USED)
        self.assertEqual(ticket.scan_events.filter(result=CheckIn.Result.ACCEPTED).count(), 1)

    def test_invalid_scan_is_recorded_without_server_error(self):
        client = APIClient()
        client.force_authenticate(self.scanner)

        response = client.post(
            "/api/scanner/scan/",
            {"credential": "x" * 32},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["result"], CheckIn.Result.INVALID)
        invalid_attempt = CheckIn.objects.get(result=CheckIn.Result.INVALID)
        self.assertEqual(invalid_attempt.method, CheckIn.Method.QR)
        self.assertEqual(invalid_attempt.scanner, self.scanner)

    def test_scanner_checks_in_ticket_using_short_external_ticket_id(self):
        self.ticket.external_ticket_id = "sheet-ticket-1"
        self.ticket.status = Ticket.Status.ISSUED
        self.ticket.save(update_fields=("external_ticket_id", "status"))
        client = APIClient()
        client.force_authenticate(self.scanner)

        response = client.post(
            "/api/scanner/scan/",
            {"credential": self.ticket.external_ticket_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], CheckIn.Result.ACCEPTED)
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.checked_in_at)
        self.assertEqual(self.ticket.scan_events.get().result, CheckIn.Result.ACCEPTED)

    def test_validation_only_reports_valid_without_checking_in(self):
        self.ticket.external_ticket_id = "sheet-ticket-pending-confirmation"
        self.ticket.status = Ticket.Status.ISSUED
        self.ticket.save(update_fields=("external_ticket_id", "status"))
        client = APIClient()
        client.force_authenticate(self.scanner)

        response = client.post(
            "/api/scanner/validate/",
            {"credential": self.ticket.external_ticket_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], "VALID")
        self.assertEqual(response.data["ticket_number"], self.ticket.ticket_number)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.checked_in_at)
        self.assertFalse(self.ticket.scan_events.exists())

    def test_admin_can_manually_check_in_pending_unissued_ticket_with_audited_reason(self):
        self.registration.payment_status = Registration.PaymentStatus.PENDING
        self.registration.save(update_fields=("payment_status",))
        client = APIClient()
        client.force_authenticate(self.admin)

        response = client.post(
            f"/api/admin/tickets/{self.ticket.pk}/manual-check-in/",
            {"reason": "Guest confirmed at the registration desk."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], CheckIn.Result.ACCEPTED)
        self.assertEqual(response.data["ticket"]["checkin_method"], CheckIn.Method.MANUAL)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.PENDING)
        self.assertFalse(self.ticket.qr_token_hash)
        self.assertIsNotNone(self.ticket.checked_in_at)
        checkin = self.ticket.scan_events.get()
        self.assertEqual(checkin.method, CheckIn.Method.MANUAL)
        self.assertEqual(checkin.reason, "Guest confirmed at the registration desk.")
        self.assertEqual(checkin.scanner, self.admin)
        audit = AuditLog.objects.get(action="TICKET_MANUAL_CHECK_IN", object_id=str(self.ticket.pk))
        self.assertEqual(audit.actor, self.admin)
        self.assertEqual(audit.details["reason"], checkin.reason)
        self.assertEqual(audit.details["payment_status"], Registration.PaymentStatus.PENDING)

    def test_manual_check_in_requires_admin_and_a_reason(self):
        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        path = f"/api/admin/tickets/{self.ticket.pk}/manual-check-in/"
        denied = scanner_client.post(path, {"reason": "At gate"}, format="json")
        self.assertEqual(denied.status_code, 403)

        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        missing_reason = admin_client.post(path, {"reason": "  "}, format="json")
        self.assertEqual(missing_reason.status_code, 400)
        self.assertFalse(self.ticket.scan_events.exists())

    def test_manual_check_in_cannot_repeat_or_override_revocation(self):
        first, _ = manual_check_in_ticket(self.ticket.pk, self.admin, "Approved at gate.")
        second, _ = manual_check_in_ticket(self.ticket.pk, self.admin, "Tried again.")
        self.assertEqual(first, CheckIn.Result.ACCEPTED)
        self.assertEqual(second, CheckIn.Result.ALREADY_USED)
        self.assertEqual(
            self.ticket.scan_events.filter(result=CheckIn.Result.ACCEPTED).count(),
            1,
        )

        revoked = Ticket.objects.create(
            ticket_number="EVT-REVOKED",
            registration=self.registration,
            ticket_type="General",
            attendee_name="Revoked Guest",
            attendee_email="revoked@example.test",
            status=Ticket.Status.REVOKED,
        )
        result, _ = manual_check_in_ticket(revoked.pk, self.admin, "Attempted at gate.")
        self.assertEqual(result, CheckIn.Result.CANCELLED)
        revoked.refresh_from_db()
        self.assertIsNone(revoked.checked_in_at)
        self.assertEqual(
            revoked.scan_events.get().result,
            CheckIn.Result.CANCELLED,
        )

    def test_scanner_cannot_access_admin_dashboard(self):
        client = APIClient()
        client.force_authenticate(self.scanner)
        self.assertEqual(client.get("/api/admin/dashboard/").status_code, 403)

    def test_admin_can_create_and_activate_event_in_application(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        starts_at = timezone.now() + timedelta(days=7)
        response = client.post(
            "/api/admin/events/",
            {
                "name": "Launch Event",
                "starts_at": starts_at.isoformat(),
                "ends_at": (starts_at + timedelta(hours=3)).isoformat(),
                "venue": "Main Hall",
                "instructions": "Use the north entrance.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["is_active"])
        self.assertEqual(Event.objects.get(pk=response.data["id"]).name, "Launch Event")
        self.assertTrue(
            AuditLog.objects.filter(action="EVENT_CREATED", object_id=response.data["id"]).exists()
        )
        self.assertEqual(client.get("/api/admin/events/").status_code, 200)

    def test_event_creation_requires_admin_and_valid_time_range(self):
        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        starts_at = timezone.now() + timedelta(days=7)
        payload = {
            "name": "Unauthorized Event",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
            "venue": "Main Hall",
            "instructions": "",
        }
        self.assertEqual(scanner_client.post("/api/admin/events/", payload, format="json").status_code, 403)

        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        payload["ends_at"] = (starts_at - timedelta(hours=1)).isoformat()
        invalid = admin_client.post("/api/admin/events/", payload, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(Event.objects.filter(name="Unauthorized Event").exists())

    def test_admin_creates_gate_scanner_and_gate_is_bound_to_account(self):
        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        created = admin_client.post(
            "/api/admin/users/",
            {
                "email": "gate-one@example.test",
                "name": "Gate One",
                "password": "Unique-gate-password-123!",
                "gate": "GATE_1",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["gate"], "GATE_1")
        self.assertTrue(created.data["is_active"])

        duplicate_gate = admin_client.post(
            "/api/admin/users/",
            {
                "email": "another-gate-one@example.test",
                "name": "Another Scanner",
                "password": "Another-gate-password-123!",
                "gate": "GATE_1",
            },
            format="json",
        )
        self.assertEqual(duplicate_gate.status_code, 409)

        scanner = User.objects.get(email="gate-one@example.test")
        _ticket, credential = issue_ticket(self.ticket.pk, self.admin)
        scanner_client = APIClient()
        scanner_client.force_authenticate(scanner)
        scan = scanner_client.post(
            "/api/scanner/scan/",
            {"credential": credential, "gate": "GATE_2"},
            format="json",
        )
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(self.ticket.scan_events.get().gate, "GATE_1")

        reset = admin_client.patch(
            f"/api/admin/users/{scanner.pk}/",
            {"password": "Reset-gate-password-123!"},
            format="json",
        )
        self.assertEqual(reset.status_code, 200)
        scanner.refresh_from_db()
        self.assertTrue(scanner.check_password("Reset-gate-password-123!"))

    def test_login_requires_csrf_and_sets_authenticated_session(self):
        client = APIClient(enforce_csrf_checks=True)
        token = client.get("/api/csrf/").json()["csrfToken"]
        rejected = client.post(
            "/api/auth/login/",
            {"email": self.admin.email, "password": "Secure-test-password-123!"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 403)
        accepted = client.post(
            "/api/auth/login/",
            {"email": self.admin.email, "password": "Secure-test-password-123!"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(client.get("/api/auth/me/").data["role"], User.Role.ADMIN)
        refreshed_token = client.get("/api/csrf/").json()["csrfToken"]
        scan = client.post(
            "/api/scanner/scan/",
            {"credential": "x" * 32},
            format="json",
            HTTP_X_CSRFTOKEN=refreshed_token,
        )
        self.assertEqual(scan.status_code, 409)
        self.assertEqual(scan.data["result"], CheckIn.Result.INVALID)

    def test_current_user_returns_an_explicit_anonymous_state(self):
        response = APIClient().get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"authenticated": False})

    def test_on_spot_ticket_pdf_and_scanner_check_in_end_to_end(self):
        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        issued_credentials = []

        payload = {
            "event_id": str(self.event.pk),
            "attendee_name": "Gate Guest",
            "attendee_email": "gate-guest@example.test",
            "ticket_type": "General",
            "ticket_number": "EVT-ONSPOT-1",
            "payment_method": "CASH",
            "amount": "250.00",
        }
        response = admin_client.post(
            "/api/admin/registrations/on-spot/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="onsite-test-registration-1",
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["issued"])
        self.assertEqual(response.data["ticket"]["status"], Ticket.Status.PENDING)

        replay = admin_client.post(
            "/api/admin/registrations/on-spot/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="onsite-test-registration-1",
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(Ticket.objects.filter(ticket_number="EVT-ONSPOT-1").count(), 1)
        changed_replay = admin_client.post(
            "/api/admin/registrations/on-spot/",
            {**payload, "amount": "251.00"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="onsite-test-registration-1",
        )
        self.assertEqual(changed_replay.status_code, 409)

        updated_at = (timezone.now() + timedelta(seconds=1)).isoformat()
        row = (
            "ticket_id,ticket_number,ticket_type,name,email,payment_status,verified_by,updated_at\n"
            f"{response.data['ticket']['id']},EVT-ONSPOT-1,General,Gate Guest,gate-guest@example.test,VERIFIED,admin@example.test,{updated_at}\n"
        )
        preview = self._preview_csv(admin_client, row.encode())

        def issue_and_capture(ticket_id, actor=None):
            ticket, credential = issue_ticket(ticket_id, actor=actor)
            issued_credentials.append(credential)
            return ticket, credential

        with patch("operations.import_commit.issue_ticket", side_effect=issue_and_capture):
            committed = admin_client.post(
                f"/api/admin/import/{preview.data['batch_id']}/commit/",
                {"confirm": True},
                format="json",
            )
        self.assertEqual(committed.data["issued"], 1)
        self.assertEqual(Ticket.objects.filter(ticket_number="EVT-ONSPOT-1").count(), 1)
        self.assertNotIn("qr_credential", committed.data)
        pdf_url = f"/api/tickets/{response.data['ticket']['id']}/pdf/"
        pdf = admin_client.get(pdf_url)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        pdf.close()

        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        first_scan = scanner_client.post(
            "/api/scanner/scan/",
            {"credential": issued_credentials[0], "gate": "North"},
            format="json",
        )
        second_scan = scanner_client.post(
            "/api/scanner/scan/",
            {"credential": issued_credentials[0], "gate": "South"},
            format="json",
        )
        self.assertEqual(first_scan.status_code, 200)
        self.assertEqual(first_scan.data["result"], CheckIn.Result.ACCEPTED)
        self.assertEqual(second_scan.status_code, 409)
        self.assertEqual(second_scan.data["result"], CheckIn.Result.ALREADY_USED)

    def test_on_spot_registration_cannot_approve_payment_inside_app(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            "/api/admin/registrations/on-spot/",
            {
                "event_id": str(self.event.pk),
                "attendee_name": "Pending Guest",
                "attendee_email": "pending@example.test",
                "ticket_type": "General",
                "payment_method": "CASH",
                "payment_confirmed": True,
                "amount": "250.00",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="pending-onsite-test",
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["issued"])
        ticket_id = response.data["ticket"]["id"]
        self.assertEqual(
            client.post(f"/api/admin/tickets/{ticket_id}/issue/").status_code,
            409,
        )
        registration_id = response.data["registration_id"]
        self.assertEqual(
            client.post(
            f"/api/admin/registrations/{registration_id}/payment/",
            {"decision": "REJECTED"},
            format="json",
            ).status_code,
            404,
        )

    def test_admin_revocation_is_audited_and_blocks_future_scans_and_downloads(self):
        ticket, credential = issue_ticket(self.ticket.pk, self.admin)
        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        response = admin_client.post(
            f"/api/admin/tickets/{ticket.pk}/status/",
            {"status": Ticket.Status.REVOKED, "reason": "Payment reversal"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Ticket.Status.REVOKED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="TICKET_REVOKED",
                object_id=str(ticket.pk),
                actor=self.admin,
            ).exists()
        )
        replay = admin_client.post(
            f"/api/admin/tickets/{ticket.pk}/status/",
            {"status": Ticket.Status.REVOKED},
            format="json",
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            admin_client.get(f"/api/tickets/{ticket.pk}/pdf/").status_code,
            404,
        )
        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        scan = scanner_client.post(
            "/api/scanner/scan/",
            {"credential": credential},
            format="json",
        )
        self.assertEqual(scan.status_code, 409)
        self.assertEqual(scan.data["result"], CheckIn.Result.CANCELLED)
        self.assertEqual(
            scanner_client.post(
                f"/api/admin/tickets/{ticket.pk}/status/",
                {"status": Ticket.Status.CANCELLED},
                format="json",
            ).status_code,
            403,
        )

    def test_import_preview_detects_duplicate_records_and_does_not_issue(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,payment_status\n"
            "e51b5053-7b79-4c18-9012-458359900001,EVT-X,,General,Guest,guest@example.test,PENDING\n"
            "e51b5053-7b79-4c18-9012-458359900001,EVT-X,,General,Guest,guest@example.test,PENDING\n"
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self._preview_csv(client, csv_content.encode())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["total_rows"], 2)
        self.assertEqual(response.data["created"], 1)
        self.assertEqual(response.data["rejected"], 1)
        self.assertTrue(response.data["commit_available"])
        commit = client.post(
            f"/api/admin/import/{response.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(commit.status_code, 200)
        self.assertEqual(commit.data["created"], 1)
        self.assertEqual(Ticket.objects.count(), 2)
        imported = Ticket.objects.get(external_ticket_id="e51b5053-7b79-4c18-9012-458359900001")
        self.assertEqual(imported.status, Ticket.Status.PENDING)
        self.assertFalse(imported.qr_token_hash)

    def _preview_csv(self, client, content, filename="tickets.csv"):
        return client.post(
            "/api/admin/import/preview/",
            {
                "event_id": str(self.event.pk),
                "file": SimpleUploadedFile(filename, content, content_type="text/csv"),
            },
            format="multipart",
        )

    def test_import_requires_confirmation_groups_orders_and_is_idempotent(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,amount_due,payment_status,updated_at\n"
            "legacy-1,LEGACY-1,order-1,General,Guest One,one@example.test,500,PENDING,2026-10-01T10:00:00+05:30\n"
            "legacy-2,LEGACY-2,order-1,General,Guest Two,two@example.test,500,PENDING,2026-10-01T10:00:00+05:30\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        self.assertEqual(preview.status_code, 201)
        self.assertEqual(preview.data["created"], 2)
        self.assertTrue(preview.data["commit_available"])
        batch_id = preview.data["batch_id"]
        self.assertEqual(
            client.post(f"/api/admin/import/{batch_id}/commit/", {}, format="json").status_code,
            400,
        )
        commit = client.post(
            f"/api/admin/import/{batch_id}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(commit.status_code, 200)
        self.assertEqual(commit.data["created"], 2)
        registration = Registration.objects.get(external_id__contains="order-1")
        self.assertEqual(registration.order_total, Decimal("500.00"))
        self.assertEqual(registration.payment_status, Registration.PaymentStatus.PENDING)
        imported = list(Ticket.objects.filter(registration=registration))
        self.assertEqual(len(imported), 2)
        self.assertTrue(all(ticket.status == Ticket.Status.PENDING for ticket in imported))
        self.assertTrue(all(not ticket.qr_token_hash and not ticket.pdf for ticket in imported))

        replay = client.post(
            f"/api/admin/import/{batch_id}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(replay.data["created"], 2)
        self.assertEqual(Ticket.objects.count(), 3)

        second_preview = self._preview_csv(client, csv_content.encode())
        second_commit = client.post(
            f"/api/admin/import/{second_preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(second_commit.data["skipped"], 2)
        self.assertEqual(Ticket.objects.filter(registration=registration).count(), 2)

    def test_imported_sheet_payment_status_controls_ticket_issuance(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,payment_status,verified_by,verified_at,updated_at\n"
            "verified-ticket,VERIFIED-1,verified-order,General,Verified Guest,verified@example.test,VERIFIED,sheet-admin,2026-10-12T10:00:00+00:00,2026-10-12T10:00:00+00:00\n"
            "rejected-ticket,REJECTED-1,rejected-order,General,Rejected Guest,rejected@example.test,REJECTED,sheet-admin,2026-10-12T10:00:00+00:00,2026-10-12T10:00:00+00:00\n"
            "pending-ticket,PENDING-1,pending-order,General,Pending Guest,pending@example.test,PENDING,,,2026-10-12T10:00:00+00:00\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        self.assertEqual(preview.data["verified_payment_rows"], 1)
        self.assertTrue(preview.data["rows"][0]["verified_payment"])
        response = client.post(
            f"/api/admin/import/{preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["issued"], 1)
        verified = Ticket.objects.get(external_ticket_id="verified-ticket")
        rejected = Ticket.objects.get(external_ticket_id="rejected-ticket")
        pending = Ticket.objects.get(external_ticket_id="pending-ticket")
        verified.registration.refresh_from_db()
        rejected.registration.refresh_from_db()
        pending.registration.refresh_from_db()
        self.assertEqual(verified.registration.payment_status, Registration.PaymentStatus.VERIFIED)
        self.assertEqual(verified.registration.imported_payment_status, "VERIFIED")
        self.assertEqual(verified.status, Ticket.Status.ISSUED)
        self.assertEqual(rejected.registration.payment_status, Registration.PaymentStatus.REJECTED)
        self.assertEqual(rejected.status, Ticket.Status.PENDING)
        self.assertEqual(pending.registration.payment_status, Registration.PaymentStatus.PENDING)
        self.assertEqual(pending.status, Ticket.Status.PENDING)
        audit = AuditLog.objects.get(action="TICKET_ISSUED", object_id=str(verified.pk))
        self.assertEqual(audit.details["authorization_basis"], "IMPORTED_PAYMENT_STATUS")
        self.assertEqual(audit.details["verified_by"], "sheet-admin")

    def test_imported_sent_email_evidence_issues_only_that_ticket_with_gatehouse_qr(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,payment_status,email_status,email_sent_at\n"
            "sent-ticket-1,SENT-1,shared-order,General,Sent Guest,sent@example.test,PENDING,SENT,2026-10-12T10:00:00+00:00\n"
            "pending-ticket-2,PENDING-2,shared-order,General,Pending Guest,pending@example.test,PENDING,PENDING,\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        self.assertEqual(preview.status_code, 201)
        self.assertEqual(preview.data["email_evidence_rows"], 1)
        self.assertTrue(preview.data["rows"][0]["email_delivery_evidence"])
        credentials = []
        original_issue = issue_ticket

        def issue_and_capture(ticket_id, actor=None):
            ticket, credential = original_issue(ticket_id, actor=actor)
            credentials.append(credential)
            return ticket, credential

        with patch("operations.import_commit.issue_ticket", side_effect=issue_and_capture):
            response = client.post(
                f"/api/admin/import/{preview.data['batch_id']}/commit/",
                {"confirm": True},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["created"], 2)
        self.assertEqual(response.data["issued"], 1)
        registration = Registration.objects.get(external_id__contains="shared-order")
        self.assertEqual(registration.payment_status, Registration.PaymentStatus.PENDING)
        sent_ticket = Ticket.objects.get(external_ticket_id="sent-ticket-1")
        pending_ticket = Ticket.objects.get(external_ticket_id="pending-ticket-2")
        self.assertEqual(sent_ticket.status, Ticket.Status.ISSUED)
        self.assertTrue(sent_ticket.qr_token_hash)
        self.assertTrue(sent_ticket.pdf)
        self.assertTrue(sent_ticket.has_imported_email_verification)
        self.assertEqual(pending_ticket.status, Ticket.Status.PENDING)
        self.assertFalse(pending_ticket.qr_token_hash)
        issue_audit = AuditLog.objects.get(
            action="TICKET_ISSUED",
            object_id=str(sent_ticket.pk),
        )
        self.assertEqual(issue_audit.actor, self.admin)
        self.assertEqual(issue_audit.details["authorization_basis"], "IMPORTED_EMAIL_SENT")

        sent_timestamp = sent_ticket.imported_email_verification_timestamp
        sent_ticket.source_metadata = {
            **sent_ticket.source_metadata,
            "email_status": "PENDING",
            "email_sent_at": "",
        }
        sent_ticket.imported_email_sent_at = None
        sent_ticket.save(update_fields=("source_metadata", "imported_email_sent_at"))
        self.assertTrue(sent_ticket.has_imported_email_verification)
        sheet_row = dict(zip(SHEET_COLUMNS, _ticket_row(sent_ticket, timezone.now())))
        self.assertEqual(sheet_row["payment_status"], Registration.PaymentStatus.PENDING)
        self.assertEqual(sheet_row["email_status"], Ticket.EmailStatus.SENT)
        self.assertEqual(sheet_row["email_sent_at"], sent_timestamp)
        GoogleSheetImportState.objects.create(
            worksheet="Online_Tickets",
            last_synced_at=timezone.now(),
            last_result={"issued": 1},
        )
        dashboard = client.get("/api/admin/dashboard/")
        self.assertEqual(dashboard.data["remaining_eligible"], 1)
        self.assertEqual(
            dashboard.data["google_sheet_imports"][0]["last_result"]["issued"],
            1,
        )

        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        scan = scanner_client.post(
            "/api/scanner/scan/",
            {"credential": credentials[0]},
            format="json",
        )
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(scan.data["result"], CheckIn.Result.ACCEPTED)

    def test_imported_sent_email_without_timestamp_does_not_issue(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,ticket_type,name,email,payment_status,email_status,email_sent_at\n"
            "no-time-ticket,NO-TIME,General,Guest,guest@example.test,PENDING,SENT,\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        response = client.post(
            f"/api/admin/import/{preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["issued"], 0)
        ticket = Ticket.objects.get(external_ticket_id="no-time-ticket")
        self.assertEqual(ticket.status, Ticket.Status.PENDING)
        self.assertFalse(ticket.qr_token_hash)
        self.assertEqual(ticket.registration.payment_status, Registration.PaymentStatus.PENDING)

    def test_import_stale_update_never_changes_issued_ticket_state(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        self.ticket.external_ticket_id = "stable-ticket-id"
        self.ticket.source_updated_at = timezone.now()
        self.ticket.status = Ticket.Status.ISSUED
        self.ticket.save(update_fields=("external_ticket_id", "source_updated_at", "status"))
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,payment_status,ticket_status,updated_at\n"
            "stable-ticket-id,EVT-0001,,General,Stale Name,guest@example.test,CAPTURED,CANCELLED,2020-01-01T00:00:00+00:00\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        response = client.post(
            f"/api/admin/import/{preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(response.data["skipped"], 1)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.attendee_name, "A. Guest")
        self.assertEqual(self.ticket.status, Ticket.Status.ISSUED)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.payment_status, Registration.PaymentStatus.PAID)

    def test_import_stale_source_cannot_overwrite_newer_native_ticket(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,updated_at\n"
            f"{self.ticket.pk},EVT-0001,,General,Stale Name,guest@example.test,2000-01-01T00:00:00+00:00\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        response = client.post(
            f"/api/admin/import/{preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(response.data["skipped"], 1)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.attendee_name, "A. Guest")

    def test_import_rejects_extra_csv_cells_and_amount_precision(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,ticket_type,name,email,amount_due\n"
            "bad-row,EXTRA-1,General,Guest,guest@example.test,10,unexpected\n"
            "bad-amount,EXTRA-2,General,Guest,guest@example.test,1.234\n"
        )
        response = self._preview_csv(client, csv_content.encode())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["rejected"], 2)
        self.assertTrue(any("more cells" in error for error in response.data["errors"][0]["errors"]))
        self.assertTrue(any("decimal amount" in error for error in response.data["errors"][1]["errors"]))

    def test_import_error_report_is_admin_only_and_csv_safe(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        csv_content = (
            "ticket_id,ticket_number,registration_id,ticket_type,name,email,payment_status\n"
            "bad,@=CMD,order,General,Guest,invalid,CAPTURED\n"
        )
        preview = self._preview_csv(client, csv_content.encode())
        report = client.get(preview.data["error_report_url"])
        self.assertEqual(report.status_code, 200)
        self.assertIn("email is invalid", report.content.decode())
        self.assertIn("'@=CMD", report.content.decode())
        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        self.assertEqual(scanner_client.get(preview.data["error_report_url"]).status_code, 403)

    def test_xlsx_import_preview_supported(self):
        from openpyxl import Workbook

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(("ticket_id", "ticket_number", "ticket_type", "name", "email"))
        worksheet.append(("xlsx-ticket-1", "XLSX-1", "General", "Xlsx Guest", "xlsx@example.test"))
        content = BytesIO()
        workbook.save(content)
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            "/api/admin/import/preview/",
            {
                "event_id": str(self.event.pk),
                "file": SimpleUploadedFile(
                    "tickets.xlsx",
                    content.getvalue(),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["created"], 1)

    @override_settings(
        GOOGLE_SHEETS_SPREADSHEET_ID="spreadsheet-id",
        GOOGLE_SERVICE_ACCOUNT_FILE="service-account.json",
        GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
    )
    def test_sheets_sync_upserts_by_stable_ticket_id(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        service = MagicMock()
        service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [
                {"properties": {"title": "Online_Tickets", "sheetId": 1}},
                {"properties": {"title": "OnSpot_Tickets", "sheetId": 2}},
            ]
        }
        stable_id = ticket.external_ticket_id or str(ticket.pk)
        service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = [
            {"values": [list(SHEET_COLUMNS)]},
            {"values": [list(SHEET_COLUMNS)]},
            {"values": [["ticket_id"]]},
            {"values": [list(SHEET_COLUMNS)]},
            {"values": [list(SHEET_COLUMNS)]},
            {"values": [["ticket_id"], [stable_id]]},
        ]
        with patch("operations.sheets_sync._service", return_value=service):
            self.assertTrue(sync_ticket(ticket.pk))
            self.assertTrue(sync_ticket(ticket.pk))

        append = service.spreadsheets.return_value.values.return_value.append
        update = service.spreadsheets.return_value.values.return_value.update
        append.assert_called_once()
        update.assert_called_once()
        self.assertIn("OnSpot_Tickets", append.call_args.kwargs["range"])
        self.assertIn("A2:X2", update.call_args.kwargs["range"])
        self.assertEqual(len(append.call_args.kwargs["body"]["values"][0]), 24)

    @override_settings(
        GOOGLE_SHEETS_SPREADSHEET_ID="spreadsheet-id",
        GOOGLE_SERVICE_ACCOUNT_FILE="service-account.json",
        GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
    )
    def test_sheets_failure_is_sanitized_and_queued_for_retry(self):
        ticket, _credential = issue_ticket(self.ticket.pk, self.admin)
        job = SpreadsheetSyncJob.objects.create(ticket=ticket)
        with patch(
            "operations.sheets_sync.sync_ticket",
            side_effect=RuntimeError("private service-account response"),
        ):
            self.assertEqual(process_sync_jobs(limit=1), 1)

        job.refresh_from_db()
        self.assertEqual(job.status, SpreadsheetSyncJob.Status.FAILED)
        self.assertEqual(job.last_error, SAFE_SYNC_ERROR)
        self.assertNotIn("private service-account response", job.last_error)
        self.assertIsNotNone(job.next_attempt_at)

    def test_sheets_command_requires_configuration(self):
        with override_settings(
            GOOGLE_SHEETS_SPREADSHEET_ID="",
            GOOGLE_SERVICE_ACCOUNT_FILE="",
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
        ):
            with self.assertRaises(CommandError):
                call_command("sync_google_sheets", verbosity=0)

    @override_settings(
        GOOGLE_SHEETS_SPREADSHEET_ID="spreadsheet-id",
        GOOGLE_SERVICE_ACCOUNT_FILE="service-account.json",
        GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
    )
    def test_google_sheet_import_preview_is_admin_only_and_requires_commit(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        sheet_rows = [
            list(SHEET_COLUMNS),
            [
                "sheet-ticket-1",
                "SHEET-1",
                "sheet-order-1",
                "General",
                "Sheet Guest",
                "sheet@example.test",
                "5551234567",
                "150.00",
                "UPI",
                "",
                "UTR-1",
                "PENDING",
                "Example Admin",
                "",
                "ISSUED",
                "",
                "SENT",
                "",
                "NOT_CHECKED_IN",
                "",
                "",
                "",
                "2026-10-01T10:00:00+05:30",
                "",
            ],
        ]
        with patch(
            "operations.views.read_worksheet_rows",
            return_value=sheet_rows,
        ) as read_sheet:
            existing_tickets = Ticket.objects.count()
            existing_registrations = Registration.objects.count()
            preview = client.post(
                "/api/admin/import/google-sheets/preview/",
                {
                    "event_id": str(self.event.pk),
                    "worksheet": "Online_Tickets",
                },
                format="json",
            )
        self.assertEqual(preview.status_code, 201)
        self.assertEqual(preview.data["source_worksheet"], "Online_Tickets")
        self.assertEqual(preview.data["created"], 1)
        self.assertEqual(Ticket.objects.count(), existing_tickets)
        self.assertEqual(Registration.objects.count(), existing_registrations)
        self.assertEqual(preview.data["commit_available"], True)
        read_sheet.assert_called_once_with("Online_Tickets")
        committed = client.post(
            f"/api/admin/import/{preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(committed.status_code, 200)
        self.assertEqual(committed.data["created"], 1)
        imported = Ticket.objects.get(external_ticket_id="sheet-ticket-1")
        self.assertEqual(imported.status, Ticket.Status.PENDING)
        self.assertFalse(imported.qr_token_hash)
        self.assertEqual(imported.registration.source, Registration.Source.ONLINE)

        spot_row = list(sheet_rows[1])
        spot_row[0] = "sheet-ticket-spot-1"
        spot_row[1] = "SHEET-SPOT-1"
        spot_row[2] = "sheet-order-spot-1"
        spot_sheet = [sheet_rows[0], spot_row]
        with patch("operations.views.read_worksheet_rows", return_value=spot_sheet):
            spot_preview = client.post(
                "/api/admin/import/google-sheets/preview/",
                {
                    "event_id": str(self.event.pk),
                    "worksheet": "OnSpot_Tickets",
                },
                format="json",
            )
        self.assertEqual(spot_preview.status_code, 201)
        self.assertEqual(spot_preview.data["rejected"], 0)
        spot_commit = client.post(
            f"/api/admin/import/{spot_preview.data['batch_id']}/commit/",
            {"confirm": True},
            format="json",
        )
        self.assertEqual(spot_commit.status_code, 200)
        imported_spot = Ticket.objects.get(external_ticket_id="sheet-ticket-spot-1")
        self.assertEqual(imported_spot.registration.source, Registration.Source.ON_SPOT)
        self.assertEqual(imported_spot.registration.payment_status, Registration.PaymentStatus.PENDING)
        self.assertFalse(imported_spot.qr_token_hash)

        scanner_client = APIClient()
        scanner_client.force_authenticate(self.scanner)
        self.assertEqual(
            scanner_client.post(
                "/api/admin/import/google-sheets/preview/",
                {"event_id": str(self.event.pk), "worksheet": "OnSpot_Tickets"},
                format="json",
            ).status_code,
            403,
        )

    @override_settings(
        GOOGLE_SHEETS_SPREADSHEET_ID="",
        GOOGLE_SERVICE_ACCOUNT_FILE="",
        GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
    )
    def test_google_sheet_import_preview_reports_missing_configuration(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            "/api/admin/import/google-sheets/preview/",
            {
                "event_id": str(self.event.pk),
                "worksheet": "Online_Tickets",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.data["detail"])

    def _automatic_sheet_rows(self, ticket_id, ticket_number, updated_at, payment_status="VERIFIED"):
        values = {
            "ticket_id": ticket_id,
            "ticket_number": ticket_number,
            "registration_id": f"order-{ticket_id}",
            "ticket_type": "General",
            "name": "Auto Import Guest",
            "email": f"{ticket_id}@example.test",
            "payment_status": payment_status,
            "ticket_status": "PENDING",
            "updated_at": updated_at.isoformat(),
        }
        return [list(SHEET_COLUMNS), [values.get(column, "") for column in SHEET_COLUMNS]]

    def test_automatic_import_commits_both_tabs_and_issues_verified_rows(self):
        from .sheet_import_sync import sync_google_sheet_imports

        timestamp = timezone.now()
        sheets = {
            "Online_Tickets": self._automatic_sheet_rows(
                "auto-online-1", "AUTO-ONLINE-1", timestamp
            ),
            "OnSpot_Tickets": self._automatic_sheet_rows(
                "auto-spot-1", "AUTO-SPOT-1", timestamp
            ),
        }
        with patch("operations.sheet_import_sync.read_worksheet_rows", side_effect=sheets.get):
            results = sync_google_sheet_imports()

        self.assertEqual([result["created"] for result in results], [1, 1])
        self.assertEqual([result["issued"] for result in results], [1, 1])
        self.assertEqual(
            Ticket.objects.get(external_ticket_id="auto-online-1").registration.source,
            Registration.Source.ONLINE,
        )
        self.assertEqual(
            Ticket.objects.get(external_ticket_id="auto-spot-1").registration.source,
            Registration.Source.ON_SPOT,
        )
        self.assertTrue(Ticket.objects.get(external_ticket_id="auto-online-1").qr_token_hash)
        self.assertEqual(GoogleSheetImportState.objects.count(), 2)

    def test_unchanged_sheet_snapshot_does_not_create_another_batch(self):
        from .sheet_import_sync import sync_google_sheet_import

        rows = self._automatic_sheet_rows(
            "auto-stable-1", "AUTO-STABLE-1", timezone.now()
        )
        with patch("operations.sheet_import_sync.read_worksheet_rows", return_value=rows):
            first = sync_google_sheet_import("Online_Tickets")
            first_checked_at = GoogleSheetImportState.objects.get(
                worksheet="Online_Tickets"
            ).last_synced_at
            second = sync_google_sheet_import("Online_Tickets")
            second_checked_at = GoogleSheetImportState.objects.get(
                worksheet="Online_Tickets"
            ).last_synced_at

        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertGreater(second_checked_at, first_checked_at)
        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertEqual(Ticket.objects.filter(external_ticket_id="auto-stable-1").count(), 1)

    def test_sheet_payment_change_applies_when_updated_at_cell_is_unchanged(self):
        from .sheet_import_sync import sync_google_sheet_import

        old_timestamp = timezone.now() - timedelta(days=2)
        rows = self._automatic_sheet_rows(
            "auto-payment-change-1",
            "AUTO-PAYMENT-CHANGE-1",
            old_timestamp,
            payment_status="PENDING",
        )
        with patch("operations.sheet_import_sync.read_worksheet_rows", return_value=rows):
            first = sync_google_sheet_import("Online_Tickets")
        self.assertEqual(first["issued"], 0)

        updated_rows = [list(rows[0]), list(rows[1])]
        payment_status_index = SHEET_COLUMNS.index("payment_status")
        updated_rows[1][payment_status_index] = "VERIFIED"
        with patch(
            "operations.sheet_import_sync.read_worksheet_rows",
            return_value=updated_rows,
        ):
            second = sync_google_sheet_import("Online_Tickets")

        ticket = Ticket.objects.get(external_ticket_id="auto-payment-change-1")
        ticket.registration.refresh_from_db()
        self.assertEqual(second["issued"], 1)
        self.assertEqual(ticket.registration.imported_payment_status, "VERIFIED")
        self.assertEqual(ticket.status, Ticket.Status.ISSUED)

    def test_automatic_import_retries_after_temporary_sheet_failure(self):
        from .sheet_import_sync import sync_google_sheet_imports

        rows = self._automatic_sheet_rows(
            "auto-retry-1", "AUTO-RETRY-1", timezone.now()
        )

        def first_poll(worksheet):
            if worksheet == "Online_Tickets":
                raise OSError("temporary provider failure")
            return [list(SHEET_COLUMNS)]

        with patch("operations.sheet_import_sync.read_worksheet_rows", side_effect=first_poll):
            first = sync_google_sheet_imports()
        self.assertIn("error", first[0])
        state = GoogleSheetImportState.objects.get(worksheet="Online_Tickets")
        self.assertEqual(state.content_hash, "")
        self.assertTrue(state.last_error)

        with patch(
            "operations.sheet_import_sync.read_worksheet_rows",
            side_effect=lambda worksheet: rows if worksheet == "Online_Tickets" else [list(SHEET_COLUMNS)],
        ):
            second = sync_google_sheet_imports()
        self.assertEqual(second[0]["issued"], 1)
        self.assertFalse(GoogleSheetImportState.objects.get(worksheet="Online_Tickets").last_error)

    def test_automatic_import_refuses_ambiguous_active_event(self):
        from .sheet_import_sync import sync_google_sheet_imports

        Event.objects.create(
            name="Other active event",
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
            venue="Other Hall",
        )
        with patch("operations.sheet_import_sync.read_worksheet_rows") as read_sheet:
            results = sync_google_sheet_imports()

        self.assertTrue(all("error" in result for result in results))
        self.assertFalse(read_sheet.called)
        self.assertEqual(ImportBatch.objects.count(), 0)
