import re

from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import CheckIn, Event, Registration, Ticket, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "name", "role", "gate", "desk", "is_active")


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
    def validate(self, attrs):
        starts_at = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends_at = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "Event end must be after its start."})
        return attrs

    class Meta:
        model = Event
        fields = ("id", "name", "starts_at", "ends_at", "venue", "instructions", "is_active")


class TicketSerializer(serializers.ModelSerializer):
    source = serializers.CharField(source="registration.source", read_only=True)
    payment_status = serializers.SerializerMethodField()
    attendee_email = serializers.EmailField(read_only=True)
    checkin_method = serializers.SerializerMethodField()
    eligibility_basis = serializers.SerializerMethodField()
    registration_desk = serializers.SerializerMethodField()

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

    def get_registration_desk(self, obj):
        creator = obj.registration.created_by
        if creator and creator.desk:
            return creator.desk
        if obj.registration.source == Registration.Source.ON_SPOT:
            return "ADMIN"
        return None

    class Meta:
        model = Ticket
        fields = (
            "id", "ticket_number", "registration_id", "source", "ticket_type",
            "attendee_name", "attendee_email", "status", "email_status",
            "payment_status", "checked_in_at", "checkin_method", "eligibility_basis",
            "registration_desk",
            "generated_at", "created_at",
        )


class OnSpotRegistrationSerializer(serializers.Serializer):
    SINGLE_TICKET = "Single ticket"
    COMBO_TICKET = "Combo 5+1"
    TICKET_PRICE = 149

    event_id = serializers.UUIDField()
    attendee_name = serializers.CharField(max_length=160)
    attendee_names = serializers.ListField(
        child=serializers.CharField(max_length=160),
        required=False,
        allow_empty=False,
        max_length=120,
    )
    attendee_email = serializers.EmailField()
    attendee_phone = serializers.CharField(max_length=10, required=False, allow_blank=True)
    ticket_type = serializers.ChoiceField(choices=(SINGLE_TICKET, COMBO_TICKET))
    quantity = serializers.IntegerField(min_value=1, max_value=20)
    payment_method = serializers.ChoiceField(choices=("CASH", "CARD", "UPI"))
    transaction_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate_event_id(self, value):
        try:
            Event.objects.get(pk=value, is_active=True)
        except Event.DoesNotExist as error:
            raise serializers.ValidationError("Select an active event.") from error
        return value

    def validate_attendee_email(self, value):
        _, separator, domain = value.rpartition("@")
        labels = domain.split(".")
        if not separator or len(labels) < 2 or any(not label for label in labels):
            raise serializers.ValidationError("Enter a valid email address with a domain, such as name@example.com.")
        top_level_domain = labels[-1]
        if len(top_level_domain) < 2 or not re.search(r"[A-Za-z]", top_level_domain):
            raise serializers.ValidationError("Enter a valid email address with a complete domain.")
        return value

    def validate_attendee_phone(self, value):
        if value and not re.fullmatch(r"[6-9][0-9]{9}", value):
            raise serializers.ValidationError("Enter a valid 10-digit Indian mobile number.")
        return value

    def validate(self, attrs):
        if attrs["payment_method"] == "UPI" and not attrs.get("transaction_reference"):
            raise serializers.ValidationError({"transaction_reference": "Enter the UPI transaction reference."})
        package_size = 6 if attrs["ticket_type"] == self.COMBO_TICKET else 1
        paid_ticket_count = 5 if package_size == 6 else 1
        attrs["ticket_count"] = attrs["quantity"] * package_size
        attendee_names = attrs.get("attendee_names")
        if attendee_names is None:
            attendee_names = [attrs["attendee_name"]] * attrs["ticket_count"]
        elif len(attendee_names) != attrs["ticket_count"]:
            raise serializers.ValidationError({
                "attendee_names": f"Enter one attendee name for each of the {attrs['ticket_count']} tickets."
            })
        if any(not name.strip() for name in attendee_names):
            raise serializers.ValidationError({"attendee_names": "Every ticket needs an attendee name."})
        attrs["attendee_names"] = attendee_names
        attrs["attendee_name"] = attendee_names[0]
        attrs["amount"] = attrs["quantity"] * paid_ticket_count * self.TICKET_PRICE
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
