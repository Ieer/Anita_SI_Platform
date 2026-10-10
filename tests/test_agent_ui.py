import argparse
import json
import os
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
        conn.init_rds_data()
        from database.sql_db.entity.table_user import SysUser, SysUserRole

        for owner in ['ui_alice', 'ui_bob', 'ui_theme']:
            values = dict(SysUser.get_by_id('admin').__data__)
            values.update(user_name=owner, user_full_name=owner)
            SysUser.create(**values)
            SysUserRole.create(user_name=owner, role_name='admin')
        gate = threading.Event()
        calls = []
        submissions = []

        def transport(request):
            payload = json.loads(request.content)
            calls.append(payload)
            prompt = next(message['content'] for message in reversed(payload['messages']) if message['role'] == 'user')
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
            return httpx.Response(200, json={
                'id': 'ui_completion', 'object': 'chat.completion', 'created': 1, 'model': 'ui-test-model',
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
        from flask import jsonify, redirect, make_response
        from common.utilities.util_jwt import jwt_encode
        from database.sql_db.repositories import agent_repository

        server = platform_app.server

        @server.get('/_ui/login/<owner>')
        def probe_login(owner):
            if owner not in ['ui_alice', 'ui_bob', 'ui_theme']:
                return '', 404
            response = make_response(redirect('/dashboard_/monitor'))
            response.set_cookie('Authorization', 'Bearer ' + jwt_encode({'user_name': owner, 'token_use': 'session'}),
                                httponly=True, samesite='Lax')
            return response

        @server.get('/_ui/stats')
        def probe_stats():
            return jsonify(calls=len(calls), submissions=len(submissions),
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
                    'element => getComputedStyle(element).backgroundColor'), 'rgb(27, 32, 36)')
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
            expect(page.locator('.ant-modal-content:visible')).to_have_css('background-color', 'rgb(27, 32, 36)')
            page.screenshot(path=str(ROOT / 'cache/report/ui/appearance-dark-modal.png'))
            self.assertFalse(errors, errors)
            loader = context.new_page()
            loader.route('**/_dash-component-suites/**', lambda route: route.abort())
            loader.goto(self.url + '/dashboard_/monitor', wait_until='domcontentloaded')
            expect(loader.locator('._dash-loading')).to_be_visible()
            expect(loader.locator('html')).to_have_attribute('data-theme', 'dark')
            expect(loader.locator('._dash-loading')).to_have_css('background-color', 'rgb(39, 46, 52)')
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