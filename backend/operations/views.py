import csv
import io
import logging
from uuid import UUID

from django.contrib.auth import login, logout
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Count, Max, Prefetch, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from .models import (
    AuditLog,
    CheckIn,
    Event,
    GoogleSheetImportState,
    ImportBatch,
    PaymentVerification,
    Registration,
    SpreadsheetSyncJob,
    Ticket,
    User,
)
from .email_delivery import EmailDeliveryConflict, EmailDeliveryError, send_ticket_email
from .import_commit import commit_import_batch
from .imports import batch_preview, create_import_batch
from .permissions import IsAdmin, IsAdminOrRegistrationDesk, IsAdminOrScanner
from .serializers import (
    CheckInSerializer,
    EventSerializer,
    LoginSerializer,
    ManualCheckInSerializer,
    OnSpotRegistrationSerializer,
    TicketStatusSerializer,
    TicketSerializer,
    UserSerializer,
)
from .services import (
    _record_scan_attempt,
    check_in_ticket,
    issue_ticket,
    manual_check_in_ticket,
    next_offline_ticket_number,
    validate_ticket_credential,
)
from .sheets_sync import (
    process_sync_jobs,
    queue_ticket_sync,
    read_worksheet_rows,
    sheets_configured,
)

logger = logging.getLogger(__name__)


