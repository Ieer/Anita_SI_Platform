import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def serve_probe(port):
    sys.path.insert(0, str(ROOT / 'src'))
    import httpx
    from openai import OpenAI
    from peewee import SqliteDatabase

    from database.sql_db import conn

    with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
        database = SqliteDatabase(Path(folder) / 'probe.db', timeout=20)
        stack.callback(database.close)
        stack.enter_context(patch.object(conn, 'sqlite_db', database))
        stack.enter_context(patch.object(conn, 'db', return_value=database))
        conn.create_rds_table()
        import secrets
        conn.init_rds_data(initial_admin_password=secrets.token_urlsafe(32))
        from database.sql_db.entity.table_user import SysUser, SysUserRole

        probe_users = ['ui_alice', 'ui_bob', 'ui_theme', 'ui_config', 'ui_member', 'ui_internal_agent',
                   'ui_enterprise', 'ui_locale', 'ui_i18n']
        for owner in probe_users:
            values = dict(SysUser.get_by_id('admin').__data__)
            values.update(user_name=owner, user_full_name=owner)
            SysUser.create(**values)
            if owner != 'ui_member':
                SysUserRole.create(user_name=owner, role_name='admin')
        from database.sql_db.entity.table_user import SysRole, SysRoleAccessMeta

        role_values = dict(SysRole.get_by_id('admin').__data__)
        role_values.update(role_name='ui_i18n_access', role_remark='Locale audit permissions')
        SysRole.create(**role_values)
        for permission in (
            '团队授权-页面', '应用1-基础权限', '应用1-权限1', '应用1-权限2',
            '应用2-基础权限', '应用2-权限1', '应用2-权限2',
        ):
            SysRoleAccessMeta.create(role_name='ui_i18n_access', access_meta=permission)
        SysUserRole.create(user_name='ui_i18n', role_name='ui_i18n_access')
        gate = threading.Event()
        calls = []
        submissions = []
        internal_agent_calls = []
        enterprise_calls = []
        channel_test_calls = []

        def notification_test(**options):
            channel_test_calls.append('notify')
            return True, 'isolated test'

        def mailbox_test(**options):
            channel_test_calls.append('listen')
            return []

        for target in (
            'common.notify.server_jiang.send_notify', 'common.notify.gewechat.send_notify',
            'common.notify.enterprise_wechat.wechat_text', 'common.notify.email_smtp.send_mail',
        ):
            stack.enter_context(patch(target, side_effect=notification_test))
        stack.enter_context(patch('common.listen.email_pop3.get_email_context_from_subject_during',
                                  side_effect=mailbox_test))

        def transport(request):
            payload = json.loads(request.content)
            prompt = next(message['content'] for message in reversed(payload['messages']) if message['role'] == 'user')
            if prompt != '请只回复 OK。':
                calls.append(payload)
            if 'wait' in prompt:
                if not gate.wait(30):
                    raise httpx.ReadTimeout('test gate expired')
            if 'proposal' in prompt and payload['messages'][-1]['role'] != 'tool':
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'ui_proposal', 'type': 'function',
                    'function': {'name': 'propose_run_task', 'arguments': '{"job_id":"ui_job"}'},
                }]}
            elif '整理任务概览' in prompt:
                message = {'role': 'assistant', 'content': (
                    '### 任务概览\n\n以下为**隔离测试数据**，不代表真实任务状态。\n\n'
                    '| 任务 | 状态 |\n| --- | --- |\n| 数据同步 | 已完成 |\n| 日志归档 | 待确认 |\n\n'
                    '建议先核对日志归档，再确认是否运行：\n\n'
                    '1. 检查任务版本与运行记录。\n2. 核对脚本及外部影响。\n\n'
                    '```python\nstatus = {"job_id": "ui_job", "state": "pending", "source": "isolated_browser_test"}\n```\n\n'
                    '<script>window.agentUnsafeHtml = true</script>'
                )}
            else:
                message = {'role': 'assistant', 'content': 'UI TEST REPLY: ' + prompt + '\n' + 'Long response. ' * 40}
            if payload.get('stream'):
                delta = {'role': 'assistant', 'content': message.get('content')}
                if message.get('tool_calls'):
                    delta['tool_calls'] = [
                        {'index': index, **call}
                        for index, call in enumerate(message['tool_calls'])
                    ]
                chunk = {
                    'id': 'ui_completion', 'object': 'chat.completion.chunk', 'created': 1,
                    'model': payload['model'],
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': 'stop'}],
                }
                return httpx.Response(
                    200, content=f'data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n',
                    headers={'Content-Type': 'text/event-stream'},
                )
            return httpx.Response(200, json={
                'id': 'ui_completion', 'object': 'chat.completion', 'created': 1, 'model': payload['model'],
                'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
            })

        stack.enter_context(patch.dict(os.environ, {
            'SMART_AGENT_API_KEY': 'ui-test-only', 'SMART_AGENT_MODEL': 'ui-test-model',
            'SMART_AGENT_BASE_URL': 'https://ui-test.invalid/v1', 'SMART_AGENT_RUN_ALLOWLIST': '["ui_job"]',
        }))
        stack.enter_context(patch('openai.OpenAI', side_effect=lambda **options: OpenAI(
            **options, http_client=httpx.Client(transport=httpx.MockTransport(transport)))))
        job = SimpleNamespace(job_id='ui_job', status=True, type='local', script_text='test-only-script',
                              script_type='Python', timeout=30, extract_names=None, notify_channels=[],
                              trigger='interval', plan={'seconds': 60, 'timezone': 'Asia/Shanghai'})
        stack.enter_context(patch('common.utilities.util_apscheduler.get_apscheduler_all_jobs', return_value=[job]))
        from datetime import datetime
        from common import task_center
        from database.sql_db.repositories import task_center_repository
        from database.sql_db.entity.table_user import SysGroup

        SysGroup.create(group_name='ui-team', group_status=True, group_remark='',
                        create_by='admin', update_by='admin', create_datetime=datetime.now(), update_datetime=datetime.now())
        payload = dict(type=job.type, script_text=job.script_text, script_type=job.script_type,
                       timeout=job.timeout, extract_names=None, notify_channels=[], env_vars=None)
        stack.enter_context(patch.object(task_center, '_platform_job_rows', return_value=[
            dict(id=job.job_id, status=True, trigger=job.trigger, plan=job.plan, kwargs=payload)]))
        task_center_repository.create_task('ui-task', 'platform', job.job_id, 'ui-team',
                                    digest=task_center._digest(payload), enabled=True)

        def submit(trigger, trigger_args, job_kwargs, job_id, **options):
            submissions.append({'job_id': job_id, 'task_id': job_kwargs['task_id']})
            return job_id

        stack.enter_context(patch('common.utilities.util_apscheduler._add_job', side_effect=submit))
        import app as platform_app
        from common.internal_agent_provider import InternalAgentProvider, _tokens
        from common.agent_providers import _PROVIDER_FACTORIES

        def internal_agent_transport(request):
            if request.url.host == 'login.microsoftonline.com':
                return httpx.Response(200, json={
                    'access_token': 'browser-internal_agent-token', 'token_type': 'Bearer', 'expires_in': 3600,
                })
            payload = json.loads(request.content)
            internal_agent_calls.append(payload)
            text = payload['input'][0]['content'][0]['text']
            return httpx.Response(200, json={
                'response': {'status': 'SUCCESS', 'model': 'browser-internal_agent-agent', 'output': [{
                    'role': 'assistant', 'type': 'message',
                    'content': [{'type': 'output_text', 'text': 'INTERNAL_AGENT REPLY: ' + text}],
                }]},
                'messages': [*payload['history'], {'role': 'user', 'content': text}],
            })

        _tokens.clear()
        internal_agent_client = stack.enter_context(httpx.Client(transport=httpx.MockTransport(internal_agent_transport)))
        stack.enter_context(patch.dict(_PROVIDER_FACTORIES, {
            'internal_agent': lambda settings, client=None: InternalAgentProvider(settings, internal_agent_client),
        }))
        from datetime import timedelta, timezone
        from common.enterprise_models_provider import EnterpriseModelsProvider
        from common.enterprise_models_credentials import PREFIX

        token_file = Path(folder) / 'enterprise-token.dat'
        token_file.write_text(json.dumps({
            'access_token': 'browser-enterprise-fake-access', 'refresh_token': 'browser-unused-refresh',
            'token_expiry': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }), encoding='utf-8')
        stack.enter_context(patch.dict(os.environ, {
            PREFIX + 'TOKEN_FILE': str(token_file), PREFIX + 'TOKEN_TIMEZONE': 'UTC',
            PREFIX + 'OWNER': 'ui_enterprise',
            PREFIX + 'ENDPOINT': 'https://models.test.invalid/v1/chat/stream',
        }))

        def enterprise_transport(request):
            payload = json.loads(request.content)
            enterprise_calls.append(payload)
            text = payload['prompt'].rsplit('\n\n', 1)[-1]
            event = {'chatId': 'browser-chat', 'current': [{
                'id': 'browser-parent', 'content': 'ENTERPRISE REPLY: ' + text,
            }]}
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  text=f'data: {json.dumps(event)}\n\ndata: [DONE]\n\n')

        enterprise_client = stack.enter_context(httpx.Client(transport=httpx.MockTransport(enterprise_transport)))
        stack.enter_context(patch.dict(_PROVIDER_FACTORIES, {
            'enterprise_models': lambda settings, client=None: EnterpriseModelsProvider(settings, enterprise_client),
        }))
        from flask import jsonify, redirect, make_response
        from common.utilities.util_jwt import jwt_encode
        from database.sql_db.repositories import agent_repository

        server = platform_app.server

        @server.get('/_ui/login/<owner>')
        def probe_login(owner):
            if owner not in probe_users:
                return '', 404
            response = make_response(redirect('/dashboard_/monitor'))
            response.set_cookie('Authorization', 'Bearer ' + jwt_encode({'user_name': owner, 'token_use': 'session'}),
                                httponly=True, samesite='Lax')
            return response

        @server.get('/_ui/stats')
        def probe_stats():
            return jsonify(calls=sum(call['model'] == 'ui-test-model' for call in calls),
                           models=[call['model'] for call in calls], submissions=len(submissions),
                           internal_agent_history_lengths=[len(call['history']) for call in internal_agent_calls],
                           enterprise_parents=[call.get('parentId') for call in enterprise_calls],
                           channel_test_calls=channel_test_calls,
                           conversations=len(agent_repository.list_conversations('ui_alice')))

        @server.post('/_ui/release')
        def probe_release():
            gate.set()
            return '', 204

        from werkzeug.serving import make_server

        http_server = make_server('127.0.0.1', port, server, threaded=True)

        @server.after_request
        def close_probe_database(response):
            database.close()
            return response

        @server.post('/_ui/shutdown')
        def shutdown_probe():
            threading.Thread(target=http_server.shutdown, daemon=True).start()
            return '', 204

        print(f'UI_PROBE_URL=http://127.0.0.1:{http_server.server_port}', flush=True)
        try:
            http_server.serve_forever()
        finally:
            http_server.server_close()
            database.close()


