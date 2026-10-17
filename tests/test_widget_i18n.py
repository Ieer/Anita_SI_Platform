import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from server import server
from dash_view.framework.agent_widget_i18n import (
    _AGENT_MESSAGES,
    agent_widget_i18n_data,
    translate_agent_message,
)


def component_text(value):
    if hasattr(value, 'to_plotly_json'):
        yield from component_text(value.to_plotly_json().get('props', {}))
    elif isinstance(value, dict):
        for child in value.values():
            yield from component_text(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from component_text(child)
    elif isinstance(value, str):
        yield value


class WidgetI18nTests(unittest.TestCase):
    def test_known_agent_messages_follow_request_locale_and_unknown_content_is_untouched(self):
        source = '请求未完成，请检查服务连接后重试。'
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}):
            self.assertEqual(translate_agent_message(source),
                             'The request did not complete. Check the service connection and try again.')
            self.assertEqual(translate_agent_message('User supplied text'), 'User supplied text')
            self.assertEqual(
                translate_agent_message('Internal Agent API Agent 请求失败（HTTP 502），请联系管理员。'),
                'Internal Agent API Agent request failed (HTTP 502). Contact an administrator.',
            )
            self.assertEqual(
                translate_agent_message('Provider“custom_agent”未在服务端注册。'),
                'Provider "custom_agent" is not registered on the server.',
            )
            self.assertEqual(
                translate_agent_message('正在处理授权工具请求…'),
                'Processing an authorized tool request…',
            )
            self.assertTrue(all(not any('\u4e00' <= char <= '\u9fff'
                                        for char in translate_agent_message(message))
                                for message in _AGENT_MESSAGES))
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=zh-cn'}):
            self.assertEqual(translate_agent_message(source), source)

    def test_global_widget_labels_and_messages_are_localized_for_javascript(self):
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}):
            data = json.loads(agent_widget_i18n_data())
        self.assertEqual(data['labels']['openChat'], 'Open Agent chat')
        self.assertEqual(data['labels']['closeChat'], 'Close Agent chat')
        self.assertEqual(data['labels']['unsupportedStream'],
                         'This browser does not support Agent streaming.')
        self.assertEqual(data['messages']['请求未完成，请检查服务连接后重试。'],
                         'The request did not complete. Check the service connection and try again.')
        status_pattern = next(pattern for pattern in data['patterns']
                              if 'HTTP' in pattern['pattern'])
        self.assertIn('Internal Agent API {stage} failed (HTTP {status})', status_pattern['template'])
        status_match = re.fullmatch(status_pattern['pattern'],
                                    'Internal Agent API Agent 请求失败（HTTP 502），请联系管理员。')
        self.assertEqual(status_match.groups(), ('Agent 请求', '502'))

    def test_widget_messages_actions_and_accessibility_labels_are_localized(self):
        from dash_view.framework.agent_widget import render_actions, render_messages

        user_text = '请照原样保留：Agent 用户内容'
        messages = [{
            'role': 'assistant',
            'content': user_text,
            'created_at': '2026-10-17T07:00:00',
            'trace': [{'tool': 'list_tasks', 'status': 'ok', 'arguments': '{"limit":1}'}],
        }, {
            'role': 'user', 'content': 'Keep this user message unchanged.',
            'created_at': '2026-10-17T07:01:00', 'trace': [],
        }]
        action = {
            'action_id': 'action-1', 'job_id': 'task-1', 'status': 'pending',
            'summary': 'run task', 'task_digest': 'digest', 'run_id': 'run-1',
            'expires_at': '2026-10-17T08:00:00', 'decided_at': None, 'result': None,
        }
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}):
            rendered_messages = render_messages(messages, SimpleNamespace(user_name='probe'))
            rendered_actions = render_actions([action], can_approve=True)
        rendered_text = list(component_text([rendered_messages, rendered_actions]))
        self.assertIn('You', rendered_text)
        self.assertIn('Tool trace · 1', rendered_text)
        self.assertIn('Query tasks', rendered_text)
        self.assertIn('Complete', rendered_text)
        self.assertIn('Execution approval · 1', rendered_text)
        self.assertIn('I reviewed the task script and its impact', rendered_text)
        self.assertIn('Approve and run', rendered_text)
        self.assertIn(user_text, rendered_text)
        self.assertIn('Keep this user message unchanged.', rendered_text)
        serialized = json.dumps(rendered_actions.to_plotly_json(),
                                default=lambda item: item.to_plotly_json()
                                if hasattr(item, 'to_plotly_json') else str(item))
        self.assertIn('aria-label', serialized)
        self.assertIn('Approve and run', serialized)

    def test_monitor_pause_callback_localizes_dynamic_labels(self):
        from dash_callback.application.dashboard_.monitor_c import descriptions, toggle_refresh

        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}), \
                patch('common.utilities.util_menu_access.get_menu_access',
                      return_value=SimpleNamespace(has_access=lambda _permission: True)):
            fields = descriptions(
                {'hostname': 'host-value', 'cpu_all_usage_percent': 0.25},
                [('主机名', 'hostname', False), ('使用率', 'cpu_all_usage_percent', True)],
            )
            self.assertEqual([field['label'] for field in fields], ['Hostname', 'usage percent'])
            self.assertEqual([field['children'] for field in fields], ['host-value', '25.0%'])
            paused, _icon, label, aria_label, pressed = toggle_refresh(1)
            self.assertTrue(paused)
            self.assertEqual(label, 'Resume auto refresh')
            self.assertEqual(aria_label, label)
            self.assertEqual(pressed, 'true')
            paused, _icon, label, aria_label, pressed = toggle_refresh(2)
            self.assertFalse(paused)
            self.assertEqual(label, 'Pause auto refresh')
            self.assertEqual(aria_label, label)
            self.assertEqual(pressed, 'false')

    def test_profile_options_localize_only_builtin_environment_entry(self):
        from database.sql_db.repositories import agent_provider_repository

        profile = {
            'name': '服务端环境配置', 'model': '用户模型', 'provider_id': 'openai_compatible',
            'profile_id': 'custom-profile',
        }
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}), \
                patch.object(agent_provider_repository, 'list_profiles', return_value=[profile]), \
                patch('common.agent.model_status', return_value={'ready': True}):
            options = agent_provider_repository.profile_options()
        self.assertEqual(options[0], {
            'label': '服务端环境配置 · 用户模型', 'value': 'custom-profile',
        })
        self.assertEqual(options[1], {
            'label': 'Server environment configuration · Environment variables',
            'value': 'environment',
        })

    def test_conversation_load_localizes_setup_hint_without_translating_title(self):
        from dash_callback.pages import agent_widget_c

        access = SimpleNamespace(
            user_name='probe', user_info=None, has_access=lambda _permission: True,
        )
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}), \
                patch.object(agent_widget_c, 'get_menu_access', return_value=access), \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=access), \
                patch.object(agent_widget_c, 'ctx', SimpleNamespace(triggered_id='agent-conversation-select')), \
                patch.object(agent_widget_c.agent_repository, 'read_messages', return_value=[]), \
                patch.object(agent_widget_c.agent_provider_repository, 'get_conversation_profile',
                             return_value='environment'), \
                patch.object(agent_widget_c, 'model_status', return_value={
                    'ready': True, 'model': '用户模型', 'message': 'Agent 已配置，可进行连接测试',
                }), \
                patch.object(agent_widget_c, 'set_props') as set_props:
            result = agent_widget_c.handle_conversation('conversation', 0, None, 'conversation', '', False)
        hint = next(call.args[1] for call in set_props.call_args_list
                    if call.args[0] == 'agent-setup-hint')
        self.assertEqual(hint['children'], ['Agent is configured and ready for a connection test.'])
        self.assertIn('用户模型', result[-1])


if __name__ == '__main__':
    unittest.main()