@ensure_csrf_cookie
def csrf(request):
    return JsonResponse({"csrfToken": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        login(request, serializer.validated_data["user"])
        return Response(UserSerializer(serializer.validated_data["user"]).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"authenticated": False})
        return Response(UserSerializer(request.user).data)


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            return Response({"status": "unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"status": "ok", "database": "ok"})


class DashboardView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        tickets = Ticket.objects.filter(registration__event__is_active=True)
        registrations = Registration.objects.filter(event__is_active=True)
        return Response({
            "registrations": registrations.count(),
            "tickets_issued": tickets.filter(status=Ticket.Status.ISSUED).count(),
            "online_tickets": tickets.filter(registration__source=Registration.Source.ONLINE).count(),
            "on_spot_tickets": tickets.filter(registration__source=Registration.Source.ON_SPOT).count(),
            "emails_pending_or_failed": tickets.filter(
                email_status__in=(Ticket.EmailStatus.PENDING, Ticket.EmailStatus.FAILED)
            ).count(),
            "check_ins": tickets.filter(checked_in_at__isnull=False).count(),
            "remaining_eligible": tickets.filter(
                status=Ticket.Status.ISSUED,
                checked_in_at__isnull=True,
            ).count(),
            "failed_sync_jobs": SpreadsheetSyncJob.objects.filter(
                status=SpreadsheetSyncJob.Status.FAILED
            ).count(),
            "google_sheet_imports": [
                {
                    "worksheet": state.worksheet,
                    "last_synced_at": state.last_synced_at,
                    "last_error": state.last_error,
                    "last_result": state.last_result,
                }
                for state in GoogleSheetImportState.objects.order_by("worksheet")
            ],
        })


class StaffActivityDashboardView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        scanners = []
        for scanner in User.objects.filter(role=User.Role.SCANNER).order_by("gate", "name"):
            scan_totals = CheckIn.objects.filter(scanner=scanner).aggregate(
                scans=Count("id"),
                accepted=Count("id", filter=Q(result=CheckIn.Result.ACCEPTED)),
                failed=Count("id", filter=~Q(result=CheckIn.Result.ACCEPTED)),
                last_scan_at=Max("scanned_at"),
            )
            scanners.append({
                "id": scanner.pk,
                "name": scanner.name,
                "email": scanner.email,
                "gate": scanner.gate,
                "is_active": scanner.is_active,
                **scan_totals,
            })

        desks = []
        for desk in User.objects.filter(role=User.Role.DESK).order_by("desk", "name"):
            registrations = Registration.objects.filter(
                created_by=desk,
                source=Registration.Source.ON_SPOT,
            )
            sales = registrations.aggregate(
                orders=Count("id"),
                total=Sum("order_total"),
                cash_orders=Count("id", filter=Q(payment_method="CASH")),
                cash_total=Sum("order_total", filter=Q(payment_method="CASH")),
                upi_orders=Count("id", filter=Q(payment_method="UPI")),
                upi_total=Sum("order_total", filter=Q(payment_method="UPI")),
                card_orders=Count("id", filter=Q(payment_method="CARD")),
                card_total=Sum("order_total", filter=Q(payment_method="CARD")),
            )
            desks.append({
                "id": desk.pk,
                "name": desk.name,
                "email": desk.email,
                "desk": desk.desk,
                "is_active": desk.is_active,
                "tickets_sold": Ticket.objects.filter(registration__in=registrations).count(),
                **{
                    key: (str(value) if key.endswith("_total") or key == "total" else value)
                    for key, value in sales.items()
                },
            })

        recent_scans = [
            {
                "id": str(scan.pk),
                "scanner_name": scan.scanner.name if scan.scanner else "",
                "scanner_gate": scan.scanner.gate if scan.scanner else "",
                "scanned_at": scan.scanned_at,
                "result": scan.result,
                "method": scan.method,
                "gate": scan.gate,
                "ticket_number": scan.ticket.ticket_number if scan.ticket else None,
                "attendee_name": scan.ticket.attendee_name if scan.ticket else None,
                "reason": scan.reason,
            }
            for scan in CheckIn.objects.filter(scanner__role=User.Role.SCANNER)
            .select_related("scanner", "ticket")
            .order_by("-scanned_at")[:100]
        ]
        recent_sales = [
            {
                "id": str(registration.pk),
                "desk_name": registration.created_by.name if registration.created_by else "",
                "desk": registration.created_by.desk if registration.created_by else "",
                "attendee_name": registration.buyer_name,
                "payment_method": registration.payment_method,
                "amount": str(registration.order_total or 0),
                "ticket_count": registration.ticket_count,
                "created_at": registration.created_at,
            }
            for registration in Registration.objects.filter(
                source=Registration.Source.ON_SPOT,
                created_by__role=User.Role.DESK,
            )
            .select_related("created_by")
            .annotate(ticket_count=Count("tickets"))
            .order_by("-created_at")[:100]
        ]
        return Response({
            "scanners": scanners,
            "desks": desks,
            "recent_scans": recent_scans,
            "recent_sales": recent_sales,
        })


class EventListView(APIView):
    permission_classes = [IsAdminOrRegistrationDesk]

    def get(self, request):
        events = Event.objects.all()
        if request.user.role == User.Role.DESK:
            events = events.filter(is_active=True)
        return Response(EventSerializer(events, many=True).data)

    def post(self, request):
        if request.user.role != User.Role.ADMIN and not request.user.is_superuser:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = EventSerializer(data={**request.data, "is_active": True})
        serializer.is_valid(raise_exception=True)
        event = serializer.save()
        AuditLog.objects.create(
            actor=request.user,
            action="EVENT_CREATED",
            object_type="Event",
            object_id=str(event.pk),
            details={"name": event.name, "is_active": event.is_active},
        )
        return Response(EventSerializer(event).data, status=status.HTTP_201_CREATED)


class EventDetailView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, event_id):
        try:
            event = Event.objects.get(pk=event_id)
        except Event.DoesNotExist as error:
            raise Http404 from error
        serializer = EventSerializer(event, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        event = serializer.save()
        AuditLog.objects.create(
            actor=request.user,
            action="EVENT_UPDATED",
            object_type="Event",
            object_id=str(event.pk),
            details={"updated_fields": sorted(request.data.keys())},
        )
        return Response(EventSerializer(event).data)


class TicketListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        tickets = Ticket.objects.select_related("registration__created_by").prefetch_related(
            Prefetch(
                "scan_events",
                queryset=CheckIn.objects.filter(result=CheckIn.Result.ACCEPTED),
                to_attr="successful_checkins",
            )
        )
        query = request.query_params.get("q", "").strip()
        if query:
            search = (
                Q(ticket_number__icontains=query)
                | Q(attendee_name__icontains=query)
                | Q(attendee_email__icontains=query)
                | Q(registration__transaction_id__icontains=query)
            )
            try:
                registration_id = UUID(query)
            except ValueError:
                pass
            else:
                search |= Q(registration_id=registration_id)
            tickets = tickets.filter(search)
        for param, field in (
            ("status", "status"),
            ("payment_status", "registration__payment_status"),
            ("email_status", "email_status"),
            ("source", "registration__source"),
        ):
            value = request.query_params.get(param)
            if value:
                tickets = tickets.filter(**{field: value})
        page = max(1, int(request.query_params.get("page", 1)))
        per_page = min(100, max(1, int(request.query_params.get("page_size", 25))))
        total = tickets.count()
        items = tickets[(page - 1) * per_page : page * per_page]
        return Response({
            "count": total,
            "page": page,
            "page_size": per_page,
            "results": TicketSerializer(items, many=True).data,
        })


class ManualCheckInView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, ticket_id):
        serializer = ManualCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result, ticket = manual_check_in_ticket(
                ticket_id=ticket_id,
                actor=request.user,
                reason=serializer.validated_data["reason"],
            )
        except Ticket.DoesNotExist as error:
            raise Http404 from error
        code = status.HTTP_200_OK if result == CheckIn.Result.ACCEPTED else status.HTTP_409_CONFLICT
        details = {"result": result, "ticket": TicketSerializer(ticket).data}
        if result == CheckIn.Result.ALREADY_USED:
            details["detail"] = "This ticket has already been checked in."
        elif result == CheckIn.Result.CANCELLED:
            details["detail"] = "Cancelled or revoked tickets cannot be manually checked in."
        return Response(
            details,
            status=code,
        )


class OnSpotRegistrationView(APIView):
    permission_classes = [IsAdminOrRegistrationDesk]

    @transaction.atomic
    def post(self, request):
        serializer = OnSpotRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or len(idempotency_key) > 128:
            return Response(
                {"detail": "A valid Idempotency-Key header is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        existing = Registration.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if request.user.role == User.Role.DESK and existing.created_by_id != request.user.pk:
                return Response(
                    {"detail": "This registration request belongs to another desk account."},
                    status=status.HTTP_409_CONFLICT,
                )
            tickets = list(existing.tickets.order_by("ticket_number"))
            ticket = tickets[0] if tickets else None
            same_request = (
                existing.event_id == data["event_id"]
                and existing.buyer_name == data["attendee_name"]
                and existing.buyer_email == data["attendee_email"]
                and existing.buyer_phone == data.get("attendee_phone", "")
                and existing.order_total == data["amount"]
                and existing.payment_method == data["payment_method"]
                and existing.transaction_id == data.get("transaction_reference", "")
                and len(tickets) == data["ticket_count"]
                and [item.attendee_name for item in tickets] == data["attendee_names"]
                and all(item.ticket_type == data["ticket_type"] for item in tickets)
            )
            if not same_request:
                return Response(
                    {"detail": "Idempotency-Key was already used for a different registration."},
                    status=status.HTTP_409_CONFLICT,
                )
            if not ticket:
                return Response({"detail": "Registration is being processed; retry shortly."}, status=202)
            sync_jobs = [item.sync_jobs.order_by("-created_at").first() for item in tickets]
            return Response({
                "registration_id": str(existing.pk),
                "ticket": TicketSerializer(ticket).data,
                "tickets": TicketSerializer(tickets, many=True).data,
                "quantity": data["quantity"],
                "ticket_count": len(tickets),
                "amount": str(existing.order_total),
                "issued": ticket.status == Ticket.Status.ISSUED,
                "pdf_url": f"/api/tickets/{ticket.pk}/pdf/" if ticket.pdf else None,
                "replayed": True,
                "sheet_sync_status": _combined_sheet_sync_status(sync_jobs),
            })
        try:
            with transaction.atomic():
                verified_at = timezone.now()
                verified_by = request.user.email
                registration = Registration.objects.create(
                    idempotency_key=idempotency_key,
                    event_id=data["event_id"],
                    source=Registration.Source.ON_SPOT,
                    buyer_name=data["attendee_name"],
                    buyer_email=data["attendee_email"],
                    buyer_phone=data.get("attendee_phone", ""),
                    order_total=data["amount"],
                    payment_method=data["payment_method"],
                    transaction_id=data.get("transaction_reference", ""),
                    payment_status=Registration.PaymentStatus.VERIFIED,
                    imported_payment_status=Registration.PaymentStatus.VERIFIED,
                    imported_verified_by=verified_by,
                    imported_verified_at=verified_at,
                    created_by=request.user,
                )
                PaymentVerification.objects.create(
                    registration=registration,
                    transaction_id=data.get("transaction_reference", ""),
                    decision=Registration.PaymentStatus.VERIFIED,
                    verifier=request.user,
                    note="On-spot payment confirmed at registration.",
                )
        except IntegrityError:
            if Registration.objects.filter(idempotency_key=idempotency_key).exists():
                return Response(
                    {"detail": "A registration with this Idempotency-Key was just created. Retry the same request."},
                    status=status.HTTP_409_CONFLICT,
                )
            raise
        tickets = []
        desk_name = request.user.desk.replace("_", " ") if request.user.desk else "ADMIN"
        for index in range(data["ticket_count"]):
            ticket = Ticket.objects.create(
                ticket_number=next_offline_ticket_number(),
                registration=registration,
                ticket_type=data["ticket_type"],
                attendee_name=data["attendee_names"][index],
                attendee_email=data["attendee_email"],
                attendee_phone=data.get("attendee_phone", ""),
            )
            ticket, _credential = issue_ticket(ticket.pk, actor=request.user)
            checked_in_at = timezone.now()
            ticket.checked_in_at = checked_in_at
            ticket.checked_in_by = request.user
            ticket.save(update_fields=("checked_in_at", "checked_in_by", "updated_at"))
            CheckIn.objects.create(
                ticket=ticket,
                scanner=request.user,
                gate=f"On-spot {desk_name}",
                result=CheckIn.Result.ACCEPTED,
                method=CheckIn.Method.MANUAL,
                reason="Payment verified and attendee admitted at on-spot registration.",
            )
            AuditLog.objects.create(
                actor=request.user,
                action="ON_SPOT_REGISTRATION_CHECKED_IN",
                object_type="Ticket",
                object_id=str(ticket.pk),
                details={
                    "registration_id": str(registration.pk),
                    "desk": request.user.desk,
                    "payment_status": Registration.PaymentStatus.VERIFIED,
                    "checked_in_at": checked_in_at.isoformat(),
                },
            )
            tickets.append(ticket)
        ticket = tickets[0]
        response = Response(
            {
                "registration_id": str(registration.pk),
                "ticket": TicketSerializer(ticket).data,
                "tickets": TicketSerializer(tickets, many=True).data,
                "quantity": data["quantity"],
                "ticket_count": len(tickets),
                "amount": str(registration.order_total),
                "issued": True,
                "pdf_url": f"/api/tickets/{ticket.pk}/pdf/" if ticket.pdf else None,
                "replayed": False,
                "sheet_sync_status": "PENDING" if sheets_configured() else "NOT_CONFIGURED",
            },
            status=status.HTTP_201_CREATED,
        )

        def sync_on_spot_ticket():
            if not sheets_configured():
                response.data["sheet_sync_status"] = "NOT_CONFIGURED"
                return
            for item in tickets:
                process_sync_jobs(limit=1, ticket_id=item.pk)
            sync_jobs = [item.sync_jobs.order_by("-created_at").first() for item in tickets]
            response.data["sheet_sync_status"] = _combined_sheet_sync_status(sync_jobs)

        transaction.on_commit(sync_on_spot_ticket, robust=True)
        return response


class DeskHistoryView(APIView):
    permission_classes = [IsAdminOrRegistrationDesk]

    def get(self, request):
        registrations = Registration.objects.filter(source=Registration.Source.ON_SPOT)
        if request.user.role == User.Role.DESK:
            registrations = registrations.filter(created_by=request.user)
        else:
            desk_id = request.query_params.get("desk_id")
            if desk_id:
                registrations = registrations.filter(
                    created_by_id=desk_id,
                    created_by__role=User.Role.DESK,
                )

        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(100, max(1, int(request.query_params.get("page_size", 25))))
        total_orders = registrations.count()
        totals = registrations.aggregate(
            amount=Sum("order_total"),
            cash=Sum("order_total", filter=Q(payment_method="CASH")),
            upi=Sum("order_total", filter=Q(payment_method="UPI")),
            card=Sum("order_total", filter=Q(payment_method="CARD")),
        )
        tickets_sold = Ticket.objects.filter(registration__in=registrations).count()
        page_items = (
            registrations.select_related("created_by", "event")
            .prefetch_related("tickets")
            .annotate(ticket_count=Count("tickets"))
            .order_by("-created_at")[(page - 1) * page_size : page * page_size]
        )
        results = [
            {
                "id": str(registration.pk),
                "created_at": registration.created_at,
                "desk": registration.created_by.desk if registration.created_by else "",
                "desk_name": registration.created_by.name if registration.created_by else "",
                "event_name": registration.event.name,
                "attendee_name": registration.buyer_name,
                "attendee_email": registration.buyer_email,
                "attendee_phone": registration.buyer_phone,
                "payment_method": registration.payment_method,
                "transaction_reference": registration.transaction_id,
                "payment_status": registration.payment_status,
                "amount": str(registration.order_total or 0),
                "ticket_count": registration.ticket_count,
                "tickets": [
                    {
                        "ticket_number": ticket.ticket_number,
                        "attendee_name": ticket.attendee_name,
                        "status": ticket.status,
                        "checked_in_at": ticket.checked_in_at,
                    }
                    for ticket in registration.tickets.all()
                ],
            }
            for registration in page_items
        ]
        return Response({
            "count": total_orders,
            "page": page,
            "page_size": page_size,
            "tickets_sold": tickets_sold,
            "total": str(totals["amount"] or 0),
            "cash_total": str(totals["cash"] or 0),
            "upi_total": str(totals["upi"] or 0),
            "card_total": str(totals["card"] or 0),
            "results": results,
        })


def _combined_sheet_sync_status(sync_jobs):
    statuses = [job.status for job in sync_jobs if job]
    if any(value == SpreadsheetSyncJob.Status.FAILED for value in statuses):
        return SpreadsheetSyncJob.Status.FAILED
    if statuses and all(value == SpreadsheetSyncJob.Status.SUCCEEDED for value in statuses):
        return SpreadsheetSyncJob.Status.SUCCEEDED
    if not sheets_configured():
        return "NOT_CONFIGURED"
    return SpreadsheetSyncJob.Status.PENDING

class IssueTicketView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, ticket_id):
        try:
            ticket, _ = issue_ticket(ticket_id, actor=request.user)
        except Ticket.DoesNotExist as error:
            raise Http404 from error
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        return Response(TicketSerializer(ticket).data)


class TicketStatusView(APIView):
    permission_classes = [IsAdmin]

    @transaction.atomic
    def post(self, request, ticket_id):
        serializer = TicketStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_status = serializer.validated_data["status"]
        try:
            ticket = Ticket.objects.select_for_update().get(pk=ticket_id)
        except Ticket.DoesNotExist as error:
            raise Http404 from error
        if ticket.status == target_status:
            return Response(TicketSerializer(ticket).data)
        if ticket.status != Ticket.Status.ISSUED:
            return Response(
                {"detail": "Only issued tickets can be cancelled or revoked."},
                status=status.HTTP_409_CONFLICT,
            )
        ticket.status = target_status
        ticket.save(update_fields=("status", "updated_at"))
        AuditLog.objects.create(
            actor=request.user,
            action=f"TICKET_{target_status}",
            object_type="Ticket",
            object_id=str(ticket.pk),
            details={"reason": serializer.validated_data.get("reason", "")},
        )
        queue_ticket_sync(ticket.pk)
        return Response(TicketSerializer(ticket).data)


class TicketEmailView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, ticket_id):
        try:
            send_ticket_email(ticket_id, request.user)
        except Ticket.DoesNotExist as error:
            raise Http404 from error
        except EmailDeliveryConflict as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        except EmailDeliveryError as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"email_status": Ticket.EmailStatus.SENT}, status=status.HTTP_200_OK)


