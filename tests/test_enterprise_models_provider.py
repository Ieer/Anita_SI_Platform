import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from common.agent import AgentError
from common.enterprise_models_credentials import PREFIX, read_credential, require_access
from common.agent import ModelSettings
from common.agent_providers import AgentTurnRequest
from common.enterprise_models_provider import EnterpriseModelsProvider, configured_endpoint


ENDPOINT = 'https://models.test.invalid/v1/chat/stream'


class CredentialTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'token.dat'
        environment = patch.dict(os.environ, {
            PREFIX + 'TOKEN_FILE': str(self.path), PREFIX + 'TOKEN_TIMEZONE': 'UTC',
            PREFIX + 'OWNER': 'alice',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def write(self, **changes):
        self.path.write_text(json.dumps({
            'access_token': 'fake-access', 'refresh_token': 'never-use-this',
            'token_expiry': '2026-01-01T01:00:00', **changes,
        }), encoding='utf-8-sig')

    def test_reads_only_access_token_with_explicit_timezone(self):
        self.write()
        credential = read_credential(self.now)
        self.assertEqual(credential.token, 'fake-access')
        self.assertEqual(credential.expires_at.hour, 1)
        self.assertNotIn('fake-access', repr(credential))
        self.assertFalse(hasattr(credential, 'refresh_token'))

    def test_invalid_empty_expired_and_oversized_files_fail_closed(self):
        for changes in (
            {'access_token': ''}, {'access_token': 'bad\r\nheader'},
            {'token_expiry': '0001-01-01T00:00:00.0000000'},
            {'token_expiry': '2025-01-01T00:00:00'}, {'token_expiry': None},
            {'access_token': 'x' * 65537},
        ):
            with self.subTest(changes=tuple(changes)):
                self.write(**changes)
                with self.assertRaises(AgentError):
                    read_credential(self.now)
        self.path.unlink()
        with self.assertRaises(AgentError):
            read_credential(self.now)

    def test_only_bound_admin_can_use_credentials(self):
        require_access(SimpleNamespace(user_name='alice', user_info=SimpleNamespace(user_roles=['admin'])))
        for name, roles in [('bob', ['admin']), ('alice', []), ('alice', ['group_admin'])]:
            with self.assertRaises(AgentError):
                require_access(SimpleNamespace(user_name=name, user_info=SimpleNamespace(user_roles=roles)))

    def test_missing_timezone_is_not_guessed(self):
        self.write()
        with patch.dict(os.environ, {PREFIX + 'TOKEN_TIMEZONE': ''}):
            with self.assertRaises(AgentError):
                read_credential(self.now)

    def test_absolute_login_file_path_is_deployment_configurable(self):
        self.write(access_token='first-file-token')
        original_path = self.path
        self.path = self.path.parent / 'alternate-login.json'
        self.write(access_token='second-file-token')
        with patch.dict(os.environ, {PREFIX + 'TOKEN_FILE': str(self.path)}):
            self.assertEqual(read_credential(self.now).token, 'second-file-token')
        self.assertEqual(read_credential(self.now).token, 'first-file-token')
        with patch.dict(os.environ, {PREFIX + 'TOKEN_FILE': original_path.name}):
            with self.assertRaises(AgentError):
                read_credential(self.now)

    def test_offset_is_respected_and_symlinks_rejected(self):
        self.write(token_expiry='2026-01-01T09:00:00+08:00')
        self.assertEqual(read_credential(self.now).expires_at.hour, 1)
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(
                st_mode=0o100600, st_file_attributes=0x400)):
            with self.assertRaises(AgentError):
                read_credential(self.now)

    def test_malformed_json_and_missing_owner_are_rejected(self):
        for body in ('{', '[]', '{}', '{"access_token":123}'):
            self.path.write_text(body, encoding='utf-8')
            with self.assertRaises(AgentError):
                read_credential(self.now)
        with patch.dict(os.environ, {PREFIX + 'OWNER': ''}):
            with self.assertRaises(AgentError):
                require_access(SimpleNamespace(user_name='alice', user_info=SimpleNamespace(user_roles=['admin'])))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'SMART_AGENT_ENTERPRISE_MODELS_ENDPOINT': ENDPOINT})
        environment.start()
        self.addCleanup(environment.stop)
        self.requests = []
        self.status = 200
        self.body = ('data: {"chatId":"chat","current":[{"id":"parent","content":"Hello"}]}\n\n'
                     'data: [DONE]\n\n')
        self.client = httpx.Client(transport=httpx.MockTransport(self.transport))
        self.addCleanup(self.client.close)
        self.settings = ModelSettings('fake-access', 'gpt-5-mini', ENDPOINT, 'enterprise_models')

    def transport(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, text=self.body, headers={'content-type': 'text/event-stream'})

    def consume(self, state=None):
        events = EnterpriseModelsProvider(self.settings, self.client).stream_turn(
            AgentTurnRequest('Question', [], provider_state=state), [], lambda *_: self.fail('tool called'))
        emitted = []
        while True:
            try:
                emitted.append(next(events))
            except StopIteration as done:
                return emitted, done.value

    def test_streams_and_continues_native_history_without_tools(self):
        emitted, result = self.consume()
        self.assertEqual(emitted[0].text, 'Hello')
        self.assertEqual(result.answer, 'Hello')
        self.consume(result.provider_state)
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(payload['parentId'], 'parent')
        self.assertEqual(payload['chatId'], 'chat')
        self.assertNotIn('tools', payload)
        self.assertEqual(self.requests[-1].headers['Authorization'], 'Bearer fake-access')

    def test_http_errors_are_not_retried(self):
        for status in (401, 403, 429, 500, 302):
            with self.subTest(status=status):
                self.status = status
                before = len(self.requests)
                with self.assertRaises(AgentError):
                    self.consume()
                self.assertEqual(len(self.requests), before + 1)

    def test_malformed_truncated_and_business_errors_are_rejected(self):
        for body in ('data: {}\n\n', 'data: not-json\n\n',
                     'data: {"error":"private-detail"}\n\n', 'data: [DONE]\n\n'):
            with self.subTest(body=body):
                self.body = body
                with self.assertRaises(AgentError) as error:
                    self.consume()
                self.assertNotIn('private-detail', str(error.exception))

    def test_registered_neutral_id_and_fixed_endpoint(self):
        from common.agent_providers import registered_provider_ids

        self.assertIn('enterprise_models', registered_provider_ids())
        self.assertIn('internal_agent', registered_provider_ids())
        with self.assertRaises(AgentError):
            ModelSettings('', 'gpt-5-mini', 'https://untrusted.invalid', 'enterprise_models').validate()

    def test_endpoint_requires_explicit_trusted_https_configuration(self):
        for endpoint in ('', 'http://localhost/stream', 'https://enterprise-api.example.com/v1/chat/stream',
                         'https://user:secret@models.test.invalid/stream',
                         'https://models.test.invalid/stream?token=secret', 'https://models.test.invalid:bad'):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {
                'SMART_AGENT_ENTERPRISE_MODELS_ENDPOINT': endpoint,
            }):
                with self.assertRaises(AgentError):
                    configured_endpoint()
        self.assertEqual(configured_endpoint(), ENDPOINT)

    def test_environment_uses_dedicated_endpoint_instead_of_generic_base_url(self):
        with patch.dict(os.environ, {
            'SMART_AGENT_PROVIDER': 'enterprise_models', 'SMART_AGENT_MODEL': 'gpt-5-mini',
            'SMART_AGENT_BASE_URL': 'https://other.test.invalid/v1',
        }):
            self.assertEqual(ModelSettings.from_environment().base_url, ENDPOINT)

    def test_secret_is_redacted_even_across_model_events(self):
        self.body = ''.join('data: ' + json.dumps({
            'chatId': 'chat', 'current': [{'id': 'parent', 'content': text}],
        }) + '\n\n' for text in ('Value: fake-', 'access', ' remains private')) + 'data: [DONE]\n\n'
        emitted, result = self.consume()
        self.assertNotIn('fake-access', result.answer)
        self.assertNotIn('fake-access', ''.join(event.text for event in emitted))
        self.assertIn('[REDACTED]', result.answer)

    def test_multiline_sse_and_error_event(self):
        self.body = (': keep-alive\r\ndata: {"chatId":"chat",\r\n'
                     'data: "current":[{"id":"parent","content":"Hello"}]}\r\n\r\n'
                     'data: [Done]\r\n\r\n')
        self.assertEqual(self.consume()[1].answer, 'Hello')
        self.body = 'event: error\ndata: {"message":"private"}\n\ndata: [DONE]\n\n'
        with self.assertRaises(AgentError):
            self.consume()