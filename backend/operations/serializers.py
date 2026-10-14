from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import CheckIn, Event, Registration, Ticket, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "name", "role", "gate", "is_active")


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"],
            password=attrs["password"],
        )
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ("id", "name", "starts_at", "ends_at", "venue", "instructions", "is_active")


class TicketSerializer(serializers.ModelSerializer):
    source = serializers.CharField(source="registration.source", read_only=True)
    payment_status = serializers.SerializerMethodField()
    attendee_email = serializers.EmailField(read_only=True)
    checkin_method = serializers.SerializerMethodField()
    eligibility_basis = serializers.SerializerMethodField()

    def get_checkin_method(self, obj):
        checkins = getattr(obj, "successful_checkins", None)
        if checkins is None:
            checkins = obj.scan_events.filter(result=CheckIn.Result.ACCEPTED)
        checkin = next(iter(checkins), None)
        return checkin.method if checkin else None

    def get_payment_status(self, obj):
        return obj.registration.imported_payment_status or Registration.PaymentStatus.PENDING

    def get_eligibility_basis(self, obj):
        if obj.has_imported_payment_verification:
            return "IMPORTED_PAYMENT_STATUS"
        if obj.has_imported_email_verification:
            return "IMPORTED_EMAIL_SENT"
        return None

    class Meta:
        model = Ticket
        fields = (
            "id", "ticket_number", "registration_id", "source", "ticket_type",
            "attendee_name", "attendee_email", "status", "email_status",
            "payment_status", "checked_in_at", "checkin_method", "eligibility_basis",
            "generated_at", "created_at",
        )


class OnSpotRegistrationSerializer(serializers.Serializer):
    event_id = serializers.UUIDField()
    attendee_name = serializers.CharField(max_length=160)
    attendee_email = serializers.EmailField()
    attendee_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    ticket_type = serializers.CharField(max_length=120)
    ticket_number = serializers.CharField(max_length=80, required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=("CASH", "CARD", "UPI"))
    transaction_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)

    def validate_event_id(self, value):
        try:
            Event.objects.get(pk=value, is_active=True)
        except Event.DoesNotExist as error:
            raise serializers.ValidationError("Select an active event.") from error
        return value

    def validate(self, attrs):
        if attrs["payment_method"] == "UPI" and not attrs.get("transaction_reference"):
            raise serializers.ValidationError({"transaction_reference": "Enter the UPI transaction reference."})
        return attrs


class TicketStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=(Ticket.Status.CANCELLED, Ticket.Status.REVOKED),
    )
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class ManualCheckInSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, trim_whitespace=True, allow_blank=False)


class CheckInSerializer(serializers.ModelSerializer):
    attendee_name = serializers.CharField(source="ticket.attendee_name", read_only=True)
    ticket_number = serializers.CharField(source="ticket.ticket_number", read_only=True)
    ticket_type = serializers.CharField(source="ticket.ticket_type", read_only=True)
    scanner_name = serializers.CharField(source="scanner.name", read_only=True)

    class Meta:
        model = CheckIn
        fields = (
            "id", "result", "scanned_at", "gate", "attendee_name",
            "ticket_number", "ticket_type", "scanner_name", "method", "reason",
        )
