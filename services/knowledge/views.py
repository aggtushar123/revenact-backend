from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.accounts.models import User
from services.accounts.permissions import CanViewAllAccounts
from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
)
from services.customers.models import Customer

from . import brief, mentions
from .gaps import fill_from_contribution
from .models import Contribution, FunctionOwner, KnowledgeGap, Question
from .serializers import ContributionSerializer, KnowledgeGapSerializer, QuestionSerializer


def _company_customer(request, pk):
    """Any customer in the caller's organisation — knowledge is company-wide,
    so this deliberately does not apply the CSM book scoping."""
    return get_object_or_404(Customer, organisation=request.user.organisation, pk=pk)


class CustomerContributionListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<id>/contributions/ — what every function
    knows about this customer, newest first; and one more piece of it. Any
    member of the organisation may read and write: the author's function
    is stamped from their profile."""

    serializer_class = ContributionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = _company_customer(self.request, self.kwargs["pk"])
        queryset = visible_contributions(self.request.user, customer.contributions).select_related(
            "author", "customer"
        )
        wanted = self.request.query_params.get("function")
        if wanted in User.Function.values:
            queryset = queryset.filter(function=wanted)
        return queryset

    def perform_create(self, serializer):
        customer = _company_customer(self.request, self.kwargs["pk"])
        with transaction.atomic():
            contribution = serializer.save(
                organisation=self.request.user.organisation,
                customer=customer,
                author=self.request.user,
                function=self.request.user.function,
            )
            # Writing something down is how a gap closes, wherever it is
            # written: the Company View box and the gap's own answer box
            # are the same act (services.knowledge.gaps). One transaction,
            # so a contribution never lands with only some of the gaps it
            # answers marked.
            fill_from_contribution(contribution, request=self.request)


class ContributionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/contributions/<id>/ — the author may edit or
    remove their own; someone who manages users may remove anyone's."""

    serializer_class = ContributionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return visible_contributions(
            self.request.user,
            Contribution.objects.filter(organisation=self.request.user.organisation),
        )

    def _check(self, obj):
        user = self.request.user
        if obj.author_id != user.id and not user.has_capability("manage_users"):
            raise PermissionDenied("Only the author can change this.")

    def perform_update(self, serializer):
        self._check(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check(instance)
        instance.delete()


class CustomerResponsibleView(APIView):
    """GET/PATCH /api/v1/customers/<id>/responsible/ — who answers for this
    customer in each function. CS is the customer's owner; the others are
    FunctionOwner rows. PATCH `{"function": "engineering", "user_id": 7}`
    (or null to clear) needs view-all-accounts; reading is for everyone."""

    permission_classes = [IsAuthenticated]

    @staticmethod
    def _payload(customer):
        owners = {fo.function: fo.user for fo in customer.function_owners.select_related("user")}
        if customer.owner_id:
            owners.setdefault(User.Function.CS, customer.owner)
        return {
            "customer_id": customer.id,
            "account_owner": (
                {
                    "id": customer.owner.id,
                    "name": customer.owner.name,
                    "function": customer.owner.function,
                }
                if customer.owner_id
                else None
            ),
            "responsible": [
                {
                    "function": function,
                    "function_display": label,
                    "user": (
                        {"id": owners[function].id, "name": owners[function].name}
                        if function in owners
                        else None
                    ),
                }
                for function, label in User.Function.choices
            ],
        }

    def get(self, request, pk):
        return Response(self._payload(_company_customer(request, pk)))

    def patch(self, request, pk):
        if not CanViewAllAccounts().has_permission(request, self):
            raise PermissionDenied("Setting who is responsible needs view-all-accounts.")
        customer = _company_customer(request, pk)
        function = request.data.get("function")
        if function not in User.Function.values:
            return Response({"detail": "Unknown function."}, status=status.HTTP_400_BAD_REQUEST)
        user_id = request.data.get("user_id")
        user = (
            None
            if user_id is None
            else get_object_or_404(User, pk=user_id, organisation=request.user.organisation)
        )
        if function == User.Function.CS:
            # The CS row is the account owner: gated and written down like
            # every other owner change (services.knowledge.ownership).
            from services.notifications.models import Notification
            from services.notifications.realtime import notify

            from .ownership import may_change_owner, record_handover

            if not may_change_owner(request.user, customer):
                raise PermissionDenied(
                    "Only the current account owner, their management chain or an "
                    "organisation-settings manager can reassign this account."
                )
            previous = customer.owner
            customer.owner = user
            customer.save(update_fields=["owner"])
            record_handover(customer, request.user, previous, str(request.data.get("note") or ""))
            if user is not None and user.id != request.user.id and previous != user:
                notify(
                    recipient=user,
                    actor=request.user,
                    kind=Notification.Kind.CUSTOMER_ASSIGNED,
                    message=f"{request.user.name} made you the account owner of {customer.name}.",
                    link=f"/organizations/{customer.id}",
                )
        elif user is None:
            customer.function_owners.filter(function=function).delete()
        elif user.function != function:
            # A CSM cannot be the engineering owner: whoever answers for a
            # function on this customer works in that function.
            return Response(
                {
                    "detail": f"{user.name} is in {user.get_function_display()}, not "
                    f"{User.Function(function).label}. Pick someone from "
                    f"{User.Function(function).label}."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        else:
            FunctionOwner.objects.update_or_create(
                customer=customer, function=function, defaults={"user": user}
            )
        return Response(self._payload(customer))


def responsible_for_q():
    """Responsibility grants reading: the people who answer for a customer
    — its CS owner and its function owners — see every function's notes
    and questions on *that* customer, whatever the chart says. Someone
    responsible for an account cannot do their job blind to what the
    other functions wrote about it."""
    from django.db.models import Q

    def q(user):
        return Q(customer__owner=user) | Q(customer__function_owners__user=user)

    return q


def visible_contributions(user, queryset):
    """The scope rule (services.accounts.hierarchy), plus what is addressed
    to `user` (an answer to a question they asked), plus every note on a
    customer they are responsible for."""
    from django.db.models import Q

    from services.accounts.hierarchy import scope_ids

    return queryset.filter(
        Q(author_id__in=scope_ids(user))
        | Q(answers_question__asked_by=user)
        | responsible_for_q()(user)
    ).distinct()


def visible_questions(user, queryset):
    """Questions asked by someone in `user`'s scope, routed to them or to
    anyone who reports to them, or on a customer they are responsible for."""
    from django.db.models import Q

    from services.accounts.hierarchy import scope_ids, subtree_ids

    return queryset.filter(
        Q(asked_by_id__in=scope_ids(user))
        | Q(assignee=user)
        | Q(assignee_id__in=subtree_ids(user))
        | responsible_for_q()(user)
    ).distinct()


def _question_queryset(request):
    return visible_questions(
        request.user,
        Question.objects.filter(organisation=request.user.organisation).select_related(
            "customer", "asked_by", "assignee", "answer__author", "answer__customer"
        ),
    )


class CustomerQuestionListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<id>/questions/ — the questions on this
    customer, open first; and a new one. POST `{"text", "assignee_id"?}`:
    with an assignee it goes to them, otherwise to whoever the text
    @mentions (`400` if nobody). One notification per person asked."""

    serializer_class = QuestionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = _company_customer(self.request, self.kwargs["pk"])
        rows = list(_question_queryset(self.request).filter(customer=customer))
        rows.sort(key=lambda q: (q.status != Question.Status.OPEN, -q.created_at.timestamp()))
        return rows

    def create(self, request, pk):
        customer = _company_customer(request, pk)
        text = str(request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "Ask something."}, status=status.HTTP_400_BAD_REQUEST)
        assignees = None
        if request.data.get("assignee_id") is not None:
            person = get_object_or_404(
                User, pk=request.data["assignee_id"], organisation=request.user.organisation
            )
            if person.id == request.user.id:
                return Response(
                    {"detail": "You can't route a question to yourself."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            assignees = [person]
        # Asked from under a Copilot answer: the question keeps the turn it
        # came from, so the chat can show whom that turn ended up asking.
        message = None
        if request.data.get("message_id") is not None:
            from services.copilot.models import Message
            from services.copilot.views import conversations_visible_to

            message = get_object_or_404(
                Message,
                pk=request.data["message_id"],
                conversation__in=conversations_visible_to(request.user),
            )
            # Forwarding someone else's question — the one-click ask under a
            # reply to a turn the caller did not write — says whose it was,
            # so the person asked knows who is actually waiting.
            if message.author_id and message.author_id != request.user.id:
                text = f'{message.author.name} asked: "{text}" — can you answer?'
        created = mentions.route_questions(
            organisation=request.user.organisation,
            asked_by=request.user,
            text=text,
            customer=customer,
            message=message,
            assignees=assignees,
        )
        if not created:
            return Response(
                {"detail": "Say who should answer — @mention them, or pick a person."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(QuestionSerializer(created, many=True).data, status=status.HTTP_201_CREATED)


class QuestionListView(generics.ListAPIView):
    """GET /api/v1/questions/ — every question in the organisation, open
    first. `?mine=true` narrows to the ones waiting on the caller;
    `?asked=true` to the ones they asked; `?status=` narrows."""

    serializer_class = QuestionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        rows = _question_queryset(self.request)
        params = self.request.query_params
        if params.get("mine") == "true":
            rows = rows.filter(assignee=self.request.user)
        if params.get("asked") == "true":
            rows = rows.filter(asked_by=self.request.user)
        if params.get("status") in Question.Status.values:
            rows = rows.filter(status=params["status"])
        if params.get("stale") == "true":
            from .aging import STALE_DAYS, stale_open_questions

            rows = rows.filter(
                pk__in=stale_open_questions(self.request.user.organisation, STALE_DAYS)
            )
        rows = list(rows)
        rows.sort(key=lambda q: (q.status != Question.Status.OPEN, -q.created_at.timestamp()))
        return rows


class QuestionAnswerView(APIView):
    """POST /api/v1/questions/<id>/answer/ `{"body"}` — the person asked
    (or someone who manages users) answers. The answer is stored as a
    contribution from their function, so it is knowledge from then on;
    the asker is told. A question is answered once (`409`)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        question = get_object_or_404(_question_queryset(request), pk=pk)
        user = request.user
        if question.assignee_id != user.id and not user.has_capability("manage_users"):
            raise PermissionDenied("This question was asked of someone else.")
        if question.status == Question.Status.ANSWERED:
            return Response({"detail": "Already answered."}, status=status.HTTP_409_CONFLICT)
        body = str(request.data.get("body") or "").strip()
        if not body:
            return Response({"detail": "Say something."}, status=status.HTTP_400_BAD_REQUEST)
        mentions.answer_question(question, user, body)
        question.refresh_from_db()
        return Response(QuestionSerializer(question).data)


class KnowledgeActivityView(APIView):
    """GET /api/v1/knowledge/activity/?days=30 — per function: members,
    contributors, contributions, questions asked of them, answered by them,
    waiting on them, and how long they take. Organisation-wide, so gated
    like the Brain."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from .activity import by_function

        try:
            days = max(1, min(365, int(request.query_params.get("days", 30))))
        except (TypeError, ValueError):
            days = 30
        return Response(by_function(request.user.organisation, days))


class KnowledgeGapListView(APIView):
    """GET /api/v1/knowledge/gaps/?customer=&status= — what the company
    cannot answer, most-asked first. Open by default; `status` reads the
    closed ones.

    Company-wide, like every other part of the knowledge layer (see
    `_company_customer`): the person who owes an answer is usually in
    another function and does not own the account, so scoping this to a
    CSM's own book would hide the question from exactly the person who
    could close it. A gap carries a subject and a count, never a record's
    words, so there is nothing personal in it to protect."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        wanted = request.query_params.get("status") or KnowledgeGap.Status.OPEN
        if wanted not in KnowledgeGap.Status.values:
            return Response({"detail": f"Unknown status {wanted!r}."}, status=400)
        rows = KnowledgeGap.objects.filter(
            organisation=request.user.organisation, status=wanted
        ).select_related("customer", "assignee")
        customer_id = request.query_params.get("customer")
        if customer_id:
            if not str(customer_id).isdigit():
                return Response({"detail": "customer must be an id."}, status=400)
            rows = rows.filter(customer_id=customer_id)
        return Response(KnowledgeGapSerializer(rows, many=True).data)


def _visible_gap(request, pk):
    return get_object_or_404(
        KnowledgeGap.objects.filter(organisation=request.user.organisation), pk=pk
    )


class KnowledgeGapAnswerView(APIView):
    """POST /api/v1/knowledge/gaps/<id>/answer/ {body} — write the answer
    down and close the loop. The answer is an ordinary Contribution, so it
    lives with everything else the company knows rather than in a second
    place nobody reads."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        gap = _visible_gap(request, pk)
        if gap.status != KnowledgeGap.Status.OPEN:
            return Response(
                {"detail": "This one is already closed."}, status=status.HTTP_409_CONFLICT
            )
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response({"detail": "An answer needs a body."}, status=400)
        with transaction.atomic():
            contribution = Contribution.objects.create(
                organisation=request.user.organisation,
                customer=gap.customer,
                author=request.user,
                function=request.user.function,
                body=body,
            )
            fill_from_contribution(contribution, request=request)
            gap.refresh_from_db()
            if gap.status != KnowledgeGap.Status.FILLED:
                # Answered by someone from another function: the gap still
                # belongs to whoever owes it, but the knowledge is recorded.
                gap.status = KnowledgeGap.Status.FILLED
                gap.filled_by = contribution
                gap.save(update_fields=["status", "filled_by"])
        return Response(
            {
                "gap": KnowledgeGapSerializer(gap).data,
                "contribution": ContributionSerializer(contribution).data,
            },
            status=status.HTTP_201_CREATED,
        )


class KnowledgeGapDismissView(APIView):
    """POST /api/v1/knowledge/gaps/<id>/dismiss/ — not worth answering."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        gap = _visible_gap(request, pk)
        if gap.status != KnowledgeGap.Status.OPEN:
            return Response(
                {"detail": "This one is already closed."}, status=status.HTTP_409_CONFLICT
            )
        gap.status = KnowledgeGap.Status.DISMISSED
        gap.save(update_fields=["status"])
        audit.record(
            "knowledge.gap_dismissed",
            request=request,
            target=gap,
            metadata={"subject": gap.subject, "customer": gap.customer.name},
        )
        return Response(KnowledgeGapSerializer(gap).data)


class AccountBriefView(APIView):
    """GET/POST /api/v1/customers/<id>/brief/ — the standing brief on this
    account, and writing a new one.

    GET never 404s on a customer that simply has no brief yet: an empty
    brief is the honest answer, and the page needs the open gaps beside it
    either way. POST costs a model call, so it only happens when asked."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        customer = _company_customer(request, pk)
        return Response(brief.as_seen_by(customer, request.user))

    def post(self, request, pk):
        customer = _company_customer(request, pk)
        if not request.user.organisation.ai_agent_enabled:
            return Response({"detail": "AI Copilot is disabled for your organisation."}, status=403)
        try:
            brief.generate(customer, actor=request.user, request=request)
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(brief.as_seen_by(customer, request.user), status=status.HTTP_201_CREATED)
