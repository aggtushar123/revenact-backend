"""Unit tier: get_completion's own provider branching — no real, paid,
live call to either Anthropic or AWS Bedrock is ever made here (see this
module's own docstring); `anthropic.Anthropic`/`anthropic.AnthropicBedrock`
are mocked at the SDK boundary, same "mock the paid API, not our own
logic" discipline as test_views.py's own SendMessageView tests (which
mock this module's own `get_completion` one layer up instead)."""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from services.copilot.anthropic_client import (
    CopilotNotConfigured,
    CopilotRequestFailed,
    get_completion,
)


def _fake_response(text="Hi there!"):
    block = MagicMock(type="text", text=text)
    return MagicMock(content=[block])


@override_settings(COPILOT_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="", AWS_ACCESS_KEY_ID="")
class DefaultProviderNotConfiguredTests(TestCase):
    def test_no_key_raises_copilot_not_configured(self):
        with self.assertRaises(CopilotNotConfigured):
            get_completion(system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}])


@override_settings(
    COPILOT_LLM_PROVIDER="anthropic",
    ANTHROPIC_API_KEY="sk-real-key",
    ANTHROPIC_MODEL="claude-sonnet-5",
)
class DirectAnthropicProviderTests(TestCase):
    @patch("anthropic.Anthropic")
    def test_uses_the_real_api_key_and_model_from_settings(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_response("A real reply.")
        mock_anthropic_cls.return_value = mock_client

        reply = get_completion(
            system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}]
        )

        self.assertEqual(reply, "A real reply.")
        mock_anthropic_cls.assert_called_once_with(api_key="sk-real-key")
        self.assertEqual(mock_client.messages.create.call_args.kwargs["model"], "claude-sonnet-5")

    @patch("anthropic.Anthropic")
    def test_a_real_sdk_failure_becomes_copilot_request_failed(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("rate limited")
        mock_anthropic_cls.return_value = mock_client

        with self.assertRaises(CopilotRequestFailed):
            get_completion(system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}])


@override_settings(
    COPILOT_LLM_PROVIDER="bedrock",
    AWS_ACCESS_KEY_ID="",
    AWS_SECRET_ACCESS_KEY="",
    AWS_REGION="",
    BEDROCK_MODEL_ID="",
)
class BedrockProviderNotConfiguredTests(TestCase):
    def test_missing_aws_credentials_raises_copilot_not_configured(self):
        with self.assertRaises(CopilotNotConfigured):
            get_completion(system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}])

    @override_settings(
        AWS_ACCESS_KEY_ID="AKIA...", AWS_SECRET_ACCESS_KEY="secret", AWS_REGION="ap-south-1"
    )
    def test_real_aws_credentials_but_no_model_id_still_raises_copilot_not_configured(self):
        with self.assertRaises(CopilotNotConfigured):
            get_completion(system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}])


@override_settings(
    COPILOT_LLM_PROVIDER="bedrock",
    AWS_ACCESS_KEY_ID="AKIA...",
    AWS_SECRET_ACCESS_KEY="real-secret",
    AWS_REGION="ap-south-1",
    BEDROCK_MODEL_ID="anthropic.claude-3-5-sonnet-20240620-v1:0",
)
class BedrockProviderTests(TestCase):
    @patch("anthropic.AnthropicBedrock")
    def test_uses_real_aws_credentials_region_and_bedrock_model_id(self, mock_bedrock_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_response("A real Bedrock reply.")
        mock_bedrock_cls.return_value = mock_client

        reply = get_completion(
            system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}]
        )

        self.assertEqual(reply, "A real Bedrock reply.")
        mock_bedrock_cls.assert_called_once_with(
            aws_access_key="AKIA...", aws_secret_key="real-secret", aws_region="ap-south-1"
        )
        self.assertEqual(
            mock_client.messages.create.call_args.kwargs["model"],
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
        )

    @patch("anthropic.AnthropicBedrock")
    def test_a_real_sdk_failure_becomes_copilot_request_failed(self, mock_bedrock_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("no model access granted")
        mock_bedrock_cls.return_value = mock_client

        with self.assertRaises(CopilotRequestFailed):
            get_completion(system="You are Copilot.", messages=[{"role": "user", "content": "Hi"}])
