"""A manager sees the accounts their reports own, all the way down — the
same org-chart rule `visible_customers` follows — and so the records on
them, in the /customers/<id>/ roll-ups and the organisation story. A peer
of the report does not."""

from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Activity, Contact, Customer
from services.customers.scoping import visible_accounts
from services.customers.tests.test_views import create_account


class ManagerSeesReportsAccounts(APITestCase):
    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=org, role=User.Role.CSM, **kw
        )
        self.manager = mk("manager@acme.io", "Manager")
        self.lead = mk("lead@acme.io", "Lead", reports_to=self.manager)
        self.rep = mk("rep@acme.io", "Rep", reports_to=self.lead)
        self.peer = mk("peer@acme.io", "Peer", reports_to=self.lead)
        # Unowned, so everyone may open the organisation; its account is the
        # rep's, two levels below the manager.
        self.customer = Customer.objects.create(organisation=org, name="Globex", owner=None)
        self.account = create_account(self.customer, name="Rep's div", owner=self.rep)
        self.contact = Contact.objects.create(account=self.account, name="Sam", email="s@g.com")
        self.activity = Activity.objects.create(
            account=self.account,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=timezone.localdate(),
        )

    def contacts(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(f"/api/v1/customers/{self.customer.pk}/contacts/")
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        return {row["id"] for row in rows}

    def story(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(f"/api/v1/organizations/{self.customer.pk}/story/")
        self.assertEqual(response.status_code, 200)
        return {(item["kind"], item["id"]) for item in response.data["items"]}

    def test_a_manager_sees_their_reports_account(self):
        self.assertIn(self.account, visible_accounts(self.manager))
        self.assertIn(self.account, visible_accounts(self.lead))
        self.assertNotIn(self.account, visible_accounts(self.peer))

    def test_a_manager_reads_its_records_in_the_roll_up_and_the_story(self):
        self.assertEqual(self.contacts(self.manager), {self.contact.pk})
        self.assertIn(("activity", self.activity.pk), self.story(self.manager))

    def test_a_peer_still_does_not(self):
        self.assertEqual(self.contacts(self.peer), set())
        self.assertNotIn(("activity", self.activity.pk), self.story(self.peer))

    def test_an_account_under_a_reports_organisation_is_seen_too(self):
        owned = Customer.objects.create(
            organisation=self.customer.organisation, name="Initech", owner=self.rep
        )
        other = User.objects.create_user(
            email="o@acme.io",
            password="x",
            name="O",
            organisation=owned.organisation,
            role=User.Role.CSM,
        )
        div = create_account(owned, name="Initech div", owner=other)
        self.assertIn(div, visible_accounts(self.manager))
        self.assertNotIn(div, visible_accounts(self.peer))


class ManagerSeesReportsAccountsOrganisation(APITestCase):
    """The same rule in the other direction: a report who owns an account
    may open its organisation, so their manager may too — and the account's
    pipeline counts in the manager's forecast."""

    def test_the_manager_opens_the_organisation_and_counts_its_pipeline(self):
        from decimal import Decimal

        from services.customers.models import Opportunity
        from services.customers.scoping import visible_customers

        org = Organisation.objects.create(name="Acme Inc", currency="USD")
        mk = lambda email, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=email, organisation=org, role=User.Role.CSM, **kw
        )
        manager = mk("m@acme.io")
        rep = mk("r@acme.io", reports_to=manager)
        colleague = mk("c@acme.io")
        peer = mk("p@acme.io")
        customer = Customer.objects.create(
            organisation=org,
            name="Globex",
            owner=colleague,
            arr_billed_at_account=Decimal("100000"),
            lifecycle_stage=Customer.LifecycleStage.LIVE,
        )
        account = create_account(customer, name="Rep's div", owner=rep)
        Opportunity.objects.create(
            account=account, title="Seats", mrr=Decimal("1000"), stage="negotiation"
        )

        self.assertIn(customer, visible_customers(User.objects.get(pk=manager.pk)))
        self.assertNotIn(customer, visible_customers(peer))
        self.client.force_authenticate(User.objects.get(pk=manager.pk))
        bridge = self.client.get("/api/v1/customers/forecast/").data["bridge"]
        self.assertEqual(bridge["expansion"], 9600.0)