@unittest.skipUnless(os.environ.get('PLATFORM_AGENT_UI_PROBE') == '1', 'opt-in browser integration')
class AgentBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = tempfile.TemporaryFile(mode='w+')
        cls.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve', '--port', '0'],
                                       stdout=subprocess.PIPE, stderr=cls.log, text=True, encoding='utf-8',
                                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        for line in cls.process.stdout:
            if line.startswith('UI_PROBE_URL='):
                cls.url = line.strip().split('=', 1)[1]
                break
        else:
            cls.log.seek(0)
            raise RuntimeError(cls.log.read())

    @classmethod
    def tearDownClass(cls):
        from urllib.request import Request, urlopen

        try:
            with urlopen(Request(cls.url + '/_ui/shutdown', method='POST'), timeout=5):
                pass
            cls.process.wait(timeout=10)
        finally:
            if cls.process.poll() is None:
                cls.process.terminate()
                cls.process.wait(timeout=10)
        cls.log.close()

    def test_appearance_modes_palettes_contrast_and_persistence(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, color_scheme='light')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_theme')
            expect(page.locator('.monitor-metric')).to_have_count(4, timeout=60000)
            page.wait_for_function("document.querySelector('#monitor-trend .js-plotly-plot')?.data?.length === 2")
            page.locator('#monitor-pause').click()
            expect(page.locator('#monitor-updated')).to_contain_text('自动刷新已暂停')
            baseline = page.evaluate("document.querySelector('#monitor-trend .js-plotly-plot').data.map(trace => trace.y)")
            timestamp = page.locator('#monitor-updated').inner_text()
            panel = page.locator('#platform-appearance')
            panel.locator('summary').click()
            expect(panel.locator('summary svg')).to_have_count(1)
            switch = panel.get_by_role('switch')
            for mode in ('light', 'dark'):
                for palette in ('teal', 'blue', 'rose'):
                    for contrast in ('normal', 'high'):
                        panel.locator(f'[data-theme-mode="{mode}"]').click()
                        panel.locator(f'[data-theme-palette="{palette}"]').click()
                        if switch.get_attribute('aria-checked') != str(contrast == 'high').lower():
                            switch.click()
                        page.wait_for_function('''expected => {
                            const state = window.platformTheme.state();
                            const spec = window.platformTheme.provider(state);
                            const graph = document.querySelector('#monitor-trend .js-plotly-plot');
                            return state.mode === expected.mode && state.palette === expected.palette &&
                                state.contrast === expected.contrast && graph?.layout?.paper_bgcolor === spec.colors.surface &&
                                graph.data[0].line.color === spec.colors.accent;
                        }''', arg={'mode': mode, 'palette': palette, 'contrast': contrast})
                        expect(page.locator('.platform-brand-wordmark')).to_have_css(
                            'filter', 'brightness(1.4)' if mode == 'dark' else 'brightness(0.5)')
                        grid = page.locator('body').evaluate('''element => {
                            const style = getComputedStyle(element, '::before');
                            return {content: style.content, image: style.backgroundImage,
                                pointerEvents: style.pointerEvents, animation: style.animationName};
                        }''')
                        if mode == 'dark' and contrast == 'normal':
                            expect(page.locator('body')).to_have_css('background-color', 'rgb(21, 24, 25)')
                            self.assertIn('conic-gradient', grid['image'])
                            self.assertIn('192, 201, 205', grid['image'])
                            expect(page.locator('#menu-collapse-sider')).to_have_css('border-top-color', 'rgb(57, 67, 70)')
                            self.assertEqual(grid['pointerEvents'], 'none')
                            self.assertEqual(grid['animation'], 'none')
                        else:
                            self.assertEqual(grid['content'], 'none')
                        self.assertEqual(baseline, page.evaluate(
                            "document.querySelector('#monitor-trend .js-plotly-plot').data.map(trace => trace.y)"))
                        expect(page.locator('#monitor-updated')).to_have_text(timestamp)
                        self.assertTrue(page.locator('.monitor-metric').first.evaluate('''element => {
                            const canvas = document.createElement('canvas').getContext('2d');
                            canvas.fillStyle = window.platformTheme.provider().colors.surface;
                            const normalized = canvas.fillStyle;
                            canvas.fillStyle = getComputedStyle(element).backgroundColor;
                            return canvas.fillStyle === normalized;
                        }'''))
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in [(1440, 900), (1366, 768), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                self.assertTrue(panel.locator('.platform-appearance-panel').evaluate(
                    'element => { const box = element.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth; }'))
                self.assertTrue(page.locator('.platform-content-column').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                page.screenshot(path=str(report / f'appearance-dark-high-{width}.png'))
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.keyboard.press('Escape')
            expect(panel).not_to_have_attribute('open', '')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            expect(page.locator('#agent-prompt')).to_be_enabled(timeout=15000)
            page.locator('#agent-prompt').fill('Appearance test draft')
            panel.locator('summary').click()
            panel.locator('[data-theme-mode="light"]').click()
            expect(page.locator('#agent-prompt')).to_have_value('Appearance test draft')
            page.keyboard.press('Escape')
            page.locator('#agent-close').click()
            page.goto(self.url + '/task_/overview')
            page.wait_for_function("document.querySelector('#tc-overview-chart .js-plotly-plot')?.data?.length > 0")
            page.locator('.modebar-btn[data-attr="zoom"][data-val="in"]').click()
            zoom = page.evaluate("document.querySelector('#tc-overview-chart .js-plotly-plot')._fullLayout.xaxis.range")
            page.locator('#platform-appearance summary').click()
            page.locator('[data-theme-mode="dark"]').click()
            page.wait_for_function("document.querySelector('#tc-overview-chart .js-plotly-plot')?.layout.paper_bgcolor === '#080808'")
            self.assertEqual(zoom, page.evaluate(
                "document.querySelector('#tc-overview-chart .js-plotly-plot')._fullLayout.xaxis.range"))
            page.keyboard.press('Escape')
            page.goto(self.url + '/person_/personal_info')
            expect(page.locator('#personal-center-tabs')).to_be_visible(timeout=15000)
            page.reload()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            expect(page.locator('html')).to_have_attribute('data-palette', 'rose')
            expect(page.locator('html')).to_have_attribute('data-contrast', 'high')
            page.locator('#platform-appearance summary').click()
            page.locator('[data-theme-mode="system"]').click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            page.emulate_media(color_scheme='dark')
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            expect(page.locator('.platform-brand-wordmark')).to_have_css('filter', 'brightness(1.4)')
            expect(page.locator('[data-theme-mode="system"]')).to_have_attribute('aria-pressed', 'true')
            peer = context.new_page()
            peer.goto(self.url + '/dashboard_/monitor')
            expect(peer.locator('#platform-appearance')).to_be_visible(timeout=15000)
            peer.locator('#platform-appearance summary').click()
            peer.locator('[data-theme-mode="light"]').click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            peer.locator('[data-theme-palette="blue"]').click()
            expect(page.locator('html')).to_have_attribute('data-palette', 'blue')
            page.emulate_media(forced_colors='active')
            self.assertTrue(page.evaluate("matchMedia('(forced-colors: active)').matches"))
            self.assertEqual(page.locator('[data-theme-mode="light"]').evaluate(
                'element => getComputedStyle(element).outlineStyle'), 'solid')
            page.screenshot(path=str(report / 'appearance-forced-colors.png'))
            page.emulate_media(forced_colors='none')
            peer.evaluate("localStorage.setItem('si-platform.appearance.v1', '{invalid')")
            expect(page.locator('html')).to_have_attribute('data-palette', 'teal')
            expect(page.locator('html')).to_have_attribute('data-contrast', 'normal')
            context.clear_cookies()
            page.goto(self.url + '/login')
            expect(page.locator('.platform-login')).to_be_visible(timeout=15000)
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            self.assertEqual(page.locator('.platform-login').evaluate(
                'element => getComputedStyle(element).backgroundColor'), 'rgb(17, 25, 26)')
            self.assertEqual(page.evaluate('window.platformTheme.provider().colors.canvas'), '#11191a')
            self.assertEqual(page.evaluate('window.platformTheme.provider().colors.surface'), '#1b2024')
            self.assertEqual(page.evaluate("JSON.parse(JSON.stringify(window.platformTheme.state())).mode"), 'system')
            self.assertFalse(errors, errors)
            context.close()
            blocked = browser.new_context()
            blocked.add_init_script("""(() => {
                const read = Storage.prototype.getItem;
                const write = Storage.prototype.setItem;
                Storage.prototype.getItem = function(key) {
                    if (key === 'si-platform.appearance.v1') throw new Error('blocked theme storage');
                    return read.call(this, key);
                };
                Storage.prototype.setItem = function(key, value) {
                    if (key === 'si-platform.appearance.v1') throw new Error('blocked theme storage');
                    return write.call(this, key, value);
                };
            })();""")
            page = blocked.new_page()
            page.goto(self.url + '/_ui/login/ui_bob')
            expect(page.locator('#platform-appearance')).to_be_visible(timeout=15000)
            page.locator('#platform-appearance summary').click()
            page.locator('[data-theme-mode="dark"]').click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            blocked.close()
            browser.close()

    def test_theme_portals_and_initial_loader(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900})
            context.add_init_script("localStorage.setItem('si-platform.appearance.v1', JSON.stringify({mode:'dark',palette:'blue',contrast:'normal'}))")
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_alice')
            expect(page.locator('#platform-appearance')).to_be_visible(timeout=60000)
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in ((1440, 900), (1366, 768), (390, 844), (320, 700)):
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function('document.documentElement.scrollWidth <= innerWidth')
                self.assertLessEqual(page.locator('body').evaluate(
                    "element => getComputedStyle(element, '::before').height.replace('px', '') * 1"), height)
                page.screenshot(path=str(report / f'appearance-perspective-grid-{width}.png'))
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.emulate_media(forced_colors='active')
            self.assertEqual(page.locator('body').evaluate(
                "element => getComputedStyle(element, '::before').display"), 'none')
            page.emulate_media(forced_colors='none')
            for route, selector in (
                ('/task_/overview', '.tc-overview-page'),
                ('/task_/task_mgmt', '.tc-platform-page'),
                ('/task_/windows_scheduler', '.platform-shell'),
                ('/task_/task_log', '.platform-shell'),
                ('/access_/role_mgmt', '#role-mgmt-button-add'),
                ('/access_/user_mgmt', '.platform-shell'),
                ('/access_/group_mgmt', '.platform-shell'),
                ('/access_/group_auth', '.platform-shell'),
                ('/setting_/notify_api', '.channel-settings-page'),
                ('/setting_/listen_api', '.channel-settings-page'),
                ('/setting_/agent_provider', '.agent-config-page'),
                ('/message_/announcement', '.platform-shell'),
                ('/person_/personal_info', '#personal-center-tabs'),
                ('/dashboard_/monitor?independent_', '.monitor-metric'),
            ):
                with self.subTest(route=route):
                    page.goto(self.url + route)
                    expect(page.locator(selector).first).to_be_visible(timeout=30000)
                    expect(page.locator('body')).to_have_css('background-color', 'rgb(21, 24, 25)')
                    self.assertIn('conic-gradient', page.locator('body').evaluate(
                        "element => getComputedStyle(element, '::before').backgroundImage"))
                    if 'independent_' in route:
                        expect(page.locator('.platform-shell')).to_have_count(0)
                    if route.startswith('/access_/') or route == '/message_/announcement':
                        table = page.locator('.ant-tabs-tabpane-active .ant-table-content').first
                        expect(table).to_be_visible(timeout=15000)
                        for width, height in ((1440, 900), (390, 844), (320, 700)):
                            page.set_viewport_size({'width': width, 'height': height})
                            expect(table).to_have_css('scrollbar-width', 'thin')
                            expect(table).to_have_css('scrollbar-color', 'rgb(127, 144, 152) rgb(29, 34, 36)')
                            scrollers = page.locator('.ant-tabs-tabpane-active .ant-table-wrapper').evaluate_all('''tables => {
                                const nodes = tables.flatMap(table => [...table.querySelectorAll(
                                    '.ant-table-container, .ant-table-content, .ant-table-body')]);
                                return nodes.filter(node => node.scrollWidth > node.clientWidth + 1 &&
                                    ['auto', 'scroll'].includes(getComputedStyle(node).overflowX)).map(node => {
                                    node.scrollLeft = Math.min(60, node.scrollWidth - node.clientWidth);
                                    const moved = node.scrollLeft > 0;
                                    node.scrollLeft = 0;
                                    return moved;
                                });
                            }''')
                            if route == '/access_/user_mgmt' and width <= 390:
                                self.assertTrue(scrollers, (route, width, 'horizontal scroll container required'))
                            self.assertTrue(all(scrollers), (route, width, scrollers))
                            page.screenshot(path=str(report / f'appearance-scrollbar-{route.rsplit("/", 1)[-1]}-{width}.png'))
                        page.set_viewport_size({'width': 1440, 'height': 900})
                        for changes in ({'contrast': 'high'}, {'mode': 'light', 'contrast': 'normal'}):
                            page.evaluate('value => window.platformTheme.update(value)', changes)
                            expect(table).to_have_css('scrollbar-width', 'auto')
                        page.evaluate('value => window.platformTheme.update(value)', {'mode': 'dark', 'contrast': 'normal'})
                        page.emulate_media(forced_colors='active')
                        expect(table).to_have_css('scrollbar-color', 'auto')
                        page.emulate_media(forced_colors='none')
                    if route == '/task_/task_log':
                        for width, height in ((1440, 900), (1366, 768), (390, 844), (320, 700)):
                            page.set_viewport_size({'width': width, 'height': height})
                            expect(page.locator('.tc-page .ant-input-affix-wrapper').first).to_have_css(
                                'border-top-color', 'rgb(127, 144, 152)')
                            expect(page.locator('#tc-log-table .ant-table-thead th').first).to_have_css('font-size', '14px')
                            expect(page.locator('#tc-log-table .ant-table-tbody td').first).to_have_css('font-size', '14px')
                            expect(page.locator('#tc-log-page .tc-eyebrow')).to_have_css('font-size', '12px')
                            expect(page.locator('#tc-log-page .ant-pagination')).to_have_css('font-size', '14px')
                            self.assertTrue(page.locator('.tc-toolbar').evaluate(
                                'element => element.scrollWidth <= element.clientWidth'))
                            self.assertTrue(page.locator('.platform-content-column').evaluate(
                                'element => element.scrollWidth <= element.clientWidth'))
                            grid_left = page.locator('body').evaluate(
                                "element => parseFloat(getComputedStyle(element, '::before').left)")
                            content_left = page.locator('.platform-content-column').evaluate(
                                'element => element.getBoundingClientRect().left')
                            self.assertAlmostEqual(grid_left, content_left, delta=1)
                            for selector in ('.ant-table-container', '.ant-table-content'):
                                expect(page.locator('#tc-log-table ' + selector)).to_have_css('scrollbar-width', 'thin')
                            page.screenshot(path=str(report / f'appearance-neutral-log-{width}.png'))
                        page.set_viewport_size({'width': 1440, 'height': 900})
                        if 'ant-layout-sider-collapsed' in page.locator('#menu-collapse-sider').get_attribute('class'):
                            page.locator('#btn-menu-collapse-sider-menu').click()
                            expect(page.locator('#menu-collapse-sider')).not_to_have_class(re.compile('ant-layout-sider-collapsed'))
                        page.locator('#btn-menu-collapse-sider-menu').click()
                        expect(page.locator('#menu-collapse-sider')).to_have_class(re.compile('ant-layout-sider-collapsed'))
                        page.wait_for_function('''() => {
                            const gridLeft = parseFloat(getComputedStyle(document.body, '::before').left);
                            const contentLeft = document.querySelector('.platform-content-column').getBoundingClientRect().left;
                            return Math.abs(gridLeft - contentLeft) < 1;
                        }''')
                        page.screenshot(path=str(report / 'appearance-neutral-log-collapsed.png'))
                        page.locator('#btn-menu-collapse-sider-menu').click()
                    if route == '/access_/role_mgmt':
                        from io import BytesIO
                        from PIL import Image, ImageChops

                        page.screenshot(path=str(report / 'appearance-perspective-grid-management.png'))
                        corner = {'x': 0, 'y': 880, 'width': 1440, 'height': 20}
                        visible = Image.open(BytesIO(page.screenshot(clip=corner))).convert('RGB')
                        hidden = page.add_style_tag(content='body::before { display: none !important; }')
                        plain = Image.open(BytesIO(page.screenshot(clip=corner))).convert('RGB')
                        hidden.evaluate('element => element.remove()')
                        self.assertIsNotNone(ImageChops.difference(visible, plain).getbbox())
            page.goto(self.url + '/missing-background-probe')
            expect(page.locator('html')).to_have_attribute('data-theme-ready', 'true')
            expect(page.locator('.platform-shell')).to_have_count(0)
            expect(page.locator('body')).to_have_css('background-color', 'rgb(21, 24, 25)')
            self.assertIn('conic-gradient', page.locator('body').evaluate(
                "element => getComputedStyle(element, '::before').backgroundImage"))
            page.screenshot(path=str(report / 'appearance-perspective-grid-error.png'))
            page.goto(self.url + '/dashboard_/monitor')
            expect(page.locator('.monitor-metric')).to_have_count(4, timeout=30000)
            page.evaluate("""() => {
                window.dash_clientside.set_props('global-message-container', {children: {
                    namespace: 'feffery_antd_components', type: 'AntdMessage',
                    props: {id: 'theme-message-probe', content: 'Theme probe notice', type: 'info', duration: 0}
                }});
                window.dash_clientside.set_props('global-notification-container', {children: {
                    namespace: 'feffery_antd_components', type: 'AntdNotification',
                    props: {id: 'theme-notification-probe', message: 'Theme notification', description: 'Isolated probe', duration: 0, placement: 'bottomLeft'}
                }});
            }""")
            expect(page.locator('.ant-message-notice-content')).to_be_visible()
            expect(page.locator('.ant-notification-notice')).to_be_visible()
            for selector in ('.ant-message-notice-content', '.ant-notification-notice-wrapper'):
                self.assertEqual(page.locator(selector).evaluate(
                    'element => getComputedStyle(element).backgroundColor'), 'rgb(39, 45, 48)')
            page.locator('#platform-appearance summary').click()
            page.locator('[data-theme-mode="light"]').click()
            for selector in ('.ant-message-notice-content', '.ant-notification-notice-wrapper'):
                expect(page.locator(selector)).to_have_css('background-color', 'rgb(255, 255, 255)')
            page.keyboard.press('Escape')
            page.goto(self.url + '/task_/task_mgmt')
            expect(page.locator('.tc-platform-page')).to_be_visible(timeout=15000)
            page.locator('#platform-appearance summary').click()
            page.locator('[data-theme-mode="dark"]').click()
            page.keyboard.press('Escape')
            page.get_by_text('新建任务', exact=True).click()
            expect(page.locator('.ant-modal-content:visible')).to_be_visible()
            expect(page.locator('.ant-modal-content:visible')).to_have_css('background-color', 'rgb(39, 45, 48)')
            page.screenshot(path=str(ROOT / 'cache/report/ui/appearance-dark-modal.png'))
            self.assertFalse(errors, errors)
            loader = context.new_page()
            loader.route('**/_dash-component-suites/**', lambda route: route.abort())
            loader.goto(self.url + '/dashboard_/monitor', wait_until='domcontentloaded')
            expect(loader.locator('._dash-loading')).to_be_visible()
            expect(loader.locator('html')).to_have_attribute('data-theme', 'dark')
            expect(loader.locator('._dash-loading')).to_have_css('background-color', 'rgb(39, 45, 48)')
            loader.screenshot(path=str(ROOT / 'cache/report/ui/appearance-dark-loader.png'), timeout=5000)
            context.close()
            browser.close()

    def test_monitor_responsive_layout_and_refresh(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_alice')
            expect(page.locator('.monitor-metric')).to_have_count(4, timeout=60000)
            expect(page.locator('#monitor-updated')).to_contain_text('每5秒刷新')
            expect(page.locator('#monitor-disk-desc .ant-progress').first).to_be_visible()
            expect(page.locator('#monitor-disk-desc .ant-progress-text').first).to_contain_text('%')
            for row in page.locator('#monitor-disk-desc tbody tr').all():
                progress = row.locator('.ant-progress-text')
                if progress.count():
                    text = progress.inner_text()
                    value = float(text.rstrip('%'))
                    self.assertGreaterEqual(value, 0)
                    self.assertLessEqual(value, 100)
                    if '正常' in row.inner_text():
                        self.assertLess(value, 80)
                    if '注意容量' in row.inner_text():
                        self.assertGreaterEqual(value, 80)
                        self.assertLess(value, 90)
            expect(page.locator('#system-monitor')).to_contain_text('可用内存')
            expect(page.locator('#system-monitor')).to_contain_text('线程数')
            page.wait_for_function('''() => {
                const graph = document.querySelector('#monitor-trend .js-plotly-plot');
                return graph && graph.data && graph.data[0].x.length >= 2;
            }''', timeout=15000)
            page.locator('#monitor-pause').click()
            expect(page.locator('#monitor-pause')).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('#monitor-updated')).to_contain_text('自动刷新已暂停')
            timestamp = page.locator('#monitor-updated').inner_text()
            start = page.evaluate('performance.now()')
            page.wait_for_function('start => performance.now() - start > 5500', arg=start)
            expect(page.locator('#monitor-updated')).to_have_text(timestamp)
            page.locator('#monitor-refresh').click()
            expect(page.locator('#monitor-updated')).not_to_have_text(timestamp)
            expect(page.locator('#monitor-updated')).to_contain_text('自动刷新已暂停')
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in [(1440, 900), (1366, 768), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function('''width => {
                    const graph = document.querySelector('#monitor-trend .js-plotly-plot');
                    const svg = graph && graph.querySelector('.svg-container');
                    return graph && svg && graph.getBoundingClientRect().width <= width
                        && svg.getBoundingClientRect().width <= graph.clientWidth + 1;
                }''', arg=width)
                page.locator('.monitor-heading').scroll_into_view_if_needed()
                page.screenshot(path=str(report / f'system-monitor-{width}.png'))
                self.assertTrue(page.locator('#system-monitor').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'),
                    page.locator('#system-monitor').evaluate('''element => ({
                        viewport: innerWidth, width: element.clientWidth, scroll: element.scrollWidth,
                        overflow: [...element.querySelectorAll('*')].filter(child =>
                            !child.closest('.monitor-table-scroll') && child.getBoundingClientRect().right >
                            element.getBoundingClientRect().right + 1).map(child => child.className).slice(0, 12)
                    })'''))
                self.assertTrue(page.locator('.platform-content-column').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                for tile in page.locator('.monitor-metric').all():
                    self.assertTrue(tile.evaluate('element => element.scrollWidth <= element.clientWidth'))
                self.assertEqual(page.locator('#monitor-refresh svg').count(), 1)
                if width <= 600:
                    self.assertTrue(page.locator('.monitor-table-scroll').evaluate(
                        'element => element.scrollWidth > element.clientWidth'))
                page.locator('.monitor-details').scroll_into_view_if_needed()
                page.screenshot(path=str(report / f'system-monitor-details-{width}.png'))
            page.locator('#monitor-pause').click()
            expect(page.locator('#monitor-pause')).to_have_attribute('aria-pressed', 'false')
            expect(page.locator('#monitor-updated')).to_contain_text('每5秒刷新')
            self.assertEqual(errors, [])
            context.close()
            browser.close()

    def test_personal_center_profile_editor_and_responsive_security(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
            page = context.new_page()
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_alice')
            page.wait_for_selector('#main-head-user-menu .ant-dropdown-trigger', timeout=30000)
            self.assertNotIn('用户画像', page.locator('#platform-navigation').inner_text())
            page.locator('#main-head-user-menu .ant-dropdown-trigger').click()
            page.get_by_text('个人中心', exact=True).click()
            page.wait_for_url('**/person_/personal_info**')
            expect(page.locator('#personal-center-tabs')).to_contain_text('基本资料')
            expect(page.locator('#personal-center-tabs')).to_contain_text('账户安全')

            full_name = page.locator('#personal-info-user-full-name')
            page.locator('#personal-info-user-full-name-edit').click()
            expect(full_name).to_be_editable()
            full_name.fill('Updated UI Alice')
            page.locator('#personal-info-user-full-name-save').click()
            expect(page.locator('#personal-info-display-name')).to_have_text('Updated UI Alice')

            page.locator('#personal-info-user-full-name-edit').click()
            full_name.fill('Canceled UI Change')
            page.locator('#personal-info-user-full-name-cancel').click()
            expect(full_name).to_have_value('Updated UI Alice')
            page.reload()
            expect(page.locator('#personal-info-display-name')).to_have_text('Updated UI Alice', timeout=30000)

            page.locator('#personal-center-tabs .ant-tabs-tab:nth-child(2) .ant-tabs-tab-btn').click()
            expect(page.locator('#personal-center-tabs')).to_contain_text('动态码验证')
            expect(page.get_by_role('button', name='修改密码')).to_be_visible()
            expect(page.get_by_role('button', name='绑定动态码')).to_be_visible()
            self.assertEqual(page.locator('.ant-qrcode').count(), 0)

            page.locator('#global-locale-switch').click()
            expect(page.locator('#personal-center-tabs')).to_contain_text('Account Security', timeout=30000)
            page.locator('#personal-center-tabs .ant-tabs-tab:nth-child(2) .ant-tabs-tab-btn').click()
            expect(page.locator('#personal-center-tabs')).to_contain_text('Two-factor authentication')
            self.assertIn('dash-i18n=en-us', [cookie['name'] + '=' + cookie['value'] for cookie in context.cookies()])
            page.locator('#personal-center-tabs .ant-tabs-tab:first-child .ant-tabs-tab-btn').click()

            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in [(1440, 900), (1366, 768), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                self.assertTrue(page.locator('.personal-center').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                self.assertTrue(page.locator('.personal-center-fields').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                page.screenshot(path=str(report / f'personal-center-{width}.png'))
            self.assertEqual(page_errors, [])
            context.close()
            browser.close()

    def test_agent_provider_profiles_have_a_tested_admin_setup_flow(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_config')
            page.goto(self.url + '/setting_/agent_provider')
            expect(page.locator('.agent-config-page')).to_be_visible(timeout=30000)
            expect(page.locator('.agent-config-grid')).to_be_visible()
            self.assertTrue(page.locator('.agent-config-form-card').evaluate('''element => {
                const fields = [...element.querySelectorAll('.agent-config-field')]
                    .filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect());
                const [name, provider, ...fullWidth] = fields;
                return fields.length === 5 && Math.abs(name.top - provider.top) < 1 &&
                    Math.abs(name.width - provider.width) < 1 && provider.left > name.right &&
                    fullWidth.every((field, i) => Math.abs(field.left - name.left) < 1 &&
                        Math.abs(field.right - provider.right) < 1 &&
                        field.top >= (i === 0 ? Math.max(name.bottom, provider.bottom) : fullWidth[i - 1].bottom));
            }'''))

            page.locator('#agent-provider-add').click()
            page.locator('#agent-provider-name').fill('比赛接入 A')
            page.locator('#agent-provider-url').fill('https://ui-test.invalid/v1')
            page.locator('#agent-provider-model').fill('browser-model-a')
            page.locator('#agent-provider-key').fill('browser-secret-profile-a')
            page.locator('#agent-provider-test').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('连接成功', timeout=30000)
            page.locator('#agent-provider-save').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('加密保存', timeout=30000)
            expect(page.locator('#agent-provider-key')).to_have_value('')
            self.assertNotIn('browser-secret-profile-a', page.locator('body').inner_text())

            page.locator('#agent-provider-add').click()
            page.locator('#agent-provider-name').fill('比赛接入 B')
            page.locator('#agent-provider-url').fill('https://ui-test.invalid/v1')
            page.locator('#agent-provider-model').fill('browser-model-b')
            page.locator('#agent-provider-key').fill('browser-secret-profile-b')
            page.locator('#agent-provider-save').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('加密保存', timeout=30000)

            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('.agent-config-notice').scroll_into_view_if_needed()
            self.assertTrue(page.locator('.agent-config-page').evaluate(
                'element => element.scrollWidth <= element.clientWidth'))
            page.screenshot(path=str(report / 'agent-configuration-390.png'))
            page.set_viewport_size({'width': 320, 'height': 700})
            page.locator('.agent-config-notice').scroll_into_view_if_needed()
            self.assertTrue(page.locator('.agent-config-page').evaluate(
                'element => element.scrollWidth <= element.clientWidth'))
            page.screenshot(path=str(report / 'agent-configuration-320.png'))

            page.set_viewport_size({'width': 1440, 'height': 900})
            page.locator('.agent-config-notice').scroll_into_view_if_needed()
            page.screenshot(path=str(report / 'agent-configuration-1440.png'))
            page.goto(self.url + '/dashboard_/monitor')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            for label, model in [('比赛接入 A', 'browser-model-a'), ('比赛接入 B', 'browser-model-b')]:
                page.locator('.ant-select').filter(has=page.locator('#agent-provider-profile')).locator(
                    '.ant-select-selector').click()
                page.locator('.ant-select-item-option').filter(has_text=label).click()
                expect(page.locator('#agent-conversation-profile-feedback')).to_contain_text(model)
                expect(page.locator('#agent-model-status')).to_have_text(model)
                page.locator('#agent-prompt').fill('Verify ' + model)
                page.locator('#agent-send').click()
                expect(page.locator('#agent-messages')).to_contain_text('UI TEST REPLY: Verify ' + model,
                                                                      timeout=30000)
                expect(page.locator('#agent-prompt')).to_be_enabled()
                self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['models'][-1], model)
            page.reload()
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-model-status')).to_have_text('browser-model-b', timeout=30000)
            expect(page.locator('#agent-messages')).to_contain_text('Verify browser-model-a')
            expect(page.locator('#agent-messages')).to_contain_text('Verify browser-model-b')

            page.goto(self.url + '/setting_/agent_provider')
            for remaining in (1, 0):
                expect(page.locator('#agent-provider-name')).not_to_have_value('', timeout=30000)
                page.locator('#agent-provider-delete').click()
                page.locator('.ant-popover:visible').get_by_role('button', name='确认删除', exact=True).click()
                expect(page.locator('.agent-config-feedback')).to_contain_text('已删除', timeout=30000)
                page.reload()
                expect(page.locator('.agent-config-list-foot')).to_contain_text(f'{remaining} / 12')

            page.goto(self.url + '/_ui/login/ui_member')
            page.goto(self.url + '/setting_/agent_provider')
            expect(page.locator('.agent-config-page')).to_have_count(0)
            expect(page.locator('body')).to_contain_text('您没有权限访问该页面', timeout=30000)
            self.assertEqual(errors, [])
            context.close()
            browser.close()

    def test_internal_agent_configuration_and_native_history_chat(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_internal_agent')
            page.goto(self.url + '/setting_/agent_provider')
            page.locator('#agent-provider-add').click()
            page.locator('.ant-select').filter(has=page.locator('#agent-provider-kind')).locator(
                '.ant-select-selector').click()
            page.get_by_title('企业内部 Agent 接口', exact=True).click()
            expect(page.locator('#agent-internal-agent-fields')).to_be_visible()
            expect(page.locator('#agent-provider-model')).to_be_hidden()
            page.locator('#agent-provider-name').fill('Internal Agent API 比赛 Agent')
            page.locator('#agent-provider-url').fill('https://internal-agent.invalid/v5')
            page.locator('#agent-provider-key').fill('browser-internal_agent-subscription')
            for key, value in {
                'agent_id': '11111111-1111-4111-8111-111111111111',
                'tenant_id': '22222222-2222-4222-8222-222222222222',
                'client_id': '33333333-3333-4333-8333-333333333333',
                'scope': 'api://browser-test/.default',
                'client_secret': 'browser-internal_agent-client-secret',
            }.items():
                page.locator('#agent-internal-agent-' + key).fill(value)
            page.locator('#agent-provider-test').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('连接成功', timeout=30000)
            page.locator('#agent-provider-save').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('加密保存', timeout=30000)
            expect(page.locator('#agent-provider-key')).to_have_value('')
            expect(page.locator('#agent-internal-agent-client_secret')).to_have_value('')
            page.reload()
            expect(page.locator('#agent-internal-agent-agent_id')).to_have_value(
                '11111111-1111-4111-8111-111111111111', timeout=30000)
            expect(page.locator('#agent-internal-agent-client_secret')).to_have_value('')
            page.locator('#agent-provider-test').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('连接成功', timeout=30000)
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in [(1440, 900), (1366, 768), (1024, 768), (768, 1024), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.locator('#agent-internal-agent-fields').scroll_into_view_if_needed()
                self.assertTrue(page.locator('.agent-config-page').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                self.assertTrue(page.locator('.platform-content-column').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                fields = page.locator('#agent-internal-agent-fields .agent-config-field').evaluate_all(
                    'elements => elements.map(element => element.getBoundingClientRect().toJSON())')
                if width > 640:
                    self.assertAlmostEqual(fields[0]['top'], fields[1]['top'], delta=1)
                    self.assertGreater(fields[1]['left'], fields[0]['right'])
                else:
                    self.assertGreater(fields[1]['top'], fields[0]['bottom'])
                page.locator('.agent-config-form-card .agent-config-card-heading').scroll_into_view_if_needed()
                self.assertTrue(page.locator('#agent-provider-save').evaluate('''element => {
                    const box = element.getBoundingClientRect();
                    return box.top >= 0 && box.bottom <= innerHeight &&
                        element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
                }'''), f'Save button is not reachable without scrolling at {width}px')
                self.assertTrue(page.locator('.agent-config-feedback').evaluate('''element => {
                    const box = element.getBoundingClientRect();
                    return box.top >= 0 && box.bottom <= innerHeight;
                }'''), f'Connection feedback is outside the viewport at {width}px')
                page.screenshot(path=str(report / f'internal_agent-configuration-{width}.png'))
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.locator('#platform-appearance summary').click()
            page.locator('[data-theme-mode="dark"]').click()
            page.keyboard.press('Escape')
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            page.locator('#agent-internal-agent-fields').scroll_into_view_if_needed()
            page.screenshot(path=str(report / 'internal_agent-configuration-dark-1440.png'))
            page.goto(self.url + '/dashboard_/monitor')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            baseline = len(context.request.get(self.url + '/_ui/stats').json()['internal_agent_history_lengths'])
            for prompt in ('First Internal Agent API question', 'Continue Internal Agent API question'):
                page.locator('#agent-prompt').fill(prompt)
                page.locator('#agent-send').click()
                try:
                    expect(page.locator('#agent-messages')).to_contain_text('INTERNAL_AGENT REPLY: ' + prompt,
                                                                          timeout=30000)
                except AssertionError as error:
                    raise AssertionError(
                        f'{error}\nPage errors: {errors}\n'
                        f"Message HTML: {page.locator('#agent-messages').inner_html()}"
                    ) from error
                expect(page.locator('#agent-prompt')).to_be_enabled()
                page.reload()
                page.locator('#agent-launcher').click()
                expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            lengths = context.request.get(self.url + '/_ui/stats').json()['internal_agent_history_lengths']
            self.assertEqual(lengths[baseline:], [0, 1])
            expect(page.locator('#agent-messages')).to_contain_text('First Internal Agent API question')
            expect(page.locator('#agent-messages')).to_contain_text('Continue Internal Agent API question')
            page.screenshot(path=str(report / 'internal_agent-chat-dark-1440.png'))
            page.goto(self.url + '/setting_/agent_provider')
            expect(page.locator('#agent-provider-name')).to_have_value('Internal Agent API 比赛 Agent', timeout=30000)
            page.locator('#agent-provider-delete').click()
            page.get_by_role('button', name='确认删除', exact=True).click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('已删除', timeout=30000)
            self.assertEqual(errors, [])
            context.close()
            browser.close()

    def test_enterprise_models_file_setup_chat_and_owner_permissions(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_enterprise')
            page.goto(self.url + '/setting_/agent_provider')
            page.locator('#agent-provider-add').click()
            page.locator('.ant-select').filter(has=page.locator('#agent-provider-kind')).locator(
                '.ant-select-selector').click()
            page.get_by_title('企业模型', exact=True).click()
            expect(page.locator('#agent-enterprise-fields')).to_be_visible()
            expect(page.locator('#agent-provider-key')).to_be_hidden()
            expect(page.locator('#agent-provider-url')).to_be_disabled()
            page.locator('#agent-provider-name').fill('Enterprise test connection')
            page.locator('#agent-enterprise-models').fill('gpt-5-mini\nenterprise-test-model')
            page.locator('.ant-select').filter(has=page.locator('#agent-enterprise-model')).locator(
                '.ant-select-selector').click()
            page.get_by_title('enterprise-test-model', exact=True).click()
            page.locator('#agent-enterprise-check').click()
            expect(page.locator('#agent-enterprise-login-status')).to_contain_text('尚未验证上游')
            page.locator('#agent-provider-test').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('连接成功', timeout=30000)
            page.locator('#agent-provider-save').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('Token 仍只保留', timeout=30000)
            page.locator('#agent-provider-make-default').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('已更新新对话', timeout=30000)
            page.reload()
            expect(page.locator('#agent-enterprise-models')).to_have_value('gpt-5-mini\nenterprise-test-model')
            expect(page.locator('.ant-select').filter(has=page.locator('#agent-enterprise-model'))).to_contain_text(
                'enterprise-test-model')
            self.assertNotIn('browser-enterprise-fake-access', page.content())
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in [(1440, 900), (1366, 768), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.locator('#agent-enterprise-fields').scroll_into_view_if_needed()
                self.assertTrue(page.locator('.agent-config-page').evaluate(
                    'element => element.scrollWidth <= element.clientWidth'))
                self.assertTrue(page.locator('#agent-provider-save').evaluate('''element => {
                    const box = element.getBoundingClientRect();
                    return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
                }'''), f'Save button is obscured at {width}px')
                page.screenshot(path=str(report / f'enterprise-models-configuration-{width}.png'))
            context.add_cookies([{'name': 'dash-i18n', 'value': 'en-us', 'url': self.url}])
            page.reload()
            expect(page.get_by_role('button', name='Check Enterprise AI sign-in', exact=True)).to_be_visible()
            page.locator('#agent-enterprise-check').click()
            expect(page.locator('#agent-enterprise-login-status')).to_contain_text('upstream not yet verified')
            context.add_cookies([{'name': 'dash-i18n', 'value': 'zh-cn', 'url': self.url}])
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.goto(self.url + '/dashboard_/monitor')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            profile_id = page.locator('#agent-conversation-title').get_attribute('data-profile-id')
            baseline = len(context.request.get(self.url + '/_ui/stats').json()['enterprise_parents'])
            for prompt in ('First enterprise question', 'Second enterprise question'):
                page.locator('#agent-prompt').fill(prompt)
                page.locator('#agent-send').click()
                expect(page.locator('#agent-messages')).to_contain_text('ENTERPRISE REPLY: ' + prompt,
                                                                      timeout=30000)
                expect(page.locator('#agent-prompt')).to_be_enabled()
                page.reload()
                page.locator('#agent-launcher').click()
                expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['enterprise_parents'][baseline:],
                             [None, 'browser-parent'])
            page.goto(self.url + '/_ui/login/ui_config')
            page.goto(self.url + '/setting_/agent_provider')
            page.locator('.ant-select').filter(has=page.locator('#agent-provider-kind')).locator(
                '.ant-select-selector').click()
            expect(page.get_by_title('企业模型', exact=True)).to_have_count(0)
            page.keyboard.press('Escape')
            page.goto(self.url + '/_ui/login/ui_member')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-conversation-title')).to_have_attribute('data-conversation-id',
                                                                              re.compile('.+'))
            conversation = page.locator('#agent-conversation-title').get_attribute('data-conversation-id')
            before = context.request.get(self.url + '/_ui/stats').json()['enterprise_parents']
            denied = context.request.post(self.url + '/agent_stream', data={
                'conversation_id': conversation, 'profile_id': profile_id,
                'prompt': 'Forbidden browser request', 'allow_log_export': False,
            })
            self.assertEqual(denied.status, 403)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['enterprise_parents'], before)
            page.goto(self.url + '/_ui/login/ui_enterprise')
            page.goto(self.url + '/setting_/agent_provider')
            expect(page.locator('#agent-provider-name')).to_have_value('Enterprise test connection')
            page.locator('#agent-provider-delete').click()
            page.get_by_role('button', name='确认删除', exact=True).click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('已删除', timeout=30000)
            self.assertEqual(errors, [])
            context.close()
            browser.close()

    def test_login_progress_and_failure_follow_language(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            page = context.new_page()
            for locale, progress, failure in [
                ('en-us', 'Verifying account', 'Username or password error'),
                ('zh-cn', '正在验证账号', '用户名或密码错误'),
            ]:
                context.add_cookies([{'name': 'dash-i18n', 'value': locale, 'url': self.url}])
                page.goto(self.url + '/login')
                page.locator('#login-username').fill('locale-audit-missing-user')
                page.locator('#login-password').fill('test-only-invalid-password')
                pending = []

                def hold_login(route):
                    payload = route.request.post_data_json
                    if 'login-store-fc.data' in str(payload.get('output', '')):
                        pending.append(route)
                    else:
                        route.continue_()

                page.route('**/_dash-update-component', hold_login)
                page.locator('#login-submit').click()
                expect(page.locator('#login-status')).to_contain_text(progress)
                self.assertEqual(len(pending), 1)
                pending[0].continue_()
                expect(page.locator('body')).to_contain_text(failure)
                expect(page.locator('#login-status')).to_have_text('')
                page.unroute('**/_dash-update-component', hold_login)
            context.close()
            browser.close()

    def test_all_registered_pages_switch_language(self):
        import re
        from playwright.sync_api import sync_playwright, expect

        routes = [
            '/dashboard_/monitor', '/person_/personal_info', '/message_/announcement',
            '/access_/role_mgmt', '/access_/user_mgmt', '/access_/group_mgmt', '/access_/group_auth',
            '/task_/overview', '/task_/task_mgmt', '/task_/windows_scheduler', '/task_/task_log',
            '/setting_/notify_api', '/setting_/listen_api', '/setting_/agent_provider',
            '/example_app/subapp1', '/example_app/subapp2',
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1366, 'height': 900})
            page = context.new_page()
            page.goto(self.url + '/_ui/login/ui_i18n')
            for locale in ('en-us', 'zh-cn'):
                context.add_cookies([{
                    'name': 'dash-i18n', 'value': locale, 'url': self.url,
                }])
                for route in routes:
                    page.goto(self.url + route)
                    pane = page.locator(f'[id="tabs-container-panel-{route}"]')
                    expect(pane).to_be_visible(timeout=60000)
                    # Wait for request-driven content rather than the initial empty tab.
                    expect(pane).not_to_have_text('')
                    if route == '/task_/task_mgmt':
                        expect(page.locator('#tc-platform-table')).to_contain_text('ui_job', timeout=30000)
                    elif route == '/dashboard_/monitor':
                        expect(page.locator('#monitor-summary .monitor-metric')).to_have_count(4, timeout=30000)
                    if locale == 'en-us':
                        headings = pane.locator('h1, h2, h3, .ant-table-thead, .ant-form-item-label')
                        for heading in headings.all():
                            self.assertIsNone(re.search(r'[\u4e00-\u9fff]', heading.inner_text()), route)
                        attributes = pane.locator('[placeholder], [aria-label]').evaluate_all(
                            """elements => elements.filter(e => e.getClientRects().length).flatMap(e =>
                                [e.getAttribute('placeholder'), e.getAttribute('aria-label')]).filter(Boolean)"""
                        )
                        self.assertFalse([text for text in attributes if re.search(r'[\u4e00-\u9fff]', text)],
                                         route)
                        if route.startswith(('/task_/', '/setting_/', '/example_app/')):
                            expect(pane).not_to_contain_text(re.compile(r'[\u4e00-\u9fff]'))
                        expect(page.locator('#main-menu')).not_to_contain_text(re.compile(r'[\u4e00-\u9fff]'))
                    if route in ('/access_/role_mgmt', '/access_/user_mgmt', '/access_/group_mgmt'):
                        prefix = route.rsplit('/', 1)[1].replace('_', '-')
                        page.locator(f'#{prefix}-button-add').click()
                        modal = page.locator('.ant-modal-content:visible')
                        expect(modal).to_be_visible()
                        if locale == 'en-us':
                            expect(modal).not_to_contain_text(re.compile(r'[\u4e00-\u9fff]'))
                page.goto(self.url + '/task_/task_mgmt')
                page.locator('#tc-platform-create').click()
                editor = page.locator('.ant-modal-content:visible')
                expect(editor).to_be_visible()
                expect(editor).to_contain_text('新建平台任务' if locale == 'zh-cn' else 'Create platform task')
                if locale == 'en-us':
                    expect(editor).not_to_contain_text(re.compile(r'[\u4e00-\u9fff]'))
                page.goto(self.url + '/dashboard_/monitor')
                page.locator('#agent-launcher').click()
                expect(page.locator('#agent-panel')).to_be_visible()
                if locale == 'en-us':
                    page.wait_for_function("""() => {
                        const panel = document.getElementById('agent-panel').cloneNode(true);
                        panel.querySelectorAll(
                            '.ant-select:has(#agent-conversation-select), .ant-select:has(#agent-provider-profile), ' +
                            '#agent-conversation-title, #agent-messages, #agent-model-status'
                        ).forEach(element => element.remove());
                        return !/[\\u4e00-\\u9fff]/.test(panel.textContent);
                    }""", timeout=15000)
                page.goto(self.url + '/locale_audit_not_found')
                expect(page.locator('.ant-result')).to_contain_text(
                    '页面不存在' if locale == 'zh-cn' else 'Page not found')
            page.goto(self.url + '/_ui/login/ui_member')
            for locale, message in [('en-us', 'You do not have permission'), ('zh-cn', '您没有权限')]:
                context.add_cookies([{'name': 'dash-i18n', 'value': locale, 'url': self.url}])
                page.goto(self.url + '/setting_/agent_provider')
                expect(page.locator('.ant-result')).to_contain_text(message)
            context.clear_cookies()
            context.add_cookies([{'name': 'dash-i18n', 'value': 'en-us', 'url': self.url}])
            page.goto(self.url + '/login')
            expect(page.locator('#login-container')).to_be_visible()
            expect(page.locator('#login-container')).not_to_contain_text(re.compile(r'[\u4e00-\u9fff]'))
            page.locator('#global-locale-switch').click()
            expect(page.locator('#login-title')).to_have_text('登录')
            context.close()
            browser.close()

    def test_settings_and_management_horizontal_alignment(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = context.new_page()
            page.goto(self.url + '/_ui/login/ui_config')
            routes = [
                ('/setting_/notify_api', '.channel-settings-page', '.channel-create-card'),
                ('/setting_/listen_api', '.channel-settings-page', '.channel-create-card'),
                ('/setting_/agent_provider', '.agent-config-page', '.agent-config-notice'),
                ('/task_/task_mgmt', '.tc-page', '.tc-page-heading'),
                ('/access_/role_mgmt', '#role-mgmt-button-add', '#role-mgmt-button-add'),
            ]
            for route, root, content in routes:
                page.goto(self.url + route)
                expect(page.locator(root + ':visible')).to_be_visible(timeout=60000)
                for width in (1920, 1366, 768, 390, 320):
                    page.set_viewport_size({'width': width, 'height': 900})
                    measurements = page.locator(content + ':visible').evaluate(
                        """element => {
                            const pane = element.closest('.ant-tabs-tabpane');
                            const style = getComputedStyle(pane);
                            const bounds = pane.getBoundingClientRect();
                            const content = element.getBoundingClientRect();
                            return {
                                left: content.left - bounds.left - parseFloat(style.paddingLeft),
                                right: bounds.right - parseFloat(style.paddingRight) - content.right
                            };
                        }"""
                    )
                    self.assertAlmostEqual(measurements['left'], 0, delta=1,
                                           msg=f'{route}: left edge at {width}px')
                    if route != '/access_/role_mgmt':
                        self.assertAlmostEqual(measurements['right'], 0, delta=1,
                                               msg=f'{route}: right edge at {width}px')
            context.close()
            browser.close()

    def test_channel_settings_layout_crud_themes_and_language(self):
        import re
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1366, 'height': 768}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_config')
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)

            def check_layout(prefix, state):
                for width, height in [(1366, 768), (768, 1024), (390, 844), (320, 700)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    page.locator('.channel-create-card').scroll_into_view_if_needed()
                    self.assertTrue(page.locator('.channel-settings-page').evaluate(
                        'element => element.scrollWidth <= element.clientWidth'), f'{prefix}: {width}px')
                    self.assertTrue(page.locator('.platform-content-column').evaluate(
                        'element => element.scrollWidth <= element.clientWidth'))
                    for selector in ('.channel-card', '.channel-add-actions .ant-btn', '.channel-name-field'):
                        for element in page.locator(selector).all():
                            self.assertTrue(element.evaluate('element => element.scrollWidth <= element.clientWidth'),
                                            f'{prefix} {selector}: {width}px')
                    page.screenshot(path=str(report / f'{prefix}-{state}-{width}.png'))
                page.set_viewport_size({'width': 1366, 'height': 768})

            for kind in ('notify', 'listen'):
                prefix = kind + '-api'
                page.goto(self.url + f'/setting_/{kind}_api')
                expect(page.locator('.channel-settings-page')).to_be_visible(timeout=30000)
                expect(page.locator('.channel-activation-empty')).to_be_visible()
                expect(page.locator('.channel-empty-state')).to_be_visible()
                expect(page.locator('.channel-empty-icon svg')).to_have_count(1)
                expect(page.locator('.channel-section-icon').first).to_have_css('width', '32px')
                self.assertLess(page.locator('.channel-note-icon').first.bounding_box()['width'], 20)
                expect(page.locator('.channel-card-heading h2').first).to_have_css('font-size', '14px')
                self.assertNotIn('There are no available', page.locator('.channel-settings-page').inner_text())
                check_layout(prefix, 'empty-zh')
                if kind == 'notify':
                    channels = [
                        ('SMTP demo', 'notify-api-add-email-smtp', 'notify-api-email-smtp', {
                            'Host': 'smtp.example.invalid', 'Port': '465', 'User': 'demo@example.invalid',
                            'Password': 'browser-only-smtp', 'Receivers': 'recipient@example.invalid',
                        }, 'Password'),
                        ('Server demo', 'notify-api-add-serverchan', 'notify-api-server-jiang', {
                            'SendKey': 'browser-only-sendkey', 'Channel': '9', 'Openid': 'demo-openid',
                        }, 'SendKey'),
                        ('Gewechat demo', 'notify-api-add-Gewechat', 'notify-api-Gewechat', {
                            'token': 'browser-only-token', 'app_id': 'demo-app',
                            'base_url': 'https://gewechat.invalid', 'wxid': 'demo-wxid',
                        }, 'token'),
                        ('WeCom demo', 'notify-api-add-wecom-group-robot', 'notify-api-wecom-group-robot', {
                            'Key': 'browser-only-wecom',
                        }, 'Key'),
                    ]
                else:
                    channels = [('POP3 demo', 'listen-api-add-email-pop3', 'listen-api-pop3', {
                        'pop3-server': 'pop.example.invalid', 'port': '995',
                        'email-account': 'demo@example.invalid', 'password': 'browser-only-pop3',
                    }, 'password')]
                for name, add_id, field_prefix, values, secret in channels:
                    page.locator('#' + prefix + '-add-name').fill(name)
                    page.locator('#' + add_id).click()
                    tab = page.locator('#' + prefix + '-edit-tabs').get_by_role('tab').filter(has_text=name)
                    expect(tab).to_be_visible(timeout=15000)
                    tab.click()
                    expect(page.locator('.channel-empty-state')).to_have_count(0)
                    expect(page.locator('.channel-activation-empty')).to_be_hidden()
                    if kind == 'listen':
                        expect(page.locator(f'input[id*="{field_prefix}-port"]')).to_have_value('995')
                    for key, value in values.items():
                        page.locator(f'input[id*="{field_prefix}-{key}"]').fill(value)
                    expect(page.locator(f'input[id*="{field_prefix}-{secret}"]')).to_have_attribute('type', 'password')
                    page.locator(f'button[id*="{field_prefix}-save"]').click()
                    expect(page.locator('.ant-message')).to_contain_text('配置保存成功', timeout=15000)
                    page.reload()
                    tab = page.locator('#' + prefix + '-edit-tabs').get_by_role('tab').filter(has_text=name)
                    expect(tab).to_be_visible(timeout=30000)
                    tab.click()
                    for key, value in values.items():
                        expect(page.locator(f'input[id*="{field_prefix}-{key}"]')).to_have_value(value)
                    page.locator('#' + prefix + '-activate').get_by_label(name, exact=True).check()
                    expect(page.locator('#' + prefix + '-activate').get_by_label(name, exact=True)).to_be_checked()
                    page.reload()
                    expect(page.locator('#' + prefix + '-activate').get_by_label(name, exact=True)).to_be_checked(timeout=30000)
                    page.locator('#' + prefix + '-edit-tabs').get_by_role('tab').filter(has_text=name).click()
                    baseline = len(context.request.get(self.url + '/_ui/stats').json()['channel_test_calls'])
                    page.locator(f'button[id*="{field_prefix}-test"]').click()
                    expect(page.locator('.ant-message')).to_contain_text('成功', timeout=15000)
                    self.assertEqual(len(context.request.get(self.url + '/_ui/stats').json()['channel_test_calls']),
                                     baseline + 1)
                check_layout(prefix, 'configured-zh')
                page.locator('#global-locale-switch').click()
                expect(page.locator('#' + prefix + '-add-name')).to_have_attribute('placeholder', 'e.g. Team alerts / Demo',
                                                                                 timeout=30000)
                for mode in ('light', 'dark'):
                    page.locator('#platform-appearance summary').click()
                    page.locator(f'[data-theme-mode="{mode}"]').click()
                    page.keyboard.press('Escape')
                    self.assertIsNone(re.search(r'[\u4e00-\u9fff]', page.locator('.channel-settings-page').inner_text()))
                    check_layout(prefix, 'en-' + mode)
                page.locator('#' + prefix + '-activate').get_by_label(channels[-1][0], exact=True).uncheck()
                page.reload()
                expect(page.locator('#' + prefix + '-activate').get_by_label(channels[-1][0], exact=True)).not_to_be_checked(
                    timeout=30000)
                page.locator('#global-locale-switch').click()
                expect(page.locator('#' + prefix + '-add-name')).to_have_attribute('placeholder', '例如：团队告警 / 比赛演示',
                                                                                 timeout=30000)
            self.assertEqual(errors, [])
            context.close()
            browser.close()

    def test_provider_settings_language_switch_and_compact_typography(self):
        import re
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1366, 'height': 768}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_locale')
            page.goto(self.url + '/setting_/agent_provider')
            expect(page.locator('.agent-config-page')).to_be_visible(timeout=30000)
            expect(page.locator('.agent-config-page h1')).to_have_count(0)
            expect(page.locator('.agent-config-heading')).to_have_count(0)
            expect(page.locator('.agent-config-profile-card #agent-config-live-status')).to_be_visible()
            expect(page.locator('.agent-config-card-heading h2').first).to_have_css('font-size', '14px')
            expect(page.locator('.agent-config-field label').first).to_have_css('font-size', '14px')
            expect(page.locator('.agent-config-field small').first).to_have_css('font-size', '12px')
            expect(page.locator('.agent-config-notice .ant-alert-message')).to_have_css('font-size', '14px')
            expect(page.locator('.agent-config-notice .ant-alert-description')).to_have_css('font-size', '12px')

            page.locator('#global-locale-switch').click()
            expect(page.get_by_role('main', name='Agent Connections')).to_be_visible(timeout=30000)
            expect(page.get_by_role('tab', name='Agent Connections', exact=True)).to_be_visible()
            expect(page.locator('#platform-navigation')).to_contain_text('Agent Connections')
            expect(page.locator('.agent-config-profile-card')).to_contain_text('Default service')
            page.locator('#agent-provider-add').click()
            expect(page.locator('#agent-provider-editor-status')).to_contain_text('New profile')
            page.locator('#agent-provider-save').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('Enter a profile name')
            page.locator('.ant-select:has(#agent-provider-kind) .ant-select-selector').click()
            page.get_by_title('Internal Agent API', exact=True).click()
            page.locator('#agent-provider-name').fill('Bilingual Internal Agent API')
            page.locator('#agent-provider-url').fill('https://internal-agent.invalid/v5')
            page.locator('#agent-provider-key').fill('browser-locale-subscription')
            page.locator('#agent-provider-test').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('Internal Agent API Agent ID must be a valid UUID.')
            for key, value in {
                'agent_id': '11111111-1111-4111-8111-111111111111',
                'tenant_id': '22222222-2222-4222-8222-222222222222',
                'client_id': '33333333-3333-4333-8333-333333333333',
                'scope': 'api://browser-test/.default',
                'client_secret': 'browser-locale-secret',
            }.items():
                page.locator('#agent-internal-agent-' + key).fill(value)
            page.locator('#agent-provider-test').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('Connection successful', timeout=30000)
            page.locator('#agent-provider-save').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('Profile saved with encryption')
            page.locator('#agent-provider-make-default').click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('Default profile updated')
            expect(page.locator('#agent-config-live-detail')).to_contain_text('New conversations')
            expect(page.locator('#agent-config-live-status')).to_have_text('Internal Agent API')
            expect(page.locator('#agent-provider-key')).to_have_value('')
            expect(page.locator('#agent-internal-agent-client_secret')).to_have_value('')
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for mode in ('light', 'dark'):
                page.locator('#platform-appearance summary').click()
                page.locator(f'[data-theme-mode="{mode}"]').click()
                page.keyboard.press('Escape')
                for width, height in [(1366, 768), (390, 844), (320, 700)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    page.locator('.agent-config-form-card .agent-config-card-heading').scroll_into_view_if_needed()
                    self.assertIsNone(re.search(r'[\u4e00-\u9fff]', page.locator('.agent-config-page').inner_text()))
                    self.assertTrue(page.locator('.agent-config-page').evaluate(
                        'element => element.scrollWidth <= element.clientWidth'))
                    self.assertTrue(page.locator('.agent-config-page').evaluate('''element =>
                        [...element.querySelectorAll('*')].filter(child => child.getClientRects().length &&
                            !child.closest('.ant-select, .ant-input-affix-wrapper')).every(child => {
                            const box = child.getBoundingClientRect();
                            const page = element.getBoundingClientRect();
                            return box.left >= page.left - 1 && box.right <= page.right + 1;
                        })'''))
                    for button in page.locator('.agent-config-actions .ant-btn').all():
                        self.assertTrue(button.evaluate('element => element.scrollWidth <= element.clientWidth + 1'),
                                        f'Action text overflows at {width}px: ' + str(button.evaluate(
                                            """element => ({
                                                text: element.textContent,
                                                scroll: element.scrollWidth,
                                                client: element.clientWidth,
                                                width: element.getBoundingClientRect().width
                                            })""")))
                    page.screenshot(path=str(report / f'agent-settings-en-{mode}-{width}.png'))
                page.set_viewport_size({'width': 1366, 'height': 768})
            page.reload()
            expect(page.get_by_role('main', name='Agent Connections')).to_be_visible(timeout=30000)
            expect(page.locator('#agent-provider-name')).to_have_value('Bilingual Internal Agent API')
            expect(page.locator('#agent-provider-editor-status')).to_contain_text('Credentials are stored securely')
            page.locator('#agent-provider-delete').click()
            expect(page.locator('.ant-popover:visible')).to_contain_text('Local chat history is kept')
            page.get_by_role('button', name='Cancel', exact=True).click()
            page.locator('#global-locale-switch').click()
            expect(page.get_by_role('main', name='Agent 接入')).to_be_visible(timeout=30000)
            expect(page.get_by_role('tab', name='Agent 接入', exact=True)).to_be_visible()
            expect(page.locator('#agent-provider-name')).to_have_value('Bilingual Internal Agent API')
            expect(page.locator('#agent-provider-key')).to_have_value('')
            expect(page.locator('#agent-internal-agent-client_secret')).to_have_value('')
            page.locator('#agent-provider-delete').click()
            page.get_by_role('button', name='确认删除', exact=True).click()
            expect(page.locator('.agent-config-feedback')).to_contain_text('配置已删除')
            self.assertEqual(errors, [])
            context.close()
            browser.close()

    def test_page_assistant_compact_layout_and_loading_states(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            model_requests = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: model_requests.append(request.url)
                    if '/page_agent_llm/v1/' in request.url else None)
            page.goto(self.url + '/_ui/login/ui_locale')
            expect(page.locator('.monitor-metric')).to_have_count(4, timeout=60000)
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=15000)
            expect(page.locator('#page-agent-scope')).to_have_text('系统监控')
            details = page.locator('.page-agent-details')
            self.assertFalse(details.evaluate('(element) => element.open'))
            expect(page.locator('#page-agent-launcher')).to_have_text('启用')
            expect(page.locator('#page-agent-launcher')).not_to_be_visible()
            expect(page.locator('#agent-conversation-profile-feedback')).not_to_be_visible()
            self.assertFalse(page.evaluate('Boolean(window.PageAgent || window.pageAgent)'))
            self.assertEqual(model_requests, [])
            expect(page.locator('#agent-composer').get_by_role('checkbox')).not_to_be_checked()
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width in (1440, 1366, 390, 320):
                page.set_viewport_size({'width': width, 'height': 900 if width > 700 else 740})
                expect(page.locator('#agent-panel')).to_be_in_viewport()
                expect(page.locator('#agent-send')).to_be_in_viewport()
                expect(page.locator('#agent-provider-profile')).to_be_in_viewport()
                service = page.locator('.agent-service-toolbar').bounding_box()
                auxiliary = details.locator('summary').bounding_box()
                self.assertLess(abs(service['y'] + service['height'] / 2
                                    - auxiliary['y'] - auxiliary['height'] / 2), 3)
                self.assertTrue(page.locator('#agent-panel').evaluate(
                    '(element) => element.scrollWidth <= element.clientWidth + 1'))
                page.screenshot(path=str(report / f'page-assistant-compact-{width}.png'))
                details.locator('summary').click()
                expect(page.locator('#page-agent-capabilities')).to_have_text('读取指标 / 打开任务中心')
                expect(page.locator('.page-agent-policy')).to_contain_text('不执行任务')
                expect(page.locator('#agent-send')).to_be_in_viewport()
                expect(page.locator('.page-agent-details-content')).to_be_in_viewport()
                page.screenshot(path=str(report / f'page-assistant-expanded-{width}.png'))
                page.keyboard.press('Escape')
                self.assertFalse(details.evaluate('(element) => element.open'))
                expect(page.locator('#agent-panel')).to_be_visible()

            details.locator('summary').click()
            page.locator('#agent-prompt').click()
            self.assertFalse(details.evaluate('(element) => element.open'))
            details.locator('summary').click()
            page.route('**/assets/vendor/page-agent/**', lambda route: route.abort())
            page.locator('#page-agent-launcher').click()
            expect(page.locator('#page-agent-status')).to_have_class(
                'page-agent-status page-agent-status-loading')
            expect(page.locator('#page-agent-launcher')).to_have_text('加载中…')
            expect(page.locator('#page-agent-launcher')).to_be_disabled()
            expect(page.locator('#page-agent-status')).to_have_class(
                'page-agent-status page-agent-status-error', timeout=20000)
            expect(page.locator('#page-agent-launcher')).to_have_text('重试')
            expect(page.locator('#page-agent-launcher')).to_be_enabled()
            page.unroute('**/assets/vendor/page-agent/**')
            page.locator('#page-agent-launcher').click()
            expect(page.locator('#page-agent-status')).to_have_class(
                'page-agent-status page-agent-status-ready', timeout=20000)
            expect(page.locator('#page-agent-status')).to_have_text('工具已就绪，模型连接将在使用时建立。')
            expect(page.locator('#page-agent-launcher')).to_have_text('已启用')
            self.assertTrue(page.evaluate('Boolean(window.pageAgent)'))
            self.assertEqual(model_requests, [])
            page.screenshot(path=str(report / 'page-assistant-ready-320.png'))
            self.assertEqual(errors, [])
            browser.close()

    def test_robot_and_persistent_dialog(self):
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.url + '/_ui/login/ui_alice')
            page.wait_for_selector('#agent-robot-scene[data-scene-state="ready"]', timeout=60000)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['conversations'], 0)
            pixels = page.locator('#agent-robot-scene canvas').evaluate('''canvas => {
                const copy = document.createElement('canvas'); copy.width = canvas.width; copy.height = canvas.height;
                const context = copy.getContext('2d'); context.drawImage(canvas, 0, 0);
                const pixels = context.getImageData(0, 0, copy.width, copy.height).data;
                return [...pixels].filter((value, index) => index % 4 === 3 && value > 0).length;
            }''')
            self.assertGreater(pixels, 300)
            if os.environ.get('PLATFORM_AGENT_RENDER_FALLBACK') == '1':
                import base64

                image = page.locator('#agent-robot-scene canvas').evaluate('canvas => canvas.toDataURL("image/png")')
                (ROOT / 'src/assets/imgs/agent-robot.png').write_bytes(base64.b64decode(image.split(',', 1)[1]))
            first_frame = int(page.locator('#agent-robot-scene').get_attribute('data-scene-frame'))
            page.wait_for_function('frame => Number(document.querySelector("#agent-robot-scene").dataset.sceneFrame) > frame + 3', arg=first_frame)
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['conversations'], 1)
            page.locator('#agent-prompt').fill('wait for reply')
            page.locator('#agent-send').click()
            expect(page.locator('#agent-widget')).to_have_attribute('data-agent-state', 'thinking')
            page.locator('#agent-close').click()
            expect(page.locator('#agent-panel')).to_be_hidden()
            context.request.post(self.url + '/_ui/release')
            page.wait_for_function('document.querySelector("#agent-messages").textContent.includes("UI TEST REPLY")')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-prompt')).to_have_value('')
            page.locator('#agent-prompt').fill('keep this draft')
            page.locator('#agent-close').click()
            self.assertFalse(page.locator('#platform-navigation').inner_text().find('用户画像') >= 0)
            page.locator('#main-head-user-menu .ant-dropdown-trigger').click()
            page.get_by_text('个人中心', exact=True).click()
            page.wait_for_url('**/person_/personal_info')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-prompt')).to_have_value('keep this draft')
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['calls'], 1)
            page.locator('#agent-expand').click()
            expect(page.locator('#agent-widget')).to_have_attribute('data-expanded', 'true')
            page.locator('#agent-expand').click()
            page.locator('#agent-prompt').fill('proposal')
            page.locator('#agent-send').click()
            expect(page.locator('#agent-approvals')).to_contain_text('ui_job', timeout=30000)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['submissions'], 0)
            consent = page.locator('#agent-approvals input[type="checkbox"]')
            consent.check()
            page.get_by_role('button', name='批准并运行', exact=True).click()
            expect(page.locator('#agent-approvals')).to_contain_text('调度器已接收', timeout=30000)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['submissions'], 1)
            report = ROOT / 'cache/report/ui'
            report.mkdir(parents=True, exist_ok=True)
            for width, height in [(1440, 900), (1366, 768), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                box = page.locator('#agent-panel').bounding_box()
                send = page.locator('#agent-send').bounding_box()
                self.assertGreaterEqual(box['x'], 0)
                self.assertLessEqual(box['x'] + box['width'], width)
                self.assertLessEqual(send['y'] + send['height'], height)
                self.assertTrue(page.locator('#agent-panel').evaluate('element => element.scrollWidth <= element.clientWidth'))
                if width <= 700:
                    expect(page.locator('#agent-panel')).to_have_attribute('aria-modal', 'true')
                    self.assertTrue(page.locator('.platform-content-column').evaluate('element => element.inert'))
                page.screenshot(path=str(report / f'agent-widget-{width}.png'))
                if width > 700:
                    page.locator('#agent-expand').click()
                    expect(page.locator('#agent-widget')).to_have_attribute('data-expanded', 'true')
                    self.assertTrue(page.locator('#agent-panel').evaluate('''element => {
                        const box = element.getBoundingClientRect();
                        return box.left >= 0 && box.top >= 0 && box.right <= innerWidth &&
                            box.bottom <= innerHeight && element.scrollWidth <= element.clientWidth;
                    }'''))
                    page.screenshot(path=str(report / f'agent-widget-expanded-{width}.png'))
                    page.locator('#agent-expand').click()
                else:
                    expect(page.locator('#agent-expand')).to_be_hidden()
            page.keyboard.press('Escape')
            expect(page.locator('#agent-panel')).to_be_hidden()
            self.assertFalse(page.locator('.platform-content-column').evaluate('element => element.inert'))
            self.assertEqual(page.evaluate('document.activeElement.id'), 'agent-launcher')
            page.reload()
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-messages')).to_contain_text('UI TEST REPLY', timeout=30000)
            self.assertEqual(context.request.get(self.url + '/_ui/stats').json()['calls'], 3)
            page.goto(self.url + '/_ui/login/ui_bob')
            page.locator('#agent-launcher').click()
            expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
            expect(page.locator('#agent-messages')).not_to_contain_text('UI TEST REPLY')
            page.screenshot(path=str(report / 'agent-widget-empty-320.png'))
            page.locator('#agent-prompt').fill('帮我整理任务概览。')
            page.get_by_role('button', name='发送', exact=True).click()
            expect(page.locator('.agent-markdown h3')).to_have_text('任务概览', timeout=30000)
            expect(page.locator('.agent-markdown table')).to_be_visible()
            expect(page.locator('.agent-markdown pre')).to_be_attached()
            self.assertIsNone(page.evaluate('window.agentUnsafeHtml'))
            self.assertEqual(page.locator('.agent-markdown script').count(), 0)
            for width, height in [(1440, 900), (1366, 768), (390, 844), (320, 700)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.locator('.agent-panel-scroll').evaluate('element => { element.scrollTop = 0; }')
                if width == 1440:
                    page.locator('#platform-navigation .ant-menu-submenu-title').first.hover()
                    expect(page.locator('.ant-menu-submenu-popup').first).to_be_visible()
                if width <= 700:
                    expect(page.locator('.ant-menu-submenu-popup:visible')).to_have_count(0)
                self.assertTrue(page.locator('#agent-panel').evaluate('element => element.scrollWidth <= element.clientWidth'))
                self.assertTrue(page.locator('.agent-markdown').evaluate('element => element.scrollWidth <= element.clientWidth'))
                self.assertEqual(page.locator('#agent-send').get_attribute('aria-label'), '发送')
                page.screenshot(path=str(report / f'agent-widget-formatted-{width}.png'))
            page.emulate_media(reduced_motion='reduce')
            page.wait_for_selector('#agent-robot-scene[data-scene-state="ready"]', state='attached')
            self.assertEqual(page.locator('#agent-robot-scene canvas').count(), 1)
            page.locator('#agent-robot-scene canvas').evaluate('canvas => canvas.dispatchEvent(new Event("webglcontextlost", {cancelable: true}))')
            expect(page.locator('#agent-widget')).to_have_attribute('data-scene-state', 'fallback')
            expect(page.locator('.agent-robot-fallback')).to_be_attached()
            expect(page.locator('#agent-send')).to_be_enabled()
            self.assertEqual(errors, [])
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    options = parser.parse_args()
    if options.serve:
        serve_probe(options.port)
    else:
        unittest.main(argv=[sys.argv[0]])