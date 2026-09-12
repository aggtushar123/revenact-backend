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
