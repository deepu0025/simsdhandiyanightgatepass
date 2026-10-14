import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from operations.models import User


class Command(BaseCommand):
    help = "Create or promote the configured bootstrap administrator without resetting an existing password."

    @transaction.atomic
    def handle(self, *args, **options):
        email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not email or not password:
            raise CommandError(
                "Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD in the project .env file."
            )
        if len(password) < 16:
            raise CommandError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 16 characters.")

        user = User.objects.filter(email=email).first()
        if user is None:
            try:
                validate_password(password, user=User(email=email))
            except ValidationError as error:
                raise CommandError(f"The configured administrator password is not acceptable: {error}") from error
            User.objects.create_superuser(email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"Created administrator account for {email}."))
            return

        changed_fields = []
        if user.role != User.Role.ADMIN:
            user.role = User.Role.ADMIN
            changed_fields.append("role")
        if not user.is_staff:
            user.is_staff = True
            changed_fields.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            changed_fields.append("is_superuser")
        if not user.is_active:
            user.is_active = True
            changed_fields.append("is_active")
        if os.environ.get("BOOTSTRAP_ADMIN_RESET_PASSWORD", "").lower() == "true":
            try:
                validate_password(password, user=user)
            except ValidationError as error:
                raise CommandError(f"The configured administrator password is not acceptable: {error}") from error
            user.set_password(password)
            changed_fields.append("password")

        if changed_fields:
            user.save(update_fields=changed_fields)
            self.stdout.write(self.style.SUCCESS(f"Updated administrator account for {email}."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Administrator account {email} already exists; unchanged."))