class TicketPdfView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ticket_id):
        if request.user.role != User.Role.ADMIN:
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            ticket = Ticket.objects.get(pk=ticket_id, status=Ticket.Status.ISSUED)
        except Ticket.DoesNotExist as error:
            raise Http404 from error
        if not ticket.pdf:
            raise Http404
        return FileResponse(ticket.pdf.open("rb"), content_type="application/pdf")


class ScanValidationView(APIView):
    permission_classes = [IsAdminOrScanner]
    parser_classes = [JSONParser]

    def post(self, request):
        credential = request.data.get("credential")
        if not isinstance(credential, str) or not 1 <= len(credential.strip()) <= 256:
            return Response({"detail": "A valid scan credential is required."}, status=400)
        method = request.data.get("method", CheckIn.Method.QR)
        if method not in CheckIn.Method.values:
            return Response({"detail": "Method must be QR or MANUAL."}, status=400)
        validation_result, ticket = validate_ticket_credential(credential)
        if validation_result != "VALID":
            _record_scan_attempt(
                request.user,
                credential,
                ticket=ticket,
                result=getattr(CheckIn.Result, validation_result, CheckIn.Result.INVALID),
                gate=_scan_gate(request),
                method=method,
            )
        payload = {
            "result": validation_result,
            "ticket_id": str(ticket.pk) if ticket else None,
            "ticket_number": ticket.ticket_number if ticket else None,
            "attendee_name": ticket.attendee_name if ticket else None,
            "ticket_type": ticket.ticket_type if ticket else None,
            "checked_in_at": ticket.checked_in_at if ticket else None,
        }
        return Response(payload, status=200)


class ScanCheckInView(APIView):
    permission_classes = [IsAdminOrScanner]
    parser_classes = [JSONParser]

    def post(self, request):
        credential = request.data.get("credential")
        if not isinstance(credential, str) or not 1 <= len(credential.strip()) <= 256:
            return Response({"detail": "A valid scan credential is required."}, status=400)
        method = request.data.get("method", CheckIn.Method.QR)
        if method not in CheckIn.Method.values:
            return Response({"detail": "Method must be QR or MANUAL."}, status=400)
        gate = _scan_gate(request)
        if request.user.role == User.Role.SCANNER and not gate:
            return Response({"detail": "This scanner account is not assigned to a gate."}, status=403)

        validation_result, ticket = validate_ticket_credential(credential)
        if validation_result != "VALID":
            _record_scan_attempt(
                request.user,
                credential,
                ticket=ticket,
                result=getattr(CheckIn.Result, validation_result, CheckIn.Result.INVALID),
                gate=gate,
                method=method,
            )
            return Response({"result": validation_result, "ticket_number": ticket.ticket_number if ticket else None}, status=409)

        result, ticket = check_in_ticket(
            credential=credential,
            scanner=request.user,
            gate=gate,
            method=method,
        )
        data = {"result": result, "checked_in_at": ticket.checked_in_at if ticket else None}
        if ticket:
            data.update({
                "ticket_number": ticket.ticket_number,
                "attendee_name": ticket.attendee_name,
                "ticket_type": ticket.ticket_type,
            })
        return Response(data, status=status.HTTP_200_OK if result == CheckIn.Result.ACCEPTED else status.HTTP_409_CONFLICT)


