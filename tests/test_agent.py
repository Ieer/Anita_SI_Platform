import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from common.agent import AgentError, ModelSettings, allowed_tools, execute_tool, model_status, redact, run_turn


def access(*permissions):
    user_info = SimpleNamespace(user_name='probe', user_full_name='Probe')
    return SimpleNamespace(user_name='probe', user_info=user_info,
                           has_access=lambda permission: permission in permissions)


def completion(content=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def tool_call(name, arguments):
    return SimpleNamespace(id='call_probe', function=SimpleNamespace(name=name, arguments=arguments))


class AgentTests(unittest.TestCase):
    def test_internal_agent_environment_uses_new_provider_and_credential_prefix(self):
        from common.agent import ModelSettings
        from common.agent_providers import registered_provider_ids

        options = {
            'agent_id': '11111111-1111-4111-8111-111111111111',
            'tenant_id': '22222222-2222-4222-8222-222222222222',
            'client_id': '33333333-3333-4333-8333-333333333333',
            'client_secret': 'fake-client-secret', 'scope': 'api://resource/.default',
        }
        with patch.dict('os.environ', {
            'SMART_AGENT_PROVIDER': 'internal_agent',
            'SMART_AGENT_BASE_URL': 'https://internal-agent.invalid/v5',
            'SMART_AGENT_API_KEY': 'fake-subscription',
            **{'SMART_AGENT_INTERNAL_AGENT_' + key.upper(): value for key, value in options.items()},
        }, clear=True):
            settings = ModelSettings.from_environment()
        self.assertEqual(settings.provider, 'internal_agent')
        self.assertEqual(settings.provider_options, options)
        self.assertEqual(settings.base_url, 'https://internal-agent.invalid/v5')
        self.assertIn('internal_agent', registered_provider_ids())
        self.assertNotIn('fake-client-secret', repr(settings))

    def test_provider_settings_localization_preserves_protocol_details(self):
        import re
        from dash_callback.application.setting_.agent_provider_c import translate_provider_message
        from server import server

        messages = [
            ('企业内部 Agent 接口', 'Internal Agent API'),
            ('请填写 1 到 64 个字符的配置名称。', 'Enter a profile name between 1 and 64 characters.'),
            ('最多保存 12 个 Agent 配置。', 'You can save up to 12 Agent profiles.'),
            ('Internal Agent API Agent ID 必须是有效 UUID。', 'Internal Agent API Agent ID must be a valid UUID.'),
            ('请填写有效的 Internal Agent API Scope。', 'Enter a valid Internal Agent API Scope.'),
            ('Provider“custom_agent”未在服务端注册。', 'Provider "custom_agent" is not registered on the server.'),
            ('未注册的 Agent Provider：custom_agent', 'Unregistered Agent provider: custom_agent'),
            ('Internal Agent API Token 获取认证或授权失败，请检查凭证、订阅和 Scope。',
             'Internal Agent API token retrieval: authentication or authorization failed. Check credentials, subscription and Scope.'),
            ('Internal Agent API Agent 请求失败（HTTP 502），请联系管理员。',
             'Internal Agent API Agent request failed (HTTP 502). Contact an administrator.'),
            ('Internal Agent API 请求超时；远端任务可能仍在执行，请确认状态后再重试，勿重复提交。',
             'Internal Agent API timed out. The remote task may still be running. Check its status before retrying; do not submit it again.'),
        ]
        for source, english in messages:
            with self.subTest(source=source):
                with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}):
                    self.assertEqual(translate_provider_message(source), english)
                    self.assertIsNone(re.search(r'[\u4e00-\u9fff]', translate_provider_message(source)))
                with server.test_request_context('/', headers={'Cookie': 'dash-i18n=zh-cn'}):
                    self.assertEqual(translate_provider_message(source), source)
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}):
            self.assertEqual(translate_provider_message('Third-party provider error [E42]'),
                             'Third-party provider error [E42]')

    def test_user_avatar_initials_support_chinese_latin_and_empty_names(self):
        from dash_view.framework.user_avatar import get_avatar_initials, render_user_avatar

        self.assertEqual(get_avatar_initials(SimpleNamespace(user_full_name='张小明', user_name='zhang')), '张')
        self.assertEqual(get_avatar_initials(SimpleNamespace(user_full_name='Grace Hopper', user_name='gh')), 'GH')
        self.assertEqual(get_avatar_initials(SimpleNamespace(user_full_name='', user_name='liuwei')), 'L')
        self.assertEqual(get_avatar_initials(SimpleNamespace(user_full_name='', user_name='')), '?')
        fallback = render_user_avatar(SimpleNamespace(user_full_name='Grace Hopper', user_name='gh'),
                                      size=28, avatar_exists=False)
        self.assertEqual(fallback.mode, 'text')
        self.assertEqual(fallback.text, 'GH')

    def test_configuration_is_required_and_never_exposes_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(model_status()['ready'])
            with self.assertRaises(AgentError):
                ModelSettings.from_environment()
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'secret-probe', 'SMART_AGENT_MODEL': 'probe'}):
            self.assertNotIn('secret-probe', repr(ModelSettings.from_environment()))
            self.assertNotIn('secret-probe', str(model_status()))

    def test_remote_http_and_url_credentials_are_rejected(self):
        for url in ['http://example.org/v1', 'https://user:pass@example.org/v1', 'file:///tmp',
                    'https://example.org/v1?key=probe', 'https://[']:
            with self.subTest(url=url), patch.dict(os.environ, {
                'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe', 'SMART_AGENT_BASE_URL': url,
            }), self.assertRaises(AgentError):
                ModelSettings.from_environment()

    def test_permission_and_consent_control_tools(self):
        self.assertEqual(allowed_tools(access('工作台-页面')), [])
        allowed = access('工作台-页面', '任务日志-页面')
        self.assertEqual(allowed_tools(allowed), [])
        self.assertEqual(allowed_tools(allowed, True)[0]['function']['name'], 'read_latest_log')
        with self.assertRaises(AgentError):
            allowed_tools(access('任务管理-页面'))

    def test_unauthorized_and_unknown_tools_never_read_data(self):
        with patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs') as query:
            for name in ['list_tasks', 'run_script', 'delete_job']:
                with self.subTest(name=name), self.assertRaises(AgentError):
                    execute_tool(name, {'limit': 5}, access('工作台-页面'))
            query.assert_not_called()
        with patch('database.sql_db.repositories.apscheduler_repository.get_latest_completed_log') as query:
            with self.assertRaises(AgentError):
                execute_tool('read_latest_log', {'job_id': 'probe'}, access('工作台-页面', '任务日志-页面'))
            query.assert_not_called()

    def test_task_tool_excludes_scripts_and_credentials(self):
        job = dict(job_id='probe', enabled=True, trigger='interval', type='local',
               next_run='later', script_text='sensitive-script', password='secret')
        with patch('common.task_center.list_tasks', return_value={'online': True, 'total': 1, 'rows': [job]}):
            result = execute_tool('list_tasks', {'limit': 1}, access('工作台-页面', '任务管理-页面'))
        self.assertEqual(result['total'], 1)
        self.assertNotIn('sensitive-script', str(result))
        self.assertNotIn('password', str(result))

    def test_invalid_arguments_never_read_data(self):
        with patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs') as query:
            for arguments in [{'limit': True}, {'limit': 0}, {'limit': 21}, {'limit': 1, 'script': 'print(1)'}, []]:
                with self.subTest(arguments=arguments), self.assertRaises(AgentError):
                    execute_tool('list_tasks', arguments, access('工作台-页面', '任务管理-页面'))
            query.assert_not_called()

    def test_logs_are_redacted_and_bounded(self):
        log = 'password=secret-value\n' + ('x' * 9000)
        with patch('common.task_center.latest_log', return_value={
            'status': 'ok', 'job_id': 'probe', 'log': log,
        }):
            result = execute_tool('read_latest_log', {'job_id': 'probe'}, access('工作台-页面', '任务日志-页面'), True)
        self.assertLessEqual(len(result['log']), 8000)
        self.assertTrue(result['truncated'])
        self.assertNotIn('secret-value', result['log'])
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'private-probe'}):
            self.assertNotIn('private-probe', redact('private-probe'))
        self.assertNotIn('abc123', redact('Bearer abc123'))
        self.assertNotIn('json-secret', redact('{"api_key": "json-secret"}'))
        self.assertNotIn('token-secret', redact('token=token-secret'))

    def test_demo_agent_requires_consent_for_local_synthetic_log(self):
        from scripts.demo_task_center import DEMO_AGENT_MODEL, demo_agent_turn

        menu_access = access('工作台-页面', '任务日志-页面')
        with patch('common.agent.execute_tool') as execute:
            denied = demo_agent_turn('查看票据归档日志', menu_access)
            execute.assert_not_called()
            self.assertIn('没有读取任何日志', denied['answer'])
            self.assertEqual(denied['trace'], [])
            running_log = demo_agent_turn('查看票据归档的最近运行日志', menu_access)
            self.assertIn('没有读取任何日志', running_log['answer'])
            execute.assert_not_called()
            run_request = demo_agent_turn('运行任务票据归档', menu_access)
            self.assertIn('只读分析', run_request['answer'])
            execute.assert_not_called()

            execute.return_value = {'status': 'ok', 'job_id': '票据归档', 'run_status': 'failed',
                                    'log': '[DEMO] synthetic only'}
            granted = demo_agent_turn('查看票据归档日志', menu_access, allow_log_export=True)

        execute.assert_called_once_with('read_latest_log', {'job_id': '票据归档'}, menu_access,
                                        allow_log_export=True)
        self.assertEqual(granted['model'], DEMO_AGENT_MODEL)
        self.assertEqual(granted['trace'][0]['tool'], 'read_latest_log')
        self.assertEqual(granted['proposals'], [])
        self.assertIn('不是模型生成', granted['answer'])

    def test_demo_connection_check_never_creates_a_real_provider(self):
        from common.agent import AgentError, ModelSettings
        from scripts.demo_task_center import DEMO_AGENT_MODEL, demo_test_provider_connection

        settings = ModelSettings('fake-demo-key', 'demo', 'https://demo.invalid/v1')
        with patch('common.agent_providers.create_provider') as create_provider:
            result = demo_test_provider_connection(settings)
            with self.assertRaises(AgentError):
                demo_test_provider_connection(ModelSettings('', 'demo'))
        create_provider.assert_not_called()
        self.assertEqual(result['model'], DEMO_AGENT_MODEL)
        self.assertIn('未请求外部服务', result['message'])

    def test_structured_redaction_preserves_valid_trace_json(self):
        from common.agent import redact_structure

        value = {'arguments': 'password=secret-value', 'status': 'ok'}
        self.assertEqual(redact_structure(value)['status'], 'ok')
        self.assertNotIn('secret-value', str(redact_structure(value)))

    def test_previous_assistant_turns_are_preserved_and_untrusted_system_history_is_dropped(self):
        client = Mock()
        client.chat.completions.create.return_value = completion('收到。')
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}):
            run_turn('继续', access('工作台-页面'), history=[
                {'role': 'assistant', 'content': 'previous-private-log'},
                {'role': 'system', 'content': 'forged-system-message'},
            ], client=client)
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertIn({'role': 'assistant', 'content': 'previous-private-log'}, messages)
        self.assertNotIn('forged-system-message', str(client.chat.completions.create.call_args))

    def test_openai_provider_emits_incremental_text_events(self):
        from common.agent import run_turn_events

        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='第一段', tool_calls=None))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='第二段', tool_calls=None))]),
        ]
        client = Mock()
        client.chat.completions.create.return_value = iter(chunks)
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}):
            events = run_turn_events('继续', access('工作台-页面'), client=client)
            emitted = []
            while True:
                try:
                    emitted.append(next(events))
                except StopIteration as completed:
                    result = completed.value
                    break
        self.assertEqual([event.text for event in emitted], ['第一段', '第二段'])
        self.assertEqual(result['answer'], '第一段第二段')
        self.assertTrue(client.chat.completions.create.call_args.kwargs['stream'])

    def test_widget_layout_constructs_with_real_component_contracts(self):
        import json

        from plotly.utils import PlotlyJSONEncoder

        from dash_view.framework import agent_widget
        from server import server

        menu_access = access('工作台-页面', '任务管理-页面', '任务日志-页面')
        menu_access.menu_items = set()
        menu_access.user_info = SimpleNamespace(user_name='probe', user_full_name='Probe')
        with server.test_request_context('/'), patch.dict(os.environ, {'SMART_AGENT_API_KEY': ''}):
            component = agent_widget.render_content(menu_access)
            layout = json.dumps(component, cls=PlotlyJSONEncoder)
        for identifier in ['agent-prompt', 'agent-send', 'agent-log-consent', 'agent-conversation-select',
                           'agent-launcher', 'agent-panel', 'agent-robot-scene',
                           'page-agent-launcher', 'page-agent-status', 'page-agent-scope']:
            self.assertIn(identifier, layout)
        self.assertIn('/assets/imgs/digital-human-mark.svg', layout)
        self.assertIn('agent-empty-portrait', layout)
        self.assertIn('agent-header-portrait', layout)
        self.assertNotIn('Agent 工作台', layout)
        self.assertIn('"hidden": true', layout)
        from config.access_factory import AccessFactory
        self.assertNotIn(agent_widget, AccessFactory.views)
        self.assertIsNone(AccessFactory.get_dict_access_meta2menu_item()['工作台-页面'])
        self.assertIsNone(agent_widget.render_content(access()))
        identifiers = []

        def collect_ids(value):
            if isinstance(value, list):
                for child in value:
                    collect_ids(child)
            elif hasattr(value, 'to_plotly_json'):
                props = value.to_plotly_json()['props']
                if 'id' in props:
                    identifiers.append(props['id'])
                collect_ids(props.get('children'))

        collect_ids(component)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(component.children[-1].max_intervals, 1)
        self.assertTrue(component.children[-1].disabled)
        self.assertNotIn('run_script', layout)

    def test_page_agent_url_requires_a_credential_free_https_url(self):
        from dash_view.framework.page_assistant import LOCAL_PAGE_AGENT_URL, page_agent_url, validate_page_agent_url

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(page_agent_url(), LOCAL_PAGE_AGENT_URL)
        self.assertTrue(validate_page_agent_url(LOCAL_PAGE_AGENT_URL))
        self.assertTrue(validate_page_agent_url('https://example.test/page-agent.js'))
        self.assertFalse(validate_page_agent_url('http://example.test/page-agent.js'))
        self.assertFalse(validate_page_agent_url('https://user:password@example.test/page-agent.js'))
        self.assertFalse(validate_page_agent_url('https://example.test/page-agent.js?token=secret'))
        self.assertFalse(validate_page_agent_url('/assets/vendor/page-agent/../secret.js'))
        self.assertFalse(validate_page_agent_url('/assets/js/page-agent.js'))

    def test_vendored_page_agent_asset_is_served_locally(self):
        from app import server
        from dash_callback.pages.agent_widget_c import load_page_agent
        from dash_view.framework.page_assistant import LOCAL_PAGE_AGENT_URL

        response = server.test_client().get(LOCAL_PAGE_AGENT_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'PageAgent', response.data)
        self.assertNotIn(LOCAL_PAGE_AGENT_URL, server.test_client().get('/').get_data(as_text=True))
        with server.test_request_context('/'), \
                patch('common.utilities.util_menu_access.get_menu_access',
                      return_value=access('工作台-页面')), \
                patch.dict(os.environ, {}, clear=True):
            loaded_url, message, status_class, disabled = load_page_agent(1)
        self.assertEqual(loaded_url, LOCAL_PAGE_AGENT_URL + '?autoInit=false')
        self.assertIn('page-agent-status-loading', status_class)
        self.assertNotIn('page-agent-status-ready', status_class)
        self.assertNotIn('已连接', message)
        self.assertTrue(disabled)

    def test_page_agent_proxy_uses_server_side_openai_credentials(self):
        from app import server

        upstream = Mock(
            status_code=200,
            headers={'Content-Type': 'application/json'},
            content=b'{"choices":[{"message":{"content":"ok"}}]}',
        )
        settings = ModelSettings('server-secret', 'internal-model', 'https://llm.intranet/v1')
        with patch('server._page_agent_access', return_value=access('工作台-页面')), \
                patch('database.sql_db.repositories.agent_provider_repository.default_profile_id',
                      return_value=None), \
                patch('common.agent.ModelSettings.from_environment', return_value=settings), \
                patch('requests.post', return_value=upstream) as post:
            response = server.test_client().post(
                '/page_agent_llm/v1/chat/completions',
                json={'model': 'browser-value', 'messages': [{'role': 'user', 'content': 'help'}],
                      'stream': False},
                headers={'Authorization': 'Bearer page-agent-browser', 'Origin': 'http://localhost'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.call_args.args[0], 'https://llm.intranet/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer server-secret')
        self.assertEqual(post.call_args.kwargs['json']['model'], 'internal-model')
        upstream.close.assert_called_once()

    def test_page_agent_proxy_rejects_unidentified_browser_client(self):
        from app import server

        response = server.test_client().post(
            '/page_agent_llm/v1/chat/completions',
            json={'messages': [{'role': 'user', 'content': 'help'}]},
        )
        self.assertEqual(response.status_code, 401)

    def test_widget_formats_assistant_markdown_without_enabling_html(self):
        from dash import dcc, html
        from dash_view.framework.agent_widget import render_messages
        from types import SimpleNamespace

        content = '### Result\n\n**Ready**\n\n<script>alert(1)</script>'
        user_info = SimpleNamespace(user_name='grace.hopper', user_full_name='Grace Hopper')
        with patch('dash_view.framework.agent_widget.avatar_file_exists', return_value=True):
            messages = render_messages([
                {'role': 'assistant', 'content': content, 'created_at': '2026-10-05T12:30:00'},
                {'role': 'user', 'content': content, 'created_at': '2026-10-05T12:31:00'},
            ], user_info)
        assistant_avatar = messages[0].children[0].children[0]
        self.assertEqual(assistant_avatar.src, '/assets/imgs/digital-human-mark.svg')
        self.assertEqual(assistant_avatar.className, 'agent-message-avatar agent-message-avatar-assistant')
        self.assertIsInstance(messages[0].children[1], dcc.Markdown)
        self.assertFalse(messages[0].children[1].dangerously_allow_html)
        user_avatar = messages[1].children[0].children[-1]
        self.assertEqual(user_avatar.className, 'agent-message-avatar agent-message-avatar-user')
        self.assertEqual(user_avatar.src, '/avatar/grace.hopper')
        self.assertEqual(user_avatar.alt, 'GH')
        self.assertEqual(user_avatar.to_plotly_json()['props']['aria-label'], 'Grace Hopper')
        self.assertIsInstance(messages[1].children[1], html.Div)
        self.assertEqual(messages[1].children[1].children, content)
        timestamp = messages[0].children[0].children[2]
        self.assertEqual(timestamp.children, '12:30')
        self.assertEqual(timestamp.title, '2026-10-05 12:30:00')

    def test_widget_callbacks_are_registered_before_shell_rendering(self):
        from app import server

        response = server.test_client().get('/_dash-dependencies')
        dependencies = response.get_json()
        inputs = {(control['id'], control['property'])
                  for callback in dependencies for control in callback['inputs'] if isinstance(control['id'], str)}
        self.assertIn(('agent-init', 'n_intervals'), inputs)
        self.assertIn(('agent-send', 'nClicks'), inputs)
        from common.utilities.util_dash import CALLBACK_PERMISSIONS

        self.assertEqual(CALLBACK_PERMISSIONS['dash_callback.pages.agent_widget_c'], '工作台-页面')

    def test_model_proposal_is_returned_without_any_execution(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(calls=[tool_call('propose_run_task', '{"job_id": "probe"}')]), completion('请在审批区核对。'),
        ]
        job = SimpleNamespace(job_id='probe', status=True, type='local', script_text='private-script',
                              script_type='Python', timeout=30, extract_names=None, notify_channels=[])
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe',
                                    'SMART_AGENT_RUN_ALLOWLIST': '["probe"]'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[job]), \
            patch('database.sql_db.repositories.task_center_repository.find_task', return_value=SimpleNamespace(task_id='task-probe')), \
            patch('common.task_center._task_access'), \
            patch('common.task_center.request_run') as submit:
            result = run_turn('提议运行probe', access('工作台-页面', '任务管理-页面'), client=client)
            submit.assert_not_called()
        self.assertEqual(len(result['proposals']), 1)
        self.assertEqual(result['trace'][0]['status'], 'pending_approval')
        self.assertNotIn('private-script', str(client.chat.completions.create.call_args))

    def test_approval_layout_has_real_component_contracts(self):
        import json
        from datetime import datetime

        from plotly.utils import PlotlyJSONEncoder
        from dash_view.framework.agent_widget import render_actions

        action = {'job_id': 'probe', 'status': 'pending', 'summary': '提议运行', 'task_digest': 'a' * 64,
                  'run_id': 'agent_run_probe', 'expires_at': datetime.now(), 'decided_at': None,
                  'action_id': 'a' * 32, 'result': ''}
        layout = json.dumps(render_actions([action], True), cls=PlotlyJSONEncoder)
        self.assertIn('agent-action-decision', layout)
        self.assertIn('agent-action-consent', layout)

    def test_real_sdk_contract_executes_read_tool_and_returns_trace(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(calls=[tool_call('list_tasks', '{"limit": 5}')]), completion('当前没有任务。'),
        ]
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[]):
            result = run_turn('查询任务', access('工作台-页面', '任务管理-页面'), client=client)
        self.assertEqual(result['answer'], '当前没有任务。')
        self.assertEqual(result['trace'][0]['tool'], 'list_tasks')
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertEqual(client.chat.completions.create.call_args.kwargs['messages'][-1]['role'], 'tool')

    def test_official_sdk_with_local_test_transport(self):
        import json

        import httpx
        from openai import OpenAI

        payloads = []

        def transport(request):
            payloads.append(json.loads(request.content))
            message = {'role': 'assistant', 'content': '没有任务。'}
            if len(payloads) == 1:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call_probe', 'type': 'function', 'function': {
                        'name': 'list_tasks', 'arguments': '{"limit": 5}',
                    }},
                ]}
            return httpx.Response(200, json={
                'id': 'test-local-only', 'object': 'chat.completion', 'created': 1, 'model': 'probe',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': message}],
            })

        with httpx.Client(transport=httpx.MockTransport(transport)) as http_client, \
                OpenAI(api_key='test-only', base_url='https://mock.invalid/v1', http_client=http_client) as client, \
                patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[]):
            result = run_turn('查询任务', access('工作台-页面', '任务管理-页面'), client=client)
        self.assertEqual(result['answer'], '没有任务。')
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[1]['messages'][-1]['role'], 'tool')

    def test_model_tool_loop_is_bounded(self):
        client = Mock()
        client.chat.completions.create.return_value = completion(calls=[tool_call('list_tasks', '{"limit": 5}')])
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[]), \
                self.assertRaises(AgentError):
            run_turn('查询任务', access('工作台-页面', '任务管理-页面'), client=client)
        self.assertEqual(client.chat.completions.create.call_count, 3)

    def test_revoked_tool_permission_is_checked_before_read(self):
        client = Mock()
        client.chat.completions.create.return_value = completion(calls=[tool_call('list_tasks', '{"limit": 5}')])
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs') as query, \
                self.assertRaises(AgentError):
            run_turn('查询任务', access('工作台-页面', '任务管理-页面'), client=client,
                     access_provider=lambda: access('工作台-页面'))
        query.assert_not_called()

    def test_model_failures_and_invalid_tool_json_are_safe(self):
        for response in [completion(calls=[tool_call('run_script', '{}')]),
                         completion(calls=[tool_call('list_tasks', '{broken')])]:
            client = Mock()
            client.chat.completions.create.return_value = response
            with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}), \
                    self.assertRaises(AgentError):
                run_turn('查询任务', access('工作台-页面', '任务管理-页面'), client=client)
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError('private-url-and-key')
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}):
            with self.assertRaises(AgentError) as caught:
                run_turn('查询任务', access('工作台-页面'), client=client)
        self.assertNotIn('private-url-and-key', str(caught.exception))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        from peewee import SqliteDatabase

        from database.sql_db.repositories import agent_repository
        from database.sql_db.entity.table_agent import (
            AgentAction, AgentConversation, AgentConversationProfile, AgentMessage,
            AgentProviderOptions, AgentProviderProfile, AgentProviderState, AgentRemoteSession,
        )
        from database.sql_db.repositories import agent_provider_repository

        self.folder = tempfile.TemporaryDirectory()
        self.database = SqliteDatabase(Path(self.folder.name) / 'agent.db')
        self.binding = self.database.bind_ctx([
            AgentAction, AgentConversation, AgentConversationProfile, AgentMessage,
            AgentProviderOptions, AgentProviderProfile, AgentProviderState, AgentRemoteSession,
        ])
        self.binding.__enter__()
        self.patcher = patch.object(agent_repository, 'db', return_value=self.database)
        self.patcher.start()
        self.profile_patcher = patch.object(agent_provider_repository, 'db', return_value=self.database)
        self.profile_patcher.start()
        from config.anita_go_conf import CommonConf

        self.key_patcher = patch.object(CommonConf, 'ENCRYPT_KEY', '33' * 32)
        self.key_patcher.start()
        self.repository = agent_repository
        for boundary in [
                patch('database.sql_db.repositories.task_center_repository.find_task', return_value=SimpleNamespace(task_id='task-probe')),
                patch('common.task_center._task_access')]:
            boundary.start()
            self.addCleanup(boundary.stop)

    def tearDown(self):
        self.key_patcher.stop()
        self.profile_patcher.stop()
        self.patcher.stop()
        self.binding.__exit__(None, None, None)
        self.database.close()
        self.folder.cleanup()

    def test_conversations_are_owned_and_persist_history(self):
        conversation_id = self.repository.create_conversation('alice')
        turn_id, history = self.repository.begin_turn('alice', conversation_id, '查询任务')
        self.assertEqual(history, [])
        self.repository.finish_turn('alice', conversation_id, turn_id, '暂无任务', model_name='probe')
        self.assertEqual([row['role'] for row in self.repository.read_messages('alice', conversation_id)], ['user', 'assistant'])
        self.assertEqual(self.repository.list_conversations('bob'), [])
        with self.assertRaises(AgentError):
            self.repository.read_messages('bob', conversation_id)
        with self.assertRaises(AgentError):
            self.repository.begin_turn('bob', conversation_id, '越权请求')

    def test_duplicate_and_stale_turns_are_rejected(self):
        conversation_id = self.repository.create_conversation('alice')
        turn_id, _ = self.repository.begin_turn('alice', conversation_id, '查询任务')
        with self.assertRaises(AgentError):
            self.repository.begin_turn('alice', conversation_id, '重复请求')
        with self.assertRaises(AgentError):
            self.repository.finish_turn('alice', conversation_id, 'invalid', '伪造结果')
        self.repository.finish_turn('alice', conversation_id, turn_id, '超时', failed=True)
        with self.assertRaises(AgentError):
            self.repository.finish_turn('alice', conversation_id, turn_id, '重复结果')
        _, history = self.repository.begin_turn('alice', conversation_id, '重新查询')
        self.assertEqual([row['role'] for row in history], ['user'])

    def test_remote_agent_sessions_are_owned_and_provider_scoped(self):
        conversation_id = self.repository.create_conversation('alice')
        self.repository.set_remote_session('alice', conversation_id, 'vendor_a', 'remote-a')
        self.repository.set_remote_session('alice', conversation_id, 'vendor_b', 'remote-b')
        self.assertEqual(self.repository.get_remote_session('alice', conversation_id, 'vendor_a'), 'remote-a')
        self.assertEqual(self.repository.get_remote_session('alice', conversation_id, 'vendor_b'), 'remote-b')
        self.assertIsNone(self.repository.get_remote_session('alice', conversation_id, 'vendor_c'))
        with self.assertRaises(AgentError):
            self.repository.get_remote_session('bob', conversation_id, 'vendor_a')

    def test_provider_profiles_encrypt_secrets_and_blank_edit_keeps_existing_key(self):
        from database.sql_db.entity.table_agent import AgentProviderProfile
        from database.sql_db.repositories import agent_provider_repository

        profile_id = agent_provider_repository.save_profile(
            None, '比赛主 Agent', 'openai_compatible', 'https://api.example.com/v1',
            'model-a', 'profile-secret-probe', True,
        )
        row = AgentProviderProfile.get_by_id(profile_id)
        self.assertNotIn('profile-secret-probe', row.encrypted_api_key)
        public_profile = agent_provider_repository.get_profile(profile_id)
        self.assertNotIn('api_key', public_profile)
        self.assertTrue(public_profile['has_api_key'])
        self.assertEqual(agent_provider_repository.resolve_profile_settings(profile_id).api_key,
                         'profile-secret-probe')

        updated_id = agent_provider_repository.save_profile(
            profile_id, '比赛主 Agent v2', 'openai_compatible', 'https://api.example.com/v1',
            'model-b', '', False,
        )
        self.assertEqual(updated_id, profile_id)
        updated = agent_provider_repository.get_profile(profile_id, include_secret=True)
        self.assertEqual(updated['name'], '比赛主 Agent v2')
        self.assertEqual(updated['model'], 'model-b')
        self.assertEqual(updated['api_key'], 'profile-secret-probe')

    def test_conversation_provider_switch_is_persisted_and_resets_remote_session(self):
        from database.sql_db.repositories import agent_provider_repository

        first = agent_provider_repository.save_profile(
            None, '服务 A', 'openai_compatible', 'https://a.example.com/v1', 'model-a', 'key-a', True,
        )
        second = agent_provider_repository.save_profile(
            None, '服务 B', 'openai_compatible', 'https://b.example.com/v1', 'model-b', 'key-b', False,
        )
        conversation_id = self.repository.create_conversation('alice')
        self.assertEqual(agent_provider_repository.get_conversation_profile('alice', conversation_id), first)
        first_settings = agent_provider_repository.resolve_profile_settings(first)
        self.repository.set_remote_session('alice', conversation_id, first_settings.remote_session_key, 'remote-a')
        turn_id, _ = self.repository.begin_turn('alice', conversation_id, 'First')
        self.repository.finish_turn(
            'alice', conversation_id, turn_id, 'Reply',
            provider_id=first_settings.remote_session_key,
            provider_state={'messages': [{'content': 'native-history'}]},
        )

        agent_provider_repository.set_conversation_profile('alice', conversation_id, second)
        self.assertEqual(agent_provider_repository.get_conversation_profile('alice', conversation_id), second)
        self.assertIsNone(self.repository.get_remote_session(
            'alice', conversation_id, first_settings.remote_session_key))
        self.assertIsNone(self.repository.get_provider_state(
            'alice', conversation_id, first_settings.remote_session_key))
        with self.assertRaises(AgentError):
            agent_provider_repository.set_conversation_profile('bob', conversation_id, first)

    def test_deleting_profile_migrates_conversations_and_clears_remote_sessions(self):
        from database.sql_db.repositories import agent_provider_repository as profiles

        first = profiles.save_profile(None, 'First', 'openai_compatible',
                                      'https://example.com/v1', 'model-a', 'secret-a')
        second = profiles.save_profile(None, 'Second', 'openai_compatible',
                                       'https://example.com/v1', 'model-b', 'secret-b')
        conversation = self.repository.create_conversation('alice')
        self.repository.set_remote_session('alice', conversation, 'provider-a', 'remote-a')
        profiles.delete_profile(first)
        self.assertEqual(profiles.get_conversation_profile('alice', conversation), second)
        self.assertEqual(profiles.default_profile_id(), second)
        self.assertIsNone(self.repository.get_remote_session('alice', conversation, 'provider-a'))
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe'}):
            profiles.delete_profile(second)
            self.assertEqual(profiles.get_conversation_profile('alice', conversation), 'environment')

    def test_connection_test_never_exposes_tools_to_the_provider(self):
        from common.agent import test_provider_connection

        client = Mock()
        client.chat.completions.create.return_value = completion('OK')
        settings = ModelSettings(
            api_key='test-secret', model='probe', base_url='https://mock.invalid/v1',
        )
        result = test_provider_connection(settings, client=client)
        self.assertEqual(result['message'], '连接成功，服务已返回测试回答。')
        self.assertNotIn('tools', client.chat.completions.create.call_args.kwargs)

    def _internal_agent_profile(self):
        from database.sql_db.repositories import agent_provider_repository as profiles

        options = {
            'agent_id': '11111111-1111-4111-8111-111111111111',
            'tenant_id': '22222222-2222-4222-8222-222222222222',
            'client_id': '33333333-3333-4333-8333-333333333333',
            'client_secret': 'repository-test-client-secret', 'scope': 'api://test/.default',
        }
        profile_id = profiles.save_profile(
            None, 'Internal Agent API', 'internal_agent', 'https://internal-agent.invalid/v5', '',
            'repository-test-subscription', provider_options=options)
        return profile_id, options

    def test_internal_agent_profile_encrypts_both_secrets_and_blank_edit_preserves_them(self):
        import json
        from database.sql_db.repositories import agent_provider_repository as profiles
        from database.sql_db.entity.table_agent import AgentProviderOptions

        profile_id, options = self._internal_agent_profile()
        public = profiles.get_profile(profile_id)
        self.assertNotIn('client_secret', json.dumps(public))
        encrypted = AgentProviderOptions.get_by_id(profile_id).encrypted_options
        self.assertNotIn(options['client_secret'], encrypted)
        edited = {key: value for key, value in options.items() if key != 'client_secret'}
        profiles.save_profile(profile_id, 'Renamed', 'internal_agent', 'https://internal-agent.invalid/v5',
                              '', '', provider_options=edited)
        private = profiles.get_profile(profile_id, include_secret=True)
        self.assertEqual(private['provider_options'], options)
        self.assertEqual(private['api_key'], 'repository-test-subscription')

    def test_legacy_profiles_work_when_internal_agent_tables_are_created_on_upgrade(self):
        from database.sql_db.repositories import agent_provider_repository as profiles
        from database.sql_db.entity.table_agent import AgentProviderOptions, AgentProviderState

        profile_id = profiles.save_profile(
            None, 'Existing', 'openai_compatible', 'https://existing.invalid/v1', 'existing-model', 'existing-key')
        conversation = self.repository.create_conversation('alice')
        self.database.drop_tables([AgentProviderOptions, AgentProviderState])
        restored = profiles.resolve_profile_settings(profile_id)
        self.assertEqual(restored.api_key, 'existing-key')
        self.assertEqual(restored.model, 'existing-model')
        self.assertEqual(restored.provider_options, {})
        self.assertIsNone(self.repository.get_provider_state('alice', conversation, restored.remote_session_key))
        self.assertTrue(AgentProviderOptions.table_exists())
        self.assertTrue(AgentProviderState.table_exists())

    def test_provider_state_is_encrypted_owned_atomic_and_reset_on_connection_changes(self):
        from database.sql_db.repositories import agent_provider_repository as profiles
        from database.sql_db.entity.table_agent import AgentProviderState

        profile_id, options = self._internal_agent_profile()
        settings = profiles.resolve_profile_settings(profile_id)
        conversation = self.repository.create_conversation('alice')
        turn, _ = self.repository.begin_turn('alice', conversation, 'First')
        state = {'messages': [{'text': 'private-native-history'}]}
        self.repository.finish_turn('alice', conversation, turn, 'Reply',
                                    provider_id=settings.remote_session_key, provider_state=state)
        self.assertNotIn('private-native-history', AgentProviderState.get().encrypted_state)
        self.assertEqual(self.repository.get_provider_state('alice', conversation, settings.remote_session_key), state)
        self.assertIsNone(self.repository.get_provider_state('alice', conversation, 'another-profile'))
        with self.assertRaises(AgentError):
            self.repository.get_provider_state('bob', conversation, settings.remote_session_key)
        turn, _ = self.repository.begin_turn('alice', conversation, 'Next')
        with self.assertRaisesRegex(AgentError, '正在处理'):
            profiles.save_profile(profile_id, 'Edited', 'internal_agent', 'https://changed.invalid/v5',
                                  '', '', provider_options=options)
        self.repository.finish_turn('alice', conversation, turn, 'Failed', failed=True,
                                    provider_id=settings.remote_session_key,
                                    provider_state={'messages': [{'text': 'invalid advancement'}]})
        self.assertEqual(self.repository.get_provider_state('alice', conversation, settings.remote_session_key), state)
        profiles.save_profile(profile_id, 'Edited', 'internal_agent', 'https://changed.invalid/v5',
                              '', '', provider_options=options)
        self.assertIsNone(self.repository.get_provider_state('alice', conversation, settings.remote_session_key))

    def test_internal_agent_sse_route_persists_native_history_across_two_turns(self):
        import json
        import httpx
        from common.internal_agent_provider import InternalAgentProvider, _tokens
        from database.sql_db.repositories import agent_provider_repository as profiles
        from app import server

        _tokens.clear()
        profile_id, _ = self._internal_agent_profile()
        conversation = self.repository.create_conversation('probe')
        calls = []

        def transport(request):
            if request.url.host == 'login.microsoftonline.com':
                return httpx.Response(200, json={
                    'access_token': 'route-test-token', 'token_type': 'Bearer', 'expires_in': 3600,
                })
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={
                'response': {'status': 'SUCCESS', 'model': 'internal_agent-test', 'output': [{
                    'role': 'assistant', 'type': 'message',
                    'content': [{'type': 'output_text', 'text': 'Internal Agent API reply'}],
                }]},
                'messages': [{'role': 'assistant', 'native': f'turn-{len(calls)}'}],
            })

        with httpx.Client(transport=httpx.MockTransport(transport)) as client, \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=access('工作台-页面')), \
                patch('common.agent_providers.create_provider',
                      side_effect=lambda settings, **kwargs: InternalAgentProvider(settings, client)):
            for prompt in ('First', 'Second'):
                response = server.test_client().post('/agent_stream', json={
                    'conversation_id': conversation, 'profile_id': profile_id,
                    'prompt': prompt, 'allow_log_export': False,
                }, buffered=True)
                self.assertIn('event: completed', response.get_data(as_text=True))
                self.assertNotIn('messages', response.get_data(as_text=True))
        self.assertEqual(calls[0]['history'], [])
        self.assertEqual(calls[1]['history'], [{'role': 'assistant', 'native': 'turn-1'}])
        key = profiles.resolve_profile_settings(profile_id).remote_session_key
        self.assertEqual(self.repository.get_provider_state('probe', conversation, key)['messages'][0]['native'],
                         'turn-2')
        profiles.delete_profile(profile_id)
        self.assertIsNone(self.repository.get_provider_state('probe', conversation, key))

    def enterprise_profile(self):
        from datetime import datetime, timedelta, timezone
        from database.sql_db.repositories import agent_provider_repository as profiles
        from common.enterprise_models_credentials import PREFIX
        from common.enterprise_models_provider import configured_endpoint

        token_file = Path(self.folder.name) / 'token.dat'
        token_file.write_text(json.dumps({
            'access_token': 'fake-enterprise-access', 'refresh_token': 'fake-never-use-refresh',
            'token_expiry': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }), encoding='utf-8')
        environment = patch.dict(os.environ, {
            PREFIX + 'TOKEN_FILE': str(token_file), PREFIX + 'TOKEN_TIMEZONE': 'UTC',
            PREFIX + 'OWNER': 'probe',
            PREFIX + 'ENDPOINT': 'https://models.test.invalid/v1/chat/stream',
        })
        environment.start()
        self.addCleanup(environment.stop)
        admin = access('工作台-页面', 'Agent配置-页面')
        admin.user_info.user_roles = ['admin']
        profile = profiles.save_profile(None, 'Enterprise test', 'enterprise_models', configured_endpoint(),
                                       'gpt-5-mini', '', True, access=admin)
        return profiles, profile, admin, token_file

    def test_enterprise_file_credentials_are_not_persisted_or_available_to_other_admins(self):
        from database.sql_db.entity.table_agent import AgentProviderProfile
        from common.enterprise_models_credentials import EnterpriseAccessError

        profiles, profile, admin, _token_file = self.enterprise_profile()
        self.assertEqual(profiles.resolve_profile_settings(profile, access=admin).api_key, 'fake-enterprise-access')
        row = AgentProviderProfile.get_by_id(profile)
        self.assertEqual(profiles._decrypt(row.encrypted_api_key), '')
        self.assertNotIn('fake-enterprise-access', str(profiles.get_profile(profile, access=admin)))
        other = access('工作台-页面', 'Agent配置-页面')
        other.user_info.user_roles = ['admin']
        other.user_name = 'bob'
        with patch('common.enterprise_models_credentials.read_credential') as read:
            self.assertEqual(profiles.profile_options(other), [])
            with self.assertRaises(EnterpriseAccessError):
                profiles.resolve_profile_settings(profile, access=other)
            read.assert_not_called()

    def test_enterprise_forged_http_profile_is_forbidden_without_reading_file(self):
        from app import server

        _profiles, profile, _admin, _token_file = self.enterprise_profile()
        conversation = self.repository.create_conversation('probe')
        with patch('common.utilities.util_menu_access.get_menu_access', return_value=access('工作台-页面')), \
                patch('common.enterprise_models_credentials.read_credential') as read:
            response = server.test_client().post('/agent_stream', json={
                'conversation_id': conversation, 'profile_id': profile,
                'prompt': 'Forbidden', 'allow_log_export': False,
            }, buffered=True)
            self.assertEqual(response.status_code, 403)
            read.assert_not_called()
        self.assertEqual(self.repository.read_messages('probe', conversation), [])

    def test_enterprise_two_turns_rotation_and_401_rejection(self):
        import httpx
        from app import server
        from common.enterprise_models_provider import EnterpriseModelsProvider

        profiles, profile, admin, token_file = self.enterprise_profile()
        conversation = self.repository.create_conversation('probe')
        calls = []
        status = [200]

        def transport(request):
            calls.append(json.loads(request.content))
            return httpx.Response(status[0], headers={'content-type': 'text/event-stream'}, text=(
                'data: {"chatId":"native","current":[{"id":"parent","content":"Enterprise reply"}]}\n\n'
                'data: [DONE]\n\n'))

        with httpx.Client(transport=httpx.MockTransport(transport)) as client, \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=admin), \
                patch('common.agent_providers.create_provider',
                      side_effect=lambda settings, **kwargs: EnterpriseModelsProvider(settings, client)):
            def send():
                return server.test_client().post('/agent_stream', json={
                    'conversation_id': conversation, 'profile_id': profile,
                    'prompt': 'Question', 'allow_log_export': False,
                }, buffered=True)

            for _ in range(2):
                self.assertIn('event: completed', send().get_data(as_text=True))
            self.assertNotIn('chatId', calls[0])
            self.assertEqual(calls[1]['parentId'], 'parent')
            data = json.loads(token_file.read_text(encoding='utf-8'))
            data['access_token'] = 'fake-rotated-access'
            token_file.write_text(json.dumps(data), encoding='utf-8')
            self.assertIn('event: completed', send().get_data(as_text=True))
            self.assertNotIn('chatId', calls[2])
            status[0] = 401
            self.assertIn('event: error', send().get_data(as_text=True))
            self.assertEqual(len(calls), 4)
            self.assertEqual(send().status_code, 400)
            self.assertEqual(len(calls), 4)
            with self.assertRaises(AgentError):
                profiles.resolve_profile_settings(profile, access=admin)

    def test_enterprise_changed_file_cannot_finish_old_turn(self):
        profiles, profile, admin, token_file = self.enterprise_profile()
        settings = profiles.resolve_profile_settings(profile, access=admin)
        conversation = self.repository.create_conversation('probe')
        turn, _history = self.repository.begin_turn('probe', conversation, 'Question')
        self.repository.prepare_provider_state('probe', conversation, turn, settings)
        data = json.loads(token_file.read_text(encoding='utf-8'))
        data['access_token'] = 'different-account-token'
        token_file.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(AgentError):
            self.repository.finish_turn('probe', conversation, turn, 'Old answer',
                provider_id=settings.remote_session_key, provider_state={
                    'provider': 'enterprise_models',
                    'credential_version': settings.provider_options['_credential_version'],
                    'chatId': 'old', 'parentId': 'old',
                })
        self.assertEqual(len(self.repository.read_messages('probe', conversation)), 1)

    def test_enterprise_incomplete_turn_requires_new_context(self):
        profiles, profile, admin, _token_file = self.enterprise_profile()
        settings = profiles.resolve_profile_settings(profile, access=admin)
        conversation = self.repository.create_conversation('probe')
        turn, _history = self.repository.begin_turn('probe', conversation, 'Question')
        self.repository.prepare_provider_state('probe', conversation, turn, settings)
        self.repository.finish_turn('probe', conversation, turn, 'Connection interrupted', failed=True)
        turn, _history = self.repository.begin_turn('probe', conversation, 'Do not replay')
        with self.assertRaises(AgentError):
            self.repository.prepare_provider_state('probe', conversation, turn, settings)

    def test_agent_stream_route_persists_response_and_remote_session(self):
        from server import server
        from common.agent_providers import AgentStreamEvent

        conversation_id = self.repository.create_conversation('probe')

        def stream_turn(*args, **kwargs):
            yield AgentStreamEvent('delta', '增量答复')
            return {
                'answer': '完整答复',
                'trace': [],
                'model': 'probe',
                'proposals': [],
                'provider': 'openai_compatible',
                'remote_conversation_id': 'remote-probe',
            }

        payload = {
            'conversation_id': conversation_id,
            'prompt': '继续',
            'allow_log_export': False,
        }
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}), \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=access('工作台-页面')), \
                patch('common.agent.run_turn_events', side_effect=stream_turn):
            response = server.test_client().post('/agent_stream', json=payload, buffered=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('event: delta', response.get_data(as_text=True))
        self.assertIn('增量答复', response.get_data(as_text=True))
        self.assertIn('event: completed', response.get_data(as_text=True))
        self.assertEqual(
            [message['role'] for message in self.repository.read_messages('probe', conversation_id)],
            ['user', 'assistant'],
        )
        self.assertEqual(
            self.repository.get_remote_session('probe', conversation_id, 'openai_compatible'),
            'remote-probe',
        )

    def test_agent_stream_route_persists_provider_error(self):
        from server import server

        conversation_id = self.repository.create_conversation('probe')

        def fail_turn(*args, **kwargs):
            if False:
                yield None
            raise AgentError('Agent 服务暂不可用。')

        payload = {
            'conversation_id': conversation_id,
            'prompt': '继续',
            'allow_log_export': False,
        }
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}), \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=access('工作台-页面')), \
                patch('common.agent.run_turn_events', side_effect=fail_turn):
            response = server.test_client().post('/agent_stream', json=payload, buffered=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('event: error', response.get_data(as_text=True))
        messages = self.repository.read_messages('probe', conversation_id)
        self.assertEqual([message['role'] for message in messages], ['user', 'error'])
        self.assertIn('Agent 服务暂不可用', messages[-1]['content'])

    def test_saved_content_and_trace_redact_secrets(self):
        conversation_id = self.repository.create_conversation('alice')
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'private-probe'}):
            turn_id, _ = self.repository.begin_turn('alice', conversation_id, 'private-probe')
            self.repository.finish_turn('alice', conversation_id, turn_id, 'private-probe',
                                 trace=[{'tool': 'read_latest_log', 'arguments': 'private-probe'}])
        self.assertNotIn('private-probe', str(self.repository.read_messages('alice', conversation_id)))

    def test_latest_log_query_reads_only_requested_job(self):
        from datetime import datetime, timedelta

        from database.sql_db.repositories import apscheduler_repository
        from database.sql_db.entity.table_apscheduler import SysApschedulerResults

        now = datetime.now()
        with self.database.bind_ctx([SysApschedulerResults]), patch.object(apscheduler_repository, 'db', return_value=self.database):
            self.database.create_tables([SysApschedulerResults])
            for job_id, started, log in [('probe', now - timedelta(minutes=1), 'old'),
                                         ('probe', now, 'new'), ('other', now, 'private-other')]:
                SysApschedulerResults.create(job_id=job_id, start_datetime=started, finish_datetime=now,
                                            status='error', log=log)
            result = apscheduler_repository.get_latest_completed_log('probe')
            self.assertEqual(result['log'], 'new')
            self.assertEqual(result['run_status'], 'error')
            self.assertNotIn('private-other', str(result))
            self.assertIsNone(apscheduler_repository.get_latest_completed_log('missing'))

    def test_callback_initializes_conversation_with_environment_profile(self):
        from dash_callback.pages import agent_widget_c
        from server import server

        menu_access = access('工作台-页面')
        with server.test_request_context('/'), \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=menu_access), \
                patch.object(agent_widget_c, 'get_menu_access', return_value=menu_access), \
                patch.object(agent_widget_c, 'ctx', SimpleNamespace(triggered_id='agent-init')), \
                patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe'}), \
                patch.object(agent_widget_c, 'set_props') as set_props:
            options, selected, profiles, profile_id = agent_widget_c.initialize_conversations(1, None)
        self.assertEqual(options[0]['value'], selected)
        self.assertEqual(profile_id, 'environment')
        self.assertIn('environment', [profile['value'] for profile in profiles])
        set_props.assert_called_once_with('agent-conversation-title', {'data-profile-id': 'environment'})

    def test_non_admin_cannot_invoke_provider_management_callback(self):
        from dash.exceptions import PreventUpdate
        from dash_callback.application.setting_ import agent_provider_c
        from server import server

        with server.test_request_context('/'), \
                patch('common.utilities.util_menu_access.get_menu_access',
                      return_value=access('工作台-页面')), \
                patch.object(agent_provider_c.agent_provider_repository, 'save_profile') as save:
            with self.assertRaises(PreventUpdate):
                agent_provider_c.manage_provider_profile(
                    0, 1, 0, 0, None, 'Forbidden', 'openai_compatible',
                    'https://example.com/v1', 'probe', 'test-only', False, 0)
        save.assert_not_called()

    def test_callback_persists_reply_and_returns_rendered_messages(self):
        from dash_callback.pages import agent_widget_c as workbench_c
        from server import server

        menu_access = access('工作台-页面', '任务管理-页面')
        conversation_id = self.repository.create_conversation(menu_access.user_name)
        with server.test_request_context('/'), \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=menu_access), \
                patch.object(workbench_c, 'get_menu_access', return_value=menu_access), \
                patch.object(workbench_c, 'ctx', SimpleNamespace(triggered_id='agent-send')), \
                patch.object(workbench_c, 'run_turn', return_value={'answer': '测试回复', 'trace': [], 'model': 'probe'}), \
                patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe'}), \
                patch.object(workbench_c, 'set_props'):
            result = workbench_c.handle_conversation(conversation_id, 1, None, conversation_id, '查询任务', False)
        self.assertEqual(result[1], conversation_id)
        self.assertEqual(result[3], '')
        self.assertEqual(self.repository.read_messages(menu_access.user_name, conversation_id)[-1]['content'], '测试回复')

    def test_callback_error_recovers_conversation_lease(self):
        from dash_callback.pages import agent_widget_c as workbench_c
        from server import server

        menu_access = access('工作台-页面')
        conversation_id = self.repository.create_conversation(menu_access.user_name)
        with server.test_request_context('/'), \
                patch('common.utilities.util_menu_access.get_menu_access', return_value=menu_access), \
                patch.object(workbench_c, 'get_menu_access', return_value=menu_access), \
                patch.object(workbench_c, 'ctx', SimpleNamespace(triggered_id='agent-send')), \
                patch.object(workbench_c, 'run_turn', side_effect=AgentError('模型超时')), \
                patch.object(workbench_c, 'set_props') as set_props, \
                patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe'}):
            result = workbench_c.handle_conversation(conversation_id, 1, None, conversation_id, '查询任务', False)
        set_props.assert_called_once_with('agent-progress', {'children': '正在处理请求…'})
        self.assertEqual(result[-1], '模型超时')
        self.assertEqual(self.repository.read_messages(menu_access.user_name, conversation_id)[-1]['role'], 'error')
        self.repository.begin_turn(menu_access.user_name, conversation_id, '重试查询')

    def test_action_is_owned_and_can_only_be_decided_once(self):
        conversation_id = self.repository.create_conversation('alice')
        action_id = self.repository.create_action('alice', conversation_id, 'probe', 'a' * 64, '运行已有任务')
        with self.assertRaises(AgentError):
            self.repository.decide_action('bob', conversation_id, action_id, True)
        action = self.repository.decide_action('alice', conversation_id, action_id, True)
        self.assertEqual(action['status'], 'submitting')
        self.assertEqual(action['run_id'], f'agent_run_{action_id}')
        with self.assertRaises(AgentError):
            self.repository.decide_action('alice', conversation_id, action_id, True)
        self.repository.finish_action('alice', conversation_id, action_id, 'unknown', '请求超时，禁止自动重试')
        self.assertEqual(self.repository.read_actions('alice', conversation_id)[0]['status'], 'unknown')
        with self.assertRaises(AgentError):
            self.repository.finish_action('alice', conversation_id, action_id, 'submitted', '重放结果')

    def test_rejected_and_expired_actions_cannot_execute(self):
        from datetime import datetime, timedelta

        from database.sql_db.entity.table_agent import AgentAction

        conversation_id = self.repository.create_conversation('alice')
        rejected = self.repository.create_action('alice', conversation_id, 'probe', 'a' * 64, '运行已有任务')
        self.repository.decide_action('alice', conversation_id, rejected, False)
        with self.assertRaises(AgentError):
            self.repository.decide_action('alice', conversation_id, rejected, True)
        expired = self.repository.create_action('alice', conversation_id, 'probe', 'a' * 64, '运行已有任务')
        with self.database.connection_context():
            AgentAction.update(expires_at=datetime.now() - timedelta(seconds=1)).where(
                AgentAction.action_id == expired).execute()
        with self.assertRaises(AgentError):
            self.repository.decide_action('alice', conversation_id, expired, True)
        self.assertEqual({row['status'] for row in self.repository.read_actions('alice', conversation_id)},
                         {'rejected', 'expired'})

    def test_approval_submits_once_and_never_exports_script_to_model(self):
        from common.agent_actions import decide_task

        menu_access = access('工作台-页面', '任务管理-页面')
        job = SimpleNamespace(job_id='probe', status=True, type='local', script_text='private-script',
                              script_type='Python', timeout=30, extract_names=None, notify_channels=[])
        conversation_id = self.repository.create_conversation(menu_access.user_name)
        with patch.dict(os.environ, {'SMART_AGENT_RUN_ALLOWLIST': '["probe"]'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[job]), \
                patch('common.task_center.request_run') as submit:
            submit.side_effect = lambda *arguments, **options: dict(
                status='submitted', run_id=options['run_id'], message='已接收运行请求')
            proposal = execute_tool('propose_run_task', {'job_id': 'probe'}, menu_access)['proposal']
            self.assertNotIn('private-script', str(proposal))
            submit.assert_not_called()
            turn_id, _ = self.repository.begin_turn(menu_access.user_name, conversation_id, '运行probe')
            self.repository.finish_turn(menu_access.user_name, conversation_id, turn_id, '等待审批', proposals=[proposal])
            action_id = self.repository.read_actions(menu_access.user_name, conversation_id)[0]['action_id']
            result = decide_task(menu_access.user_name, conversation_id, action_id, True, lambda: menu_access)
            self.assertEqual(result['status'], 'submitted')
            self.assertEqual(submit.call_args.args[1], 'task-probe')
            self.assertEqual(submit.call_args.kwargs['source'], 'agent')
            self.assertNotIn('private-script', str(submit.call_args))
            with self.assertRaises(AgentError):
                decide_task(menu_access.user_name, conversation_id, action_id, True, lambda: menu_access)
            submit.assert_called_once()

    def test_task_change_revocation_and_unknown_submission_do_not_retry(self):
        from common.agent_actions import decide_task, propose_task

        menu_access = access('工作台-页面', '任务管理-页面')
        job = SimpleNamespace(job_id='probe', status=True, type='local', script_text='initial',
                              script_type='Python', timeout=30, extract_names=None, notify_channels=[])
        conversation_id = self.repository.create_conversation(menu_access.user_name)
        with patch.dict(os.environ, {'SMART_AGENT_RUN_ALLOWLIST': '["probe"]'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[job]), \
                patch('common.task_center.request_run') as submit:
            for reason in ['changed', 'revoked', 'timeout', 'wrong_receipt']:
                with self.subTest(reason=reason):
                    submit.reset_mock()
                    proposal = propose_task('probe', menu_access)
                    action_id = self.repository.create_action(menu_access.user_name, conversation_id, **proposal)
                    provider = Mock(side_effect=[menu_access, access('工作台-页面')]) if reason == 'revoked' else lambda: menu_access
                    if reason == 'changed':
                        job.script_text = 'changed'
                    submit.side_effect = TimeoutError('private-connection') if reason == 'timeout' else None
                    submit.return_value = dict(status='submitted', run_id='unrelated-run-id', message='已接收')
                    result = decide_task(menu_access.user_name, conversation_id, action_id, True, provider)
                    self.assertEqual(result['status'], 'unknown' if reason in {'timeout', 'wrong_receipt'} else 'blocked')
                    self.assertNotIn('private-connection', str(result))
                    with self.assertRaises(AgentError):
                        decide_task(menu_access.user_name, conversation_id, action_id, True, lambda: menu_access)
                    self.assertEqual(submit.call_count, 1 if reason in {'timeout', 'wrong_receipt'} else 0)

    def test_execution_allowlist_fails_closed(self):
        menu_access = access('工作台-页面', '任务管理-页面')
        with patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs') as query, \
                patch('common.utilities.util_apscheduler.add_local_date_job') as submit:
            for value in ['', '{}', '[true]', '["other"]', '[]']:
                with self.subTest(value=value), patch.dict(os.environ, {'SMART_AGENT_RUN_ALLOWLIST': value}), \
                        self.assertRaises(AgentError):
                    execute_tool('propose_run_task', {'job_id': 'probe'}, menu_access)
            query.assert_not_called()
            submit.assert_not_called()

    def test_approval_callback_requires_explicit_consent_then_submits_once(self):
        from common.agent_actions import propose_task
        from dash_callback.pages import agent_widget_c as workbench_c
        from server import server

        menu_access = access('工作台-页面', '任务管理-页面')
        conversation_id = self.repository.create_conversation(menu_access.user_name)
        job = SimpleNamespace(job_id='probe', status=True, type='local', script_text='private-script',
                              script_type='Python', timeout=30, extract_names=None, notify_channels=[])
        with patch.dict(os.environ, {'SMART_AGENT_RUN_ALLOWLIST': '["probe"]'}), \
                patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[job]), \
                patch('common.task_center.request_run') as submit:
            submit.side_effect = lambda *arguments, **options: dict(
                status='submitted', run_id=options['run_id'], message='已接收运行请求')
            action_id = self.repository.create_action(menu_access.user_name, conversation_id, **propose_task('probe', menu_access))
            consent_id = {'type': 'agent-action-consent', 'action_id': action_id}
            with server.test_request_context('/'), \
                    patch('common.utilities.util_menu_access.get_menu_access', return_value=menu_access), \
                    patch.object(workbench_c, 'get_menu_access', return_value=menu_access), \
                    patch.object(workbench_c, 'ctx', SimpleNamespace(triggered_id={
                        'type': 'agent-action-decision', 'action_id': action_id, 'decision': 'approve'})):
                result = workbench_c.handle_action([1], [False], [consent_id], conversation_id, conversation_id)
                self.assertIn('核对', result[1])
                submit.assert_not_called()
                result = workbench_c.handle_action([2], [True], [consent_id], conversation_id, conversation_id)
                self.assertIn('已接收', result[1])
                workbench_c.handle_action([3], [True], [consent_id], conversation_id, conversation_id)
                submit.assert_called_once()