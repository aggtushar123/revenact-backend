"""The ledger is the truth, and it cannot lie: append-only, every row carries
the balance, the database refuses a negative one, a reference applies once."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.billing import accounts, ledger
from services.billing.models import CreditLedger


class LedgerTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.account = accounts.ensure(self.org)
        self.staff = User.objects.create_superuser(email="s@revenact.io", password="x", name="S")

    def test_a_new_organisation_starts_with_the_trial_credits_once(self):
        self.assertEqual(ledger.balance(self.account), 200)
        accounts.ensure(self.org)
        self.assertEqual(ledger.balance(self.account), 200, "ensure is idempotent")
        self.assertEqual(CreditLedger.objects.filter(account=self.account).count(), 1)

    def test_every_row_carries_the_balance_after(self):
        ledger.consume(self.account, 5, reason="test", reference="a")
        ledger.grant(self.account, 10, reason="test", reference="b")
        rows = list(CreditLedger.objects.filter(account=self.account).order_by("id"))
        self.assertEqual([r.balance_after for r in rows], [200, 195, 205])
        self.assertEqual(ledger.balance(self.account), 205)

    def test_consuming_more_than_the_balance_is_refused_and_writes_nothing(self):
        with self.assertRaises(ledger.BillingError) as caught:
            ledger.consume(self.account, 201, reason="too much")
        self.assertEqual(caught.exception.code, "INSUFFICIENT_CREDITS")
        self.assertEqual(ledger.balance(self.account), 200)

    def test_the_database_itself_refuses_a_negative_balance(self):
        """Even a logic bug that bypasses ledger.py cannot persist one."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CreditLedger.objects.create(
                    account=self.account, kind="consume", amount=-1, balance_after=-1
                )

    def test_rows_are_append_only(self):
        row = CreditLedger.objects.filter(account=self.account).first()
        row.amount = 999
        with self.assertRaises(TypeError):
            row.save()
        with self.assertRaises(TypeError):
            row.delete()

    def test_a_reference_applies_once(self):
        """A webhook delivered twice, or a retried charge, moves credits once."""
        ledger.grant(self.account, 50, reason="payment", reference="evt_1")
        with self.assertRaises(ledger.BillingError) as caught:
            ledger.grant(self.account, 50, reason="payment", reference="evt_1")
        self.assertEqual(caught.exception.code, "DUPLICATE_REFERENCE")
        self.assertEqual(ledger.balance(self.account), 250)

    def test_a_staff_adjustment_needs_a_reason_and_is_audited(self):
        with self.assertRaises(ledger.BillingError):
            ledger.adjust(self.account, 100, reason="", actor=self.staff)
        ledger.adjust(self.account, -50, reason="Goodwill reversal", actor=self.staff)
        self.assertEqual(ledger.balance(self.account), 150)
        event = AuditEvent.objects.get(action="billing.credits.adjusted")
        self.assertEqual(event.organisation, self.org)
        self.assertEqual(event.metadata["amount"], -50)

    def test_expire_all_zeroes_the_balance_once(self):
        ledger.expire_all(self.account, reason="trial over")
        self.assertEqual(ledger.balance(self.account), 0)
        self.assertIsNone(ledger.expire_all(self.account, reason="again"))