class ScanView(APIView):
    permission_classes = [IsAdminOrScanner]
    parser_classes = [JSONParser]

    def post(self, request):
        credential = request.data.get("credential")
        if not isinstance(credential, str) or not 1 <= len(credential.strip()) <= 256:
            return Response({"detail": "A valid scan credential is required."}, status=400)
        gate = _scan_gate(request)
        if request.user.role == User.Role.SCANNER and not gate:
            return Response({"detail": "This scanner account is not assigned to a gate."}, status=403)

        validation_result, ticket = validate_ticket_credential(credential)
        if validation_result == "VALID":
            result, ticket = check_in_ticket(
                credential=credential,
                scanner=request.user,
                gate=gate,
            )
            code = status.HTTP_200_OK if result == CheckIn.Result.ACCEPTED else status.HTTP_409_CONFLICT
            data = {"result": result, "checked_in_at": ticket.checked_in_at if ticket else None}
            if ticket:
                data.update({
                    "ticket_number": ticket.ticket_number,
                    "attendee_name": ticket.attendee_name,
                    "ticket_type": ticket.ticket_type,
                })
            return Response(data, status=code)

        _record_scan_attempt(
            request.user,
            credential,
            ticket=ticket,
            result=getattr(CheckIn.Result, validation_result, CheckIn.Result.INVALID),
            gate=gate,
        )
        data = {"result": validation_result, "checked_in_at": ticket.checked_in_at if ticket else None}
        if ticket:
            data.update({
                "ticket_number": ticket.ticket_number,
                "attendee_name": ticket.attendee_name,
                "ticket_type": ticket.ticket_type,
            })
        return Response(data, status=status.HTTP_409_CONFLICT)


