"""What the classifier reads for a call: the title, then the summary (a digest
of the whole transcript), else the transcript's opening, else the title alone.
A title that only says a call happened is nothing to read."""

from unittest.mock import patch

from django.core.exceptions import SuspiciousFileOperation
from django.test import SimpleTestCase

from services.customers import calls
from services.customers.classification import _text_for
from services.customers.files import read_transcript_text
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
    def test_the_summary_comes_first_and_the_transcript_is_not_read(self):
        """The summary digests the whole transcript; the transcript's first
        characters are often small talk."""
        call = self.call(summary="They want SSO.", transcript=self.transcript("Hi, how are you?"))
        call = Call.objects.get(pk=call.pk)
        with patch(
            "services.customers.files.read_transcript_text", wraps=read_transcript_text
        ) as spy:
            self.assertEqual(_text_for(call), "Renewal readiness. They want SSO.")
            self.assertTrue(calls.has_something_to_read(call))
        spy.assert_not_called()

    def test_then_the_transcript(self):
        call = self.call(transcript=self.transcript("We need SSO by Q4."))
        self.assertEqual(_text_for(call), "Renewal readiness. We need SSO by Q4.")

    def test_a_transcript_handed_over_at_creation_beats_the_stored_one(self):
        call = self.call(transcript=self.transcript("stored words"))
        call._transcript_text = "pasted words"
        self.assertEqual(_text_for(call), "Renewal readiness. pasted words")

    def test_then_the_title_alone(self):
        self.assertEqual(_text_for(self.call()), "Renewal readiness")

    def test_an_unreadable_transcript_file_reads_as_none(self):
        call = self.call(transcript=self.transcript("gone"))
        call.transcript.file.storage.delete(call.transcript.file.name)
        call = Call.objects.get(pk=call.pk)
        self.assertEqual(_text_for(call), "Renewal readiness")

    def test_a_storage_error_reads_as_no_transcript(self):
        """Not every read failure is an `OSError`/`ValueError` — a storage
        backend can raise its own exception (`SuspiciousFileOperation`,
        say). None of them may fail a classification batch."""
        call = self.call(transcript=self.transcript("stored words"))
        call = Call.objects.get(pk=call.pk)
        storage = call.transcript.file.storage
        with patch.object(storage, "open", side_effect=SuspiciousFileOperation("nope")):
            self.assertEqual(_text_for(call), "Renewal readiness")

    def test_a_binary_transcript_file_does_not_raise(self):
        call = self.call(transcript=self.binary_transcript(b"\xff\xfe\x00\x01binary junk\x00"))
        call = Call.objects.get(pk=call.pk)
        text = _text_for(call)
        self.assertTrue(text.startswith("Renewal readiness"))

    def test_the_transcript_file_is_read_only_once(self):
        """`has_something_to_read` and `call_text` both ask for the
        transcript; the file is opened and decoded at most once per call."""
        call = self.call(transcript=self.transcript("We need SSO."))
        call = Call.objects.get(pk=call.pk)
        with patch(
            "services.customers.files.read_transcript_text", wraps=read_transcript_text
        ) as spy:
            self.assertTrue(calls.has_something_to_read(call))
            self.assertEqual(calls.call_text(call), "Renewal readiness. We need SSO.")
        self.assertEqual(spy.call_count, 1)

    def test_something_to_read(self):
        self.assertFalse(calls.has_something_to_read(self.call(title="Weekly sync")))
        self.assertTrue(calls.has_something_to_read(self.call(title="Sync", summary="Fine.")))
        self.assertTrue(calls.has_something_to_read(self.call(title="SSO escalation")))
        handed = self.call(title="Sync")
        handed._transcript_text = "We need SSO."
        self.assertTrue(calls.has_something_to_read(handed))
