"""The backfill must agree with the columns in use today, exactly.

Phase 3 switches the read path onto memberships. If this backfill is wrong,
that switch silently changes who can see what, which is the highest-severity
failure in the whole plan. So it is tested against the real migration function
rather than a reimplementation of it.
"""

from importlib import import_module

from django.test import TestCase

from services.accounts.models import Organisation, Role, User
from services.identity.models import Department, OrganizationMembership

# Imported by path because the module name starts with a digit, so it cannot
# be a normal `from ... import`. Testing the real migration function rather
# than a copy is the point: a copy can drift from what actually runs.
backfill_module = import_module("services.identity.migrations.0002_backfill_memberships")


class FakeApps:
    """`RunPython` receives historical models; the live ones are equivalent here
    because none of these tables has changed shape since 0001."""

    MODELS = {
        ("accounts", "User"): User,
        ("identity", "Department"): Department,
        ("identity", "OrganizationMembership"): OrganizationMembership,
    }

    def get_model(self, app_label, model_name):
        return self.MODELS[(app_label, model_name)]


class BackfillTests(TestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")
        self.role = Role.objects.create(
            organisation=self.acme, name="Admin", slug="admin", permissions=["manage_users"]
        )

        def member(email, org, **kw):
            return User.objects.create_user(
                email=email, password="x", name=email.split("@")[0], organisation=org, **kw
            )

        self.alice = member("alice@acme.io", self.acme, role=self.role, function="leadership")
        self.carl = member("carl@acme.io", self.acme, function="cs")
        self.dana = member("dana@acme.io", self.acme, function="cs")
        self.zed = member("zed@rival.io", self.rival, function="engineering")
        # A platform operator: no organisation, and so no membership anywhere.
        self.root = User.objects.create_superuser(email="root@revenact.io", password="x")

        # OrganizationMembership.objects.all() must be empty before the run,
        # since the real migration runs against a table nothing has written to.
        OrganizationMembership.objects.all().delete()
        Department.objects.all().delete()

    def run_backfill(self):
        backfill_module.backfill(FakeApps(), None)

    def test_every_member_gets_one_active_membership_in_their_own_tenant(self):
        self.run_backfill()

        self.assertEqual(OrganizationMembership.objects.count(), 4)
        for user, org in [
            (self.alice, self.acme),
            (self.carl, self.acme),
            (self.dana, self.acme),
            (self.zed, self.rival),
        ]:
            membership = OrganizationMembership.objects.get(user=user)
            self.assertEqual(membership.organisation_id, org.id)
            self.assertEqual(membership.status, "active")

    def test_a_platform_operator_belongs_to_no_tenant(self):
        """A superuser administers the platform; they are not a member of a
        customer's organisation, and must not silently become one."""
        self.run_backfill()
        self.assertFalse(OrganizationMembership.objects.filter(user=self.root).exists())

    def test_the_role_someone_holds_today_is_carried_over(self):
        """Faithfully, whatever it is — nothing is upgraded or invented.

        Note that nobody has a null role: `UserManager._resolve_role` gives a
        new user their organisation's CSM system role when none is passed. So
        "carried over" means the explicit role for Alice and the default CSM
        role for Carl, not None.
        """
        self.run_backfill()
        self.assertEqual(OrganizationMembership.objects.get(user=self.alice).role_id, self.role.id)

        carl_role = OrganizationMembership.objects.get(user=self.carl).role
        self.assertEqual(carl_role.id, self.carl.role_id)
        self.assertEqual(carl_role.slug, "csm")
        self.assertEqual(carl_role.permissions, [], "the default role grants nothing")

    def test_departments_come_from_function_and_are_shared_within_a_tenant(self):
        self.run_backfill()

        acme_departments = set(
            Department.objects.filter(organisation=self.acme).values_list("name", flat=True)
        )
        self.assertEqual(acme_departments, {"Leadership", "Customer Success"})

        # The two CS people land in the same department row, not two copies.
        carl = OrganizationMembership.objects.get(user=self.carl)
        dana = OrganizationMembership.objects.get(user=self.dana)
        self.assertEqual(carl.department_id, dana.department_id)
        self.assertEqual(carl.department.name, "Customer Success")

    def test_departments_do_not_leak_across_tenants(self):
        self.run_backfill()
        zed = OrganizationMembership.objects.get(user=self.zed)
        self.assertEqual(zed.department.organisation_id, self.rival.id)
        self.assertEqual(
            Department.objects.filter(organisation=self.rival).count(), 1, "one function, one dept"
        )

    def test_function_itself_is_left_alone(self):
        """Function still drives knowledge routing and ticket visibility."""
        self.run_backfill()
        self.carl.refresh_from_db()
        self.assertEqual(self.carl.function, "cs")

    def test_running_twice_changes_nothing(self):
        """A half-applied migration must be safe to re-apply."""
        self.run_backfill()
        first = OrganizationMembership.objects.count()
        departments = Department.objects.count()

        self.run_backfill()

        self.assertEqual(OrganizationMembership.objects.count(), first)
        self.assertEqual(Department.objects.count(), departments)

    def test_someone_who_already_has_a_live_membership_is_skipped(self):
        existing = OrganizationMembership.objects.create(
            organisation=self.acme, user=self.alice, status="suspended"
        )
        self.run_backfill()

        alice_rows = OrganizationMembership.objects.filter(user=self.alice)
        self.assertEqual(alice_rows.count(), 1)
        self.assertEqual(alice_rows.first().id, existing.id)
        self.assertEqual(alice_rows.first().status, "suspended", "not overwritten")

    def test_the_reverse_removes_only_what_it_created(self):
        self.run_backfill()
        kept = Department.objects.create(organisation=self.acme, name="Hand made")

        backfill_module.unbackfill(FakeApps(), None)

        self.assertEqual(OrganizationMembership.objects.count(), 0)
        self.assertEqual(list(Department.objects.values_list("id", flat=True)), [kept.id])
