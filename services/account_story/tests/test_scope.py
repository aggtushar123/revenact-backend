from django.db.models import Q
from django.http import Http404
from django.test import SimpleTestCase

from services.account_story.scope import AccountScope, resolve_account_scope
from services.customers.models import Account
from services.customers.tests.test_views import blind_to_one_account

from .fixtures import AccountStoryFixture


class AccountScopeTests(SimpleTestCase):
    def setUp(self):
        self.scope = AccountScope(account=Account(pk=7, name="EMEA"))

    def test_rows_filed_on_this_account_only(self):
        self.assertEqual(self.scope.parent_q(), Q(account_id=7))
        self.assertEqual(self.scope.parent_q(7), Q(account_id=7))

    def test_any_other_narrowing_reads_nothing(self):
        for account in (8, "none"):
            with self.subTest(account=account):
                self.assertEqual(self.scope.parent_q(account), Q(pk__in=[]))

    def test_the_one_account_is_the_whole_account_list(self):
        self.assertEqual(self.scope.accounts, {7: "EMEA"})
        self.assertEqual(self.scope.account_ref(7), {"id": 7, "name": "EMEA"})
        self.assertIsNone(self.scope.account_ref(None))

    def test_cursors_are_keyed_apart_from_an_organisation_s(self):
        self.assertEqual(self.scope.cursor_key, "account:7")


class ResolveAccountScopeTests(AccountStoryFixture):
    def test_an_account_the_viewer_may_open(self):
        self.assertEqual(self.account_scope().account, self.emea)

    def test_an_account_the_viewer_cannot_open_is_a_404_whether_or_not_it_exists(self):
        danas_co = self.customer("Dana's", owner=self.other)
        danas = self.account("Dana's div", customers=[danas_co], owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        stranger = self.account("Stranger div", customers=[globex])
        for account_id in (danas.pk, stranger.pk, 999_999):
            with self.subTest(account_id=account_id), self.assertRaises(Http404):
                resolve_account_scope(self.csm, account_id)

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.assertEqual(resolve_account_scope(viewer, seen.pk).account, seen)
        with self.assertRaises(Http404):
            resolve_account_scope(viewer, hidden.pk)

    def test_an_account_shared_by_two_organisations_resolves_once(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        for user in (self.csm, self.admin):
            with self.subTest(user=user.name):
                self.assertEqual(resolve_account_scope(user, shared.pk).account, shared)
