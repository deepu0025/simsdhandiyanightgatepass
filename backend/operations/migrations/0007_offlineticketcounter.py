import re

from django.db import migrations, models


def initialize_offline_ticket_counter(apps, schema_editor):
    Ticket = apps.get_model("operations", "Ticket")
    OfflineTicketCounter = apps.get_model("operations", "OfflineTicketCounter")
    highest_number = 0
    for ticket_number in Ticket.objects.filter(
        ticket_number__startswith="SIMS-DN-OFF-"
    ).values_list("ticket_number", flat=True).iterator():
        match = re.fullmatch(r"SIMS-DN-OFF-(\d+)", ticket_number)
        if match:
            highest_number = max(highest_number, int(match.group(1)))
    OfflineTicketCounter.objects.create(id=1, last_number=highest_number)


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0006_user_desk_alter_user_role_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="OfflineTicketCounter",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("last_number", models.PositiveBigIntegerField(default=0)),
            ],
        ),
        migrations.RunPython(
            initialize_offline_ticket_counter,
            migrations.RunPython.noop,
        ),
    ]
