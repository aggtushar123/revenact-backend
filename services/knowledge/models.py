"""What the whole company knows about a customer.

Revenact's records were the CSM's: emails, notes, tickets, calls — one
function's view of an account. This app holds the rest of the company's:
an engineer's note that their SSO integration breaks on token refresh, a
sales rep's that the expansion is stalled on procurement, an analyst's that
usage fell 30% after the March release. Each is a `Contribution` — one
person, one function, one customer, plain text — and the Copilot reads
them all, so a question about an account is answered from everything the
company knows, with who said what.

**Company-wide by design.** A contribution is visible to every signed-in
member of the organisation, whatever their book. The point of a company
brain is that nothing is siloed; the CSM's own-book scoping stays on the
CSM dashboards, not on knowledge.

`FunctionOwner` says who is responsible for a customer in each function —
the engineer, the sales rep, the analyst — so "ask the responsible person"
is a lookup, not a guess. The CS owner stays `Customer.owner`; this table
holds the other functions.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from services.accounts.models import Organisation, User
from services.customers.models import Customer


class FunctionOwner(models.Model):
    """Who answers for this customer in one function."""

    customer = models.ForeignKey(Customer, related_name="function_owners", on_delete=models.CASCADE)
    function = models.CharField(max_length=16, choices=User.Function.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="function_ownerships", on_delete=models.CASCADE
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "function"], name="one_owner_per_function_per_customer"
            )
        ]

    def __str__(self):
        return f"{self.get_function_display()} for {self.customer}: {self.user}"

    def clean(self):
        # The engineering owner is an engineer: responsibility for a function
        # sits with someone in it. (The account owner, Customer.owner, is the
        # one role open to any function.)
        if self.user_id and self.user.function != self.function:
            raise ValidationError(
                {
                    "user": f"{self.user.name} is in {self.user.get_function_display()}, "
                    f"not {self.get_function_display()}."
                }
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Contribution(models.Model):
    """One person's knowledge about one customer, from their function."""

    organisation = models.ForeignKey(
        Organisation, related_name="contributions", on_delete=models.CASCADE
    )
    customer = models.ForeignKey(Customer, related_name="contributions", on_delete=models.CASCADE)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="contributions", on_delete=models.CASCADE
    )
    function = models.CharField(
        max_length=16,
        choices=User.Function.choices,
        help_text="The author's function when they wrote this — a snapshot, so a "
        "later move between teams does not relabel what they knew then.",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["customer", "-created_at"])]

    def __str__(self):
        return f"[{self.get_function_display()}] {self.author}: {self.body[:40]}"


class Question(models.Model):
    """A question routed to a person, on the record.

    "@Mei, why is their usage down?" — asked in the Copilot or on the
    Company View — becomes one of these: who asked, who must answer, about
    which customer, and whether it has been answered. The answer is stored
    as a `Contribution` from the answerer's function, so the next person who
    asks gets it from the Copilot without asking Mei again. That is the
    whole point: a question answered once is knowledge, not a thread.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"

    organisation = models.ForeignKey(
        Organisation, related_name="questions", on_delete=models.CASCADE
    )
    customer = models.ForeignKey(
        Customer,
        related_name="questions",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The customer the question is about, when it names one.",
    )
    asked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="questions_asked", on_delete=models.CASCADE
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="questions_to_answer", on_delete=models.CASCADE
    )
    text = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    answer = models.OneToOneField(
        Contribution,
        related_name="answers_question",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The contribution the answer was stored as.",
    )
    message = models.ForeignKey(
        "copilot.Message",
        related_name="questions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The Copilot message that asked it, when it was asked there.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    last_nudged_at = models.DateTimeField(
        null=True, blank=True, help_text="When the assignee was last reminded — see aging.py."
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.asked_by} → {self.assignee}: {self.text[:40]}"


class KnowledgeGap(models.Model):
    """Something the company cannot answer about a customer.

    Raised from two real events rather than invented: a Copilot question
    about a company where retrieval found nothing to go on, and a routed
    Question that went stale unanswered (services.knowledge.aging). Both
    mean the same thing — somebody wanted to know and nobody here knew.

    A gap holds the subject and how often it has come up, never the words
    of any record: it is a question, and questions are the company's, not
    one person's. Writing a Contribution on the customer from the function
    that should answer closes it, which is the whole loop: ask, notice
    nobody knows, write it down, stop being asked."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FILLED = "filled", "Filled"
        DISMISSED = "dismissed", "Dismissed"

    class Source(models.TextChoices):
        COPILOT = "copilot", "Asked the Copilot"
        QUESTION = "question", "Routed question went unanswered"

    organisation = models.ForeignKey(
        Organisation, related_name="knowledge_gaps", on_delete=models.CASCADE
    )
    customer = models.ForeignKey(Customer, related_name="knowledge_gaps", on_delete=models.CASCADE)
    subject = models.CharField(max_length=500)
    #: Lowercased subject, so the same question asked twice counts twice
    #: rather than raising a second gap. Stored because the uniqueness is
    #: the point, and a database can only enforce what it can see.
    fingerprint = models.CharField(max_length=500)
    function = models.CharField(
        max_length=16, choices=User.Function.choices, blank=True, default=""
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="knowledge_gaps",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.COPILOT)
    #: The routed question this came from, so a stale one is never raised twice.
    question = models.OneToOneField(
        "Question", related_name="gap", on_delete=models.CASCADE, null=True, blank=True
    )
    times_asked = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    filled_by = models.ForeignKey(
        Contribution, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    first_asked_at = models.DateTimeField(auto_now_add=True)
    last_asked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-times_asked", "-last_asked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "fingerprint"], name="one_open_gap_per_question_per_customer"
            )
        ]
        indexes = [models.Index(fields=["organisation", "status", "-times_asked"])]

    def __str__(self):
        return f"{self.customer}: {self.subject[:40]}"


class AccountBrief(models.Model):
    """What an account actually does with the product, written down once.

    Not a second Headline: a Headline is a card about what just happened,
    this is the standing answer to "what do they use us for, who cares
    about what, and what is still open". Regenerated when someone asks,
    never on a schedule — it costs a model call, and a brief that quietly
    rewrites itself is one nobody trusts.

    `sources` are citation snapshots like a Copilot answer's, and are
    filtered again for whoever reads them: the brief is generated as the
    customer's owner reads, which is wider than some readers."""

    customer = models.OneToOneField(Customer, related_name="brief", on_delete=models.CASCADE)
    use_cases = models.JSONField(default=list, blank=True)
    stakeholders = models.JSONField(default=list, blank=True)
    open_threads = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    generated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Brief for {self.customer}"
