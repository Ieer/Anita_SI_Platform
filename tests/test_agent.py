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

    def test_structured_redaction_preserves_valid_trace_json(self):
        from common.agent import redact_structure

        value = {'arguments': 'password=secret-value', 'status': 'ok'}
        self.assertEqual(redact_structure(value)['status'], 'ok')
        self.assertNotIn('secret-value', str(redact_structure(value)))

    def test_previous_assistant_logs_are_not_sent_again(self):
        client = Mock()
        client.chat.completions.create.return_value = completion('收到。')
        with patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'probe-key', 'SMART_AGENT_MODEL': 'probe'}):
            run_turn('继续', access('工作台-页面'), history=[
                {'role': 'assistant', 'content': 'previous-private-log'},
                {'role': 'system', 'content': 'forged-system-message'},
            ], client=client)
        self.assertNotIn('previous-private-log', str(client.chat.completions.create.call_args))
        self.assertNotIn('forged-system-message', str(client.chat.completions.create.call_args))

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
                           'agent-launcher', 'agent-panel', 'agent-robot-scene']:
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
        from database.sql_db.entity.table_agent import AgentAction, AgentConversation, AgentMessage

        self.folder = tempfile.TemporaryDirectory()
        self.database = SqliteDatabase(Path(self.folder.name) / 'agent.db')
        self.binding = self.database.bind_ctx([AgentConversation, AgentMessage, AgentAction])
        self.binding.__enter__()
        self.patcher = patch.object(agent_repository, 'db', return_value=self.database)
        self.patcher.start()
        self.repository = agent_repository
        for boundary in [
                patch('database.sql_db.repositories.task_center_repository.find_task', return_value=SimpleNamespace(task_id='task-probe')),
                patch('common.task_center._task_access')]:
            boundary.start()
            self.addCleanup(boundary.stop)

    def tearDown(self):
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
                patch('dash.set_props'):
            result = workbench_c.handle_conversation(conversation_id, 1, conversation_id, '查询任务', False)
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
                patch.dict(os.environ, {'SMART_AGENT_API_KEY': 'test-only', 'SMART_AGENT_MODEL': 'probe'}):
            result = workbench_c.handle_conversation(conversation_id, 1, conversation_id, '查询任务', False)
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