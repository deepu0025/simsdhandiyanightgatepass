import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


IMPORTED_VERIFIED_PAYMENT_STATUSES = frozenset({"PAID", "CAPTURED", "VERIFIED"})


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("name", "Administrator")
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have staff and superuser permissions.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        SCANNER = "SCANNER", "Scanner"

    username = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=160, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.SCANNER)
    gate = models.CharField(
        max_length=16,
        choices=(("", "Unassigned"), ("GATE_1", "Gate 1"), ("GATE_2", "Gate 2"), ("GATE_3", "Gate 3"), ("GATE_4", "Gate 4")),
        blank=True,
        default="",
    )
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("gate",),
                condition=models.Q(role="SCANNER") & ~models.Q(gate=""),
                name="one_scanner_account_per_gate",
            )
        ]

    def __str__(self):
        return self.email


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    venue = models.CharField(max_length=240)
    instructions = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Registration(models.Model):
    class Source(models.TextChoices):
        ONLINE = "ONLINE", "Online"
        ON_SPOT = "ON_SPOT", "On-spot"
        IMPORT = "IMPORT", "Import"

    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="registrations")
    source = models.CharField(max_length=16, choices=Source.choices)
    buyer_name = models.CharField(max_length=160)
    buyer_email = models.EmailField()
    buyer_phone = models.CharField(max_length=32, blank=True)
    order_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    payment_method = models.CharField(max_length=40, blank=True)
    upi_id = models.CharField(max_length=120, blank=True)
    payment_status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    transaction_id = models.CharField(max_length=120, blank=True)
    imported_payment_status = models.CharField(max_length=40, blank=True)
    imported_verified_by = models.CharField(max_length=160, blank=True)
    imported_verified_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_registrations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("event", "payment_status"))]


class PaymentVerification(models.Model):
    registration = models.ForeignKey(
        Registration,
        on_delete=models.PROTECT,
        related_name="payment_verifications",
    )
    transaction_id = models.CharField(max_length=120, blank=True)
    decision = models.CharField(max_length=16, choices=Registration.PaymentStatus.choices)
    verifier = models.ForeignKey(User, on_delete=models.PROTECT)
    note = models.CharField(max_length=500, blank=True)
    verified_at = models.DateTimeField(auto_now_add=True)


class Ticket(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ISSUED = "ISSUED", "Issued"
        CANCELLED = "CANCELLED", "Cancelled"
        REVOKED = "REVOKED", "Revoked"

    class EmailStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_ticket_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    ticket_number = models.CharField(max_length=80, unique=True)
    registration = models.ForeignKey(Registration, on_delete=models.PROTECT, related_name="tickets")
    ticket_type = models.CharField(max_length=120)
    attendee_name = models.CharField(max_length=160)
    attendee_email = models.EmailField()
    attendee_phone = models.CharField(max_length=32, blank=True)
    # Only the digest is stored; raw credentials exist in the PDF and scan request.
    qr_token_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    pdf = models.FileField(upload_to="tickets/%Y/%m/", blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    source_ticket_generated_at = models.DateTimeField(null=True, blank=True)
    imported_email_sent_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_metadata = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    email_status = models.CharField(
        max_length=16,
        choices=EmailStatus.choices,
        default=EmailStatus.PENDING,
    )
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checked_in_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("ticket_number",)),
            models.Index(fields=("status", "checked_in_at")),
        ]

    @property
    def has_imported_email_verification(self):
        if self.source_metadata.get("gatehouse_email_verification_at"):
            return True
        source_email_status = str(self.source_metadata.get("email_status", "")).strip().upper()
        return bool(self.imported_email_sent_at and source_email_status == self.EmailStatus.SENT)

    @property
    def imported_email_verification_timestamp(self):
        return (
            self.source_metadata.get("gatehouse_email_verification_at")
            or (
                self.imported_email_sent_at.isoformat()
                if self.has_imported_email_verification and self.imported_email_sent_at
                else ""
            )
        )

    @property
    def has_imported_payment_verification(self):
        return (
            self.registration.imported_payment_status.strip().upper()
            in IMPORTED_VERIFIED_PAYMENT_STATUSES
        )

    @property
    def is_eligible_for_issuance(self):
        return self.has_imported_payment_verification or self.has_imported_email_verification


class EmailDeliveryAttempt(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="email_attempts")
    status = models.CharField(max_length=16, choices=Ticket.EmailStatus.choices)
    error_message = models.CharField(max_length=300, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)


class CheckIn(models.Model):
    class Method(models.TextChoices):
        QR = "QR", "QR scan"
        MANUAL = "MANUAL", "Manual"

    class Result(models.TextChoices):
        VALID = "VALID", "Valid ticket"
        ACCEPTED = "ACCEPTED", "Accepted"
        ALREADY_USED = "ALREADY_USED", "Already checked in"
        INVALID = "INVALID", "Invalid credential"
        PAYMENT_NOT_VERIFIED = "PAYMENT_NOT_VERIFIED", "Payment not verified"
        INELIGIBLE = "INELIGIBLE", "Payment not verified"
        CANCELLED = "CANCELLED", "Cancelled or revoked"

    ticket = models.ForeignKey(
        Ticket,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scan_events",
    )
    scanner = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    gate = models.CharField(max_length=100, blank=True)
    result = models.CharField(max_length=20, choices=Result.choices)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.QR)
    reason = models.CharField(max_length=500, blank=True)
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-scanned_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("ticket",),
                condition=models.Q(result="ACCEPTED"),
                name="one_successful_checkin_per_ticket",
            )
        ]


class SpreadsheetSyncJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="sync_jobs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)


class GoogleSheetImportState(models.Model):
    worksheet = models.CharField(max_length=32, unique=True)
    content_hash = models.CharField(max_length=64, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    last_result = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "Previewed"
        COMMITTED = "COMMITTED", "Committed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="import_batches")
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="import_batches")
    original_filename = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PREVIEWED)
    rows = models.JSONField(default=list)
    result = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
