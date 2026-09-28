"""What the classifier reads for a call: the title, then the transcript, else
the summary. A title that only says a call happened is nothing to read."""

from django.test import SimpleTestCase

from services.customers import calls
from services.customers.classification import _text_for
from services.customers.models import Call
from services.customers.tests.test_call_state import CallFixture


class GenericTitleTests(SimpleTestCase):
    def test_titles_that_say_only_that_a_call_happened_are_generic(self):
        for title in ["", "Call", "Weekly sync", "Zoom meeting", "Quick check-in", "Untitled"]:
            with self.subTest(title=title):
                self.assertTrue(calls.is_generic_title(title))

    def test_a_title_naming_anything_is_not(self):
        for title in ["Renewal readiness", "Call with Kraft Heinz", "Q3 QBR", "SSO escalation"]:
            with self.subTest(title=title):
                self.assertFalse(calls.is_generic_title(title))


class CallTextTests(CallFixture):
    def test_the_transcript_comes_first(self):
        call = self.call(summary="They want SSO.", transcript=self.transcript("We need SSO by Q4."))
        self.assertEqual(_text_for(call), "Renewal readiness. We need SSO by Q4.")

    def test_a_transcript_handed_over_at_creation_beats_the_stored_one(self):
        call = self.call(transcript=self.transcript("stored words"))
        call._transcript_text = "pasted words"
        self.assertEqual(_text_for(call), "Renewal readiness. pasted words")

    def test_then_the_summary(self):
        self.assertEqual(
            _text_for(self.call(summary="They want SSO.")), "Renewal readiness. They want SSO."
        )

    def test_then_the_title_alone(self):
        self.assertEqual(_text_for(self.call()), "Renewal readiness")

    def test_an_unreadable_transcript_file_reads_as_none(self):
        call = self.call(summary="They want SSO.", transcript=self.transcript("gone"))
        call.transcript.file.storage.delete(call.transcript.file.name)
        call = Call.objects.get(pk=call.pk)
        self.assertEqual(_text_for(call), "Renewal readiness. They want SSO.")

    def test_something_to_read(self):
        self.assertFalse(calls.has_something_to_read(self.call(title="Weekly sync")))
        self.assertTrue(calls.has_something_to_read(self.call(title="Sync", summary="Fine.")))
        self.assertTrue(calls.has_something_to_read(self.call(title="SSO escalation")))
        handed = self.call(title="Sync")
        handed._transcript_text = "We need SSO."
        self.assertTrue(calls.has_something_to_read(handed))
