from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    AuditLog,
    CheckIn,
    EmailDeliveryAttempt,
    Event,
    Registration,
    SpreadsheetSyncJob,
    Ticket,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "role", "gate", "desk", "is_active", "is_staff")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name", "role", "gate", "desk")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "name", "role", "password1", "password2")}),)
    search_fields = ("email", "name")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "starts_at", "venue", "is_active")
    search_fields = ("name", "venue")


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("id", "buyer_name", "buyer_email", "source", "payment_status", "created_at")
    list_filter = ("source", "payment_status")
    search_fields = ("buyer_name", "buyer_email", "transaction_id")
    readonly_fields = ("payment_status", "imported_payment_status", "imported_verified_by", "imported_verified_at")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "attendee_name", "status", "email_status", "checked_in_at")
    list_filter = ("status", "email_status")
    search_fields = ("ticket_number", "attendee_name", "attendee_email")
    readonly_fields = ("qr_token_hash",)


admin.site.register((EmailDeliveryAttempt, CheckIn, SpreadsheetSyncJob, AuditLog))
