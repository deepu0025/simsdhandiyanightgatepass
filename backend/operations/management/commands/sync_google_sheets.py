import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef, Q

from operations.models import (
    IMPORTED_VERIFIED_PAYMENT_STATUSES,
    SpreadsheetSyncJob,
    Ticket,
)
from operations.sheet_import_sync import sync_google_sheet_imports
from operations.sheets_sync import process_sync_jobs, sheets_configured


class Command(BaseCommand):
    help = "Queue and process eligible ticket syncs to Google Sheets."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--enqueue-issued",
            action="store_true",
            help="Queue issued tickets that do not already have an active or successful sync.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Continuously import both worksheets and sync tickets to Google Sheets.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=30,
            help="Polling interval in seconds for --watch (30-60 seconds).",
        )

    def handle(self, *args, **options):
        if not sheets_configured():
            raise CommandError(
                "Set GOOGLE_SHEETS_SPREADSHEET_ID and either GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE before syncing."
            )
        if options["limit"] < 1:
            raise CommandError("--limit must be a positive integer.")
        if options["watch"] and not 30 <= options["interval"] <= 60:
            raise CommandError("--interval must be between 30 and 60 seconds.")

        def enqueue_issued():
            existing = SpreadsheetSyncJob.objects.filter(
                ticket_id=OuterRef("pk"),
                status__in=(
                    SpreadsheetSyncJob.Status.PENDING,
                    SpreadsheetSyncJob.Status.PROCESSING,
                    SpreadsheetSyncJob.Status.SUCCEEDED,
                ),
            )
            tickets = Ticket.objects.filter(
                status=Ticket.Status.ISSUED,
            ).filter(
                Q(registration__imported_payment_status__in=IMPORTED_VERIFIED_PAYMENT_STATUSES)
                | Q(
                    imported_email_sent_at__isnull=False,
                    source_metadata__email_status=Ticket.EmailStatus.SENT,
                )
                | Q(source_metadata__gatehouse_email_verification_at__isnull=False)
            ).annotate(has_sync=Exists(existing)).filter(has_sync=False)
            jobs = [
                SpreadsheetSyncJob(ticket=ticket)
                for ticket in tickets.iterator(chunk_size=500)
            ]
            SpreadsheetSyncJob.objects.bulk_create(jobs, batch_size=500)
            return len(jobs)

        def run_sync_cycle(backfill):
            if options["watch"]:
                results = sync_google_sheet_imports()
                for result in results:
                    if result.get("error"):
                        self.stderr.write(
                            f'{result["worksheet"]}: {result["error"]}'
                        )
                    elif result.get("changed"):
                        self.stdout.write(
                            f'{result["worksheet"]}: '
                            f'{result.get("created", 0)} created, '
                            f'{result.get("updated", 0)} updated, '
                            f'{result.get("issued", 0)} issued, '
                            f'{result.get("rejected", 0)} rejected.'
                        )

            queued = enqueue_issued() if backfill else 0
            processed = process_sync_jobs(options["limit"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queued {queued}; processed {processed} Google Sheets job(s)."
                )
            )

        if not options["watch"]:
            run_sync_cycle(backfill=options["enqueue_issued"])
            return

        self.stdout.write("Watching both Google Sheets tabs; press Ctrl+C to stop.")
        try:
            initial_cycle = True
            while True:
                run_sync_cycle(backfill=initial_cycle)
                initial_cycle = False
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Google Sheets worker stopped.")
