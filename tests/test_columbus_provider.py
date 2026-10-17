import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import Mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from common.agent import (
    AgentError, MAX_PROVIDER_STATE_BYTES, ModelSettings,
    test_provider_connection as check_connection,
)
from common.agent_providers import AgentTurnRequest, create_provider, registered_provider_ids
from common.columbus_provider import ColumbusProvider, _tokens


def options():
    return {
        'agent_id': '11111111-1111-4111-8111-111111111111',
        'tenant_id': '22222222-2222-4222-8222-222222222222',
        'client_id': '33333333-3333-4333-8333-333333333333',
        'client_secret': 'test-client-secret', 'scope': 'api://test-resource/.default',
    }


def settings(**changes):
    return ModelSettings(**{
        'api_key': 'test-subscription-key', 'model': '',
        'base_url': 'https://columbus.invalid/v5',
        'provider': 'columbus_agent', 'provider_options': options(), **changes,
    })


def answer(messages=True):
    result = {'response': {
        'status': 'SUCCESS', 'model': 'test-columbus-model',
        'output': [{
            'role': 'assistant', 'type': 'message',
            'content': [{'type': 'output_text', 'text': 'Test answer'}],
        }],
    }}
    if messages:
        result['messages'] = [{'role': 'assistant', 'type': 'message',
                               'content': [{'type': 'output_text', 'text': 'Native history'}]}]
    return result


def consume(events):
    emitted = []
    while True:
        try:
            emitted.append(next(events))
        except StopIteration as completed:
            return emitted, completed.value