class ScanHistoryView(APIView):
    permission_classes = [IsAdminOrScanner]

    def get(self, request):
        scans = CheckIn.objects.select_related("ticket", "scanner")
        if request.user.role != User.Role.ADMIN:
            scans = scans.filter(scanner=request.user)
        return Response(CheckInSerializer(scans[:100], many=True).data)


class ImportPreviewView(APIView):
    permission_classes = [IsAdmin]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded = request.FILES.get("file")
        try:
            event = Event.objects.get(pk=request.data.get("event_id"), is_active=True)
            batch, unknown = create_import_batch(uploaded=uploaded, event=event, actor=request.user)
        except Event.DoesNotExist as error:
            raise Http404("Select an active event.") from error
        result = batch_preview(batch, unknown)
        result["error_report_url"] = f"/api/admin/import/{batch.pk}/errors.csv/"
        return Response(result, status=status.HTTP_201_CREATED)


class GoogleSheetImportPreviewView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        worksheet = request.data.get("worksheet")
        if worksheet not in {"Online_Tickets", "OnSpot_Tickets"}:
            return Response(
                {"detail": "Choose Online_Tickets or OnSpot_Tickets."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not sheets_configured():
            return Response(
                {"detail": "Google Sheets import is not configured on the backend."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            event = Event.objects.get(pk=request.data.get("event_id"), is_active=True)
        except Event.DoesNotExist as error:
            raise Http404("Select an active event.") from error

        try:
            rows = read_worksheet_rows(worksheet)
        except (HttpError, GoogleAuthError, OSError, ValueError) as error:
            logger.warning(
                "Google Sheets import preview failed (%s).",
                type(error).__name__,
            )
            return Response(
                {"detail": "Could not read the configured Google Sheet. Check the sheet ID, sharing, worksheet, and service-account configuration."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not rows:
            return Response(
                {"detail": f"The {worksheet} worksheet has no header row or data."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerows(rows)
        upload = SimpleUploadedFile(
            f"{worksheet}.csv",
            output.getvalue().encode("utf-8"),
            content_type="text/csv",
        )
        batch, unknown = create_import_batch(
            uploaded=upload,
            event=event,
            actor=request.user,
            registration_source=(
                Registration.Source.ONLINE
                if worksheet == "Online_Tickets"
                else Registration.Source.ON_SPOT
            ),
        )
        result = batch_preview(batch, unknown)
        result["source_worksheet"] = worksheet
        result["error_report_url"] = f"/api/admin/import/{batch.pk}/errors.csv/"
        return Response(result, status=status.HTTP_201_CREATED)


class ImportCommitView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, batch_id):
        if request.data.get("confirm") is not True:
            return Response({"detail": "Set confirm=true to commit this import."}, status=400)
        try:
            result = commit_import_batch(batch_id, request.user)
        except ImportBatch.DoesNotExist as error:
            raise Http404 from error
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        result["batch_id"] = str(batch_id)
        result["error_report_url"] = f"/api/admin/import/{batch_id}/errors.csv/"
        return Response(result)


class ImportErrorReportView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, batch_id):
        try:
            batch = ImportBatch.objects.get(pk=batch_id, requested_by=request.user)
        except ImportBatch.DoesNotExist as error:
            raise Http404 from error
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="import-errors-{batch.pk}.csv"'
        writer = csv.writer(response)
        writer.writerow(("row", "ticket_number", "action", "errors"))
        for row in batch.rows:
            if row["errors"]:
                writer.writerow((
                    row["row"],
                    _csv_safe(row["ticket_number"]),
                    row["action"],
                    _csv_safe("; ".join(row["errors"])),
                ))
        return response


def _csv_safe(value):
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _scan_gate(request):
    if request.user.role == User.Role.SCANNER:
        return request.user.gate
    return str(request.data.get("gate", ""))


class UserListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        users = User.objects.filter(role=User.Role.SCANNER).order_by("gate", "email")
        return Response(UserSerializer(users, many=True).data)

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        name = str(request.data.get("name", "")).strip()
        password = request.data.get("password")
        gate = str(request.data.get("gate", "")).strip().upper()
        if gate not in {f"GATE_{number}" for number in range(1, 5)}:
            return Response({"detail": "Choose one of the four scanner gates."}, status=400)
        if not email or not password or len(password) < 12:
            return Response({"detail": "Email and a password of at least 12 characters are required."}, status=400)
        if User.objects.filter(email__iexact=email).exists():
            return Response({"detail": "A user already uses this login ID."}, status=409)
        if User.objects.filter(gate=gate, role=User.Role.SCANNER).exists():
            return Response({"detail": "A scanner account is already assigned to this gate."}, status=409)
        user = User.objects.create_user(email=email, password=password, name=name, role=User.Role.SCANNER)
        user.gate = gate
        user.save(update_fields=("gate",))
        AuditLog.objects.create(
            actor=request.user,
            action="SCANNER_CREATED",
            object_type="User",
            object_id=str(user.pk),
            details={"gate": gate},
        )
        return Response(UserSerializer(user).data, status=201)

    def patch(self, request, user_id=None):
        if not user_id:
            return Response(status=404)
        try:
            user = User.objects.get(pk=user_id, role=User.Role.SCANNER)
        except User.DoesNotExist as error:
            raise Http404 from error
        update_fields = []
        if "is_active" in request.data:
            user.is_active = bool(request.data["is_active"])
            update_fields.append("is_active")
        if "gate" in request.data:
            gate = str(request.data.get("gate", "")).strip().upper()
            if gate not in {f"GATE_{number}" for number in range(1, 5)}:
                return Response({"detail": "Choose one of the four scanner gates."}, status=400)
            if User.objects.filter(gate=gate, role=User.Role.SCANNER).exclude(pk=user.pk).exists():
                return Response({"detail": "A scanner account is already assigned to this gate."}, status=409)
            user.gate = gate
            update_fields.append("gate")
        if "password" in request.data:
            password = request.data["password"]
            if not isinstance(password, str) or len(password) < 12:
                return Response({"detail": "A password of at least 12 characters is required."}, status=400)
            user.set_password(password)
            update_fields.append("password")
        if update_fields:
            user.save(update_fields=update_fields)
            AuditLog.objects.create(
                actor=request.user,
                action="SCANNER_UPDATED",
                object_type="User",
                object_id=str(user.pk),
                details={"updated_fields": sorted(set(update_fields))},
            )
        return Response(UserSerializer(user).data)


class DeskUserListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        users = User.objects.filter(role=User.Role.DESK).order_by("desk", "email")
        return Response(UserSerializer(users, many=True).data)

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        name = str(request.data.get("name", "")).strip()
        password = request.data.get("password")
        desk = str(request.data.get("desk", "")).strip().upper()
        if desk not in {"DESK_1", "DESK_2"}:
            return Response({"detail": "Choose Desk 1 or Desk 2."}, status=400)
        if not email or not isinstance(password, str) or len(password) < 12:
            return Response({"detail": "Email and a password of at least 12 characters are required."}, status=400)
        if User.objects.filter(email__iexact=email).exists():
            return Response({"detail": "A user already uses this login ID."}, status=409)
        if User.objects.filter(desk=desk, role=User.Role.DESK).exists():
            return Response({"detail": "A registration account is already assigned to this desk."}, status=409)
        user = User.objects.create_user(
            email=email,
            password=password,
            name=name,
            role=User.Role.DESK,
            desk=desk,
        )
        AuditLog.objects.create(
            actor=request.user,
            action="REGISTRATION_DESK_CREATED",
            object_type="User",
            object_id=str(user.pk),
            details={"desk": desk},
        )
        return Response(UserSerializer(user).data, status=201)

    def patch(self, request, user_id=None):
        if not user_id:
            return Response(status=404)
        try:
            user = User.objects.get(pk=user_id, role=User.Role.DESK)
        except User.DoesNotExist as error:
            raise Http404 from error
        update_fields = []
        if "is_active" in request.data:
            user.is_active = bool(request.data["is_active"])
            update_fields.append("is_active")
        if "desk" in request.data:
            desk = str(request.data.get("desk", "")).strip().upper()
            if desk not in {"DESK_1", "DESK_2"}:
                return Response({"detail": "Choose Desk 1 or Desk 2."}, status=400)
            if User.objects.filter(desk=desk, role=User.Role.DESK).exclude(pk=user.pk).exists():
                return Response({"detail": "A registration account is already assigned to this desk."}, status=409)
            user.desk = desk
            update_fields.append("desk")
        if "password" in request.data:
            password = request.data["password"]
            if not isinstance(password, str) or len(password) < 12:
                return Response({"detail": "A password of at least 12 characters is required."}, status=400)
            user.set_password(password)
            update_fields.append("password")
        if update_fields:
            user.save(update_fields=update_fields)
            AuditLog.objects.create(
                actor=request.user,
                action="REGISTRATION_DESK_UPDATED",
                object_type="User",
                object_id=str(user.pk),
                details={"updated_fields": sorted(set(update_fields))},
            )
        return Response(UserSerializer(user).data)