class ColumbusProviderTests(unittest.TestCase):
    def setUp(self):
        _tokens.clear()
        self.requests = []
        self.response = answer()
        self.token_lifetime = 3600
        self.agent_status = 200
        self.client = httpx.Client(transport=httpx.MockTransport(self.transport))
        self.addCleanup(self.client.close)

    def transport(self, request):
        self.requests.append(request)
        if request.url.host == 'login.microsoftonline.com':
            return httpx.Response(200, json={
                'access_token': 'test-access-token', 'token_type': 'Bearer',
                'expires_in': self.token_lifetime,
            })
        return httpx.Response(self.agent_status, json=self.response)

    def test_factory_registers_builtin_and_validates_dedicated_options(self):
        self.assertIn('columbus_agent', registered_provider_ids())
        self.assertIsInstance(create_provider(settings(), client=self.client), ColumbusProvider)
        for key in options():
            with self.subTest(key=key):
                invalid = options()
                invalid[key] = ''
                with self.assertRaises(AgentError):
                    settings(provider_options=invalid).validate()

    def test_dual_auth_and_native_two_turn_history_without_local_tools(self):
        provider = ColumbusProvider(settings(), self.client)
        executor = Mock(side_effect=AssertionError('must not execute local tools'))
        events, result = consume(provider.stream_turn(
            AgentTurnRequest('First question', history=[]), [{'name': 'local_tool'}], executor))
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(str(self.requests[-1].url),
                         'https://columbus.invalid/v5/columbus/platform/agent/message')
        self.assertEqual(self.requests[-1].headers['Authorization'], 'Bearer test-access-token')
        self.assertEqual(self.requests[-1].headers['Ocp-Apim-Subscription-Key'], 'test-subscription-key')
        self.assertEqual(payload['agentId'], options()['agent_id'])
        self.assertEqual(parse_qs(self.requests[0].content.decode()), {
            'grant_type': ['client_credentials'], 'client_id': [options()['client_id']],
            'client_secret': [options()['client_secret']], 'scope': [options()['scope']],
        })
        self.assertEqual(payload['input'][0]['content'][0]['text'], 'First question')
        self.assertFalse(payload['background'])
        self.assertEqual(payload['history'], [])
        self.assertNotIn('tools', payload)
        self.assertEqual([event.text for event in events if event.type == 'delta'], ['Test answer'])
        self.assertEqual(result.model, 'test-columbus-model')
        consume(provider.stream_turn(AgentTurnRequest(
            'Second question', history=[], provider_state=result.provider_state), [], executor))
        self.assertEqual(json.loads(self.requests[-1].content)['history'], self.response['messages'])
        self.assertEqual(sum(r.url.host == 'login.microsoftonline.com' for r in self.requests), 1)
        executor.assert_not_called()

    def test_expired_token_and_changed_credentials_fetch_new_tokens(self):
        self.token_lifetime = 30
        provider = ColumbusProvider(settings(), self.client)
        for _ in range(2):
            consume(provider.stream_turn(AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(len(self.requests), 4)
        self.token_lifetime = 3600
        consume(provider.stream_turn(AgentTurnRequest('Question', []), [], Mock()))
        changed = options()
        changed['client_secret'] = 'rotated-secret'
        consume(ColumbusProvider(settings(provider_options=changed), self.client).stream_turn(
            AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(sum(r.url.host == 'login.microsoftonline.com' for r in self.requests), 4)

    def test_401_invalidates_token_but_never_replays_submission(self):
        self.agent_status = 401
        provider = ColumbusProvider(settings(), self.client)
        with self.assertRaises(AgentError):
            consume(provider.stream_turn(AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(_tokens, {})

    def test_error_shapes_fail_closed_without_echoing_raw_errors(self):
        errors = [
            {'status': 200, 'data': {'taskId': 'test-task'}},
            {'status': 200, 'data': {'taskStatus': 'STARTED'}},
            {'status': 200, 'data': {'taskStatus': 'REVOKED'}},
            {'status': 200, 'data': {'taskStatus': 'SUCCESS', 'taskResult': {
                'detail': {'code': '400', 'errorType': 'SYSTEM_ERROR', 'message': 'private-detail'}}}},
            {'errorMsg': 'private-detail'},
            {'response': {'status': 'SUCCESS', 'output': []}},
            {'response': {'status': 'UNKNOWN', 'output': []}},
            {'response': {'status': 'SUCCESS', 'error': {'detail': 'private-detail'}}},
        ]
        for body in errors:
            with self.subTest(body=body):
                self.response = body
                with self.assertRaises(AgentError) as caught:
                    consume(ColumbusProvider(settings(), self.client).stream_turn(
                        AgentTurnRequest('Question', []), [], Mock()))
                self.assertNotIn('private-detail', str(caught.exception))

    def test_documented_output_only_shape_answers_but_blocks_unsafe_continuation(self):
        self.response = answer(messages=False)
        provider = ColumbusProvider(settings(), self.client)
        _, result = consume(provider.stream_turn(AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(result.answer, 'Test answer')
        self.assertEqual(result.provider_state, {'messages': None})
        count = len(self.requests)
        with self.assertRaisesRegex(AgentError, 'messages'):
            consume(provider.stream_turn(AgentTurnRequest(
                'Next question', [], provider_state=result.provider_state), [], Mock()))
        self.assertEqual(len(self.requests), count)

    def test_wrapped_task_result_and_history_size_limit(self):
        self.response = {'status': 200, 'data': {
            'taskStatus': 'SUCCESS', 'taskResult': answer(),
        }}
        _, result = consume(ColumbusProvider(settings(), self.client).stream_turn(
            AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(result.answer, 'Test answer')
        state = {'messages': [{'content': 'x' * 262145}]}
        with self.assertRaisesRegex(AgentError, '过大'):
            consume(ColumbusProvider(settings(), self.client).stream_turn(
                AgentTurnRequest('Question', [], provider_state=state), [], Mock()))

    def test_timeout_invalid_json_and_redirect_are_explicit_failures(self):
        def timeout(request):
            raise httpx.ReadTimeout('private-detail', request=request)

        for transport in (
            timeout, lambda request: httpx.Response(200, text='not json'),
            lambda request: httpx.Response(302, headers={'Location': 'https://elsewhere.invalid'}),
        ):
            with self.subTest(transport=transport), httpx.Client(
                    transport=httpx.MockTransport(transport)) as client:
                with self.assertRaises(AgentError) as caught:
                    consume(ColumbusProvider(settings(), client).stream_turn(
                        AgentTurnRequest('Question', []), [], Mock()))
                self.assertNotIn('private-detail', str(caught.exception))

    def test_known_credentials_are_redacted_from_answers(self):
        self.response['response']['output'][0]['content'][0]['text'] = (
            'test-client-secret test-subscription-key test-access-token')
        _, result = consume(ColumbusProvider(settings(), self.client).stream_turn(
            AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(result.answer, '[REDACTED] [REDACTED] [REDACTED]')

    def test_upstream_sse_is_rejected_without_consuming_or_replaying_it(self):
        class UnexpectedStream(httpx.SyncByteStream):
            def __iter__(self):
                raise AssertionError('SSE must not be consumed as JSON')
                yield b''

        def transport(request):
            if request.url.host == 'login.microsoftonline.com':
                return self.transport(request)
            self.requests.append(request)
            return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                                  stream=UnexpectedStream())

        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            with self.assertRaisesRegex(AgentError, 'SSE'):
                consume(ColumbusProvider(settings(), client).stream_turn(
                    AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(len(self.requests), 2)

    def test_exact_native_state_size_limit(self):
        skeleton = {'messages': [{'content': ''}]}
        overhead = len(json.dumps(skeleton, ensure_ascii=False).encode('utf-8'))
        self.response['messages'] = [{'content': 'x' * (MAX_PROVIDER_STATE_BYTES - overhead)}]
        _, result = consume(ColumbusProvider(settings(), self.client).stream_turn(
            AgentTurnRequest('Question', []), [], Mock()))
        self.assertEqual(len(json.dumps(result.provider_state).encode()), MAX_PROVIDER_STATE_BYTES)
        self.response['messages'][0]['content'] += 'x'
        with self.assertRaisesRegex(AgentError, '过大'):
            consume(ColumbusProvider(settings(), self.client).stream_turn(
                AgentTurnRequest('Question', []), [], Mock()))

    def test_connection_test_uses_columbus_contract_without_tools(self):
        result = check_connection(settings(), client=self.client)
        self.assertEqual(result['model'], 'test-columbus-model')
        self.assertNotIn('tools', json.loads(self.requests[-1].content))


if __name__ == '__main__':
    unittest.main()
