import importlib.util
import json
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
SPEC = importlib.util.spec_from_file_location('scaffold_page', ROOT / 'scripts' / 'scaffold_page.py')
scaffold_page = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scaffold_page)
CHECK_SPEC = importlib.util.spec_from_file_location('check_pages', ROOT / 'scripts' / 'check_pages.py')
check_pages = importlib.util.module_from_spec(CHECK_SPEC)
CHECK_SPEC.loader.exec_module(check_pages)


class ThemeTests(unittest.TestCase):
    def test_theme_callbacks_do_not_sample_or_query_tasks(self):
        import dash
        from dash_callback.application.dashboard_ import monitor_c
        from dash_callback.application.task_ import overview_c

        figure = {'data': [], 'layout': {'uirevision': 'keep'}}
        state = {'mode': 'dark', 'palette': 'blue'}
        with patch.object(monitor_c, 'ctx', SimpleNamespace(triggered_id='platform-theme')), \
                patch.object(monitor_c.util_sys, 'get_sys_info') as sample:
            result = monitor_c.callback_func.__wrapped__(0, 0, 0, True, state, [], figure)
            sample.assert_not_called()
            self.assertIs(result[7], dash.no_update)
            self.assertIs(result[9], dash.no_update)
            self.assertEqual(result[8]['layout']['uirevision'], 'keep')
        with patch.object(dash, 'ctx', SimpleNamespace(triggered_id='platform-theme')), \
                patch.object(overview_c, 'build_overview') as query:
            result = overview_c.refresh_overview(0, 0, 0, 'schedule', '1d', 1, 20, state, figure)
            query.assert_not_called()
            self.assertIs(result[4], dash.no_update)
            self.assertEqual(result[1]['layout']['uirevision'], 'keep')

    def test_root_provider_and_appearance_component_contract(self):
        from plotly.utils import PlotlyJSONEncoder
        import app as platform_app
        from dash_view.framework.head import render_appearance
        from common.utilities.util_dash import CustomDash

        self.assertEqual(platform_app.app.layout().id, 'platform-theme-provider')
        with platform_app.server.test_request_context('/'):
            serialized = json.dumps(render_appearance(), cls=PlotlyJSONEncoder)
        for value in ('platform-theme-contrast', 'data-theme-mode', 'data-theme-palette', 'platform-appearance'):
            self.assertIn(value, serialized)
        markup = CustomDash('theme_probe').interpolate_index(
            metas='', title='Probe', css='<style id="probe-css"></style>', config='',
            scripts='', app_entry='', favicon='', renderer='')
        self.assertLess(markup.index('window.platformThemeConfig'), markup.index('probe-css'))
        self.assertIn('/assets/js/theme.js', markup)

    def test_preference_validation_and_system_resolution(self):
        from common.utilities.util_theme import DEFAULT_PREFERENCE, normalize_preference, theme_spec

        for value in (None, [], {'mode': 'invalid', 'palette': '<script>', 'contrast': []}):
            self.assertEqual(normalize_preference(value), DEFAULT_PREFERENCE)
        self.assertEqual(theme_spec(system_dark=True)['mode'], 'dark')
        self.assertEqual(theme_spec({'mode': 'light'}, system_dark=True)['mode'], 'light')
        self.assertEqual(theme_spec({'mode': 'light'}, login=True)['mode'], 'dark')

    def test_all_theme_text_pairs_meet_contrast_targets(self):
        from common.utilities.util_theme import browser_config

        def luminance(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                      for channel in channels]
            return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))

        def ratio(foreground, background):
            values = sorted((luminance(foreground), luminance(background)))
            return (values[1] + .05) / (values[0] + .05)

        themes = browser_config()['themes']
        self.assertEqual(len(themes), 12)
        for name, spec in themes.items():
            colors = spec['colors']
            target = 7 if name.endswith(':high') else 4.5
            for role in ('ink', 'muted', 'accent', 'success', 'warning', 'danger', 'info', 'secondary'):
                with self.subTest(theme=name, role=role):
                    self.assertGreaterEqual(ratio(colors[role], colors['surface']), target)
            self.assertGreaterEqual(ratio(colors['on-accent'], colors['accent']), target)

    def test_graph_restyle_preserves_data_and_interaction_state(self):
        from common.utilities.util_theme import restyle_figure

        original = {'data': [{'x': [1, 2], 'y': [4, 5], 'line': {'color': '#087f77'}}],
                    'layout': {'uirevision': 'keep', 'xaxis': {'range': [1, 2]}}}
        result = restyle_figure(original, {'mode': 'dark', 'palette': 'rose'})
        self.assertEqual(result['layout']['uirevision'], 'keep')
        self.assertEqual(result['layout']['xaxis']['range'], [1, 2])
        self.assertEqual(result['data'][0]['y'], [4, 5])
        self.assertEqual(original['data'][0]['line']['color'], '#087f77')
        self.assertEqual(result['data'][0]['line']['color'], '#ffadd0')
        restored = restyle_figure(result, {'mode': 'light', 'palette': 'blue'})
        self.assertEqual(restored['data'][0]['line']['color'], '#245ab5')
        self.assertEqual(restyled := restyle_figure(restored, {'mode': 'dark', 'palette': 'teal'}),
                 restyle_figure(restyled, {'mode': 'dark', 'palette': 'teal'}))


class MonitorTests(unittest.TestCase):
    def test_demo_host_snapshot_is_synthetic_and_uses_monitor_contract(self):
        from datetime import datetime
        from scripts.demo_task_center import demo_system_info
        from dash_callback.application.dashboard_ import monitor_c
        from server import server

        info = demo_system_info(datetime(2026, 10, 10, 12, 0, 0))
        with server.test_request_context('/'):
            summary, history, figure, disks, status = monitor_c.build_snapshot(info, [])

        self.assertEqual(info['hostname'], 'demo-host.invalid')
        self.assertEqual(info['sampled_at'], '2026-10-10 12:00:00')
        self.assertEqual(figure['data'][0]['y'], [93.0])
        self.assertEqual(figure['data'][1]['y'], [86.0])
        self.assertEqual(disks[0]['usage'].percent, 92)
        self.assertEqual(disks[0]['status'][0]['color'], 'red')
        self.assertIn('容量预警', status)

    def test_sampling_normalizes_cpu_and_preserves_unreadable_mounts(self):
        from collections import namedtuple
        from common.utilities import util_sys

        cpu_times = namedtuple('CpuTimes', 'user system idle')
        process = SimpleNamespace(pid=123, create_time=lambda: 1000,
                                  memory_info=lambda: SimpleNamespace(rss=1024),
                                  num_threads=lambda: 4, memory_percent=lambda: 2.5)
        util_sys.cache.clear()
        with patch.object(util_sys, '_cpu_sample', cpu_times(10, 20, 70)), \
                patch.object(util_sys.psutil, 'cpu_times', return_value=cpu_times(30, 30, 140)), \
                patch.object(util_sys.psutil, 'Process', return_value=process), \
                patch.object(util_sys.psutil, 'disk_partitions', return_value=[
                    SimpleNamespace(device='device', mountpoint='/mounted', fstype='NTFS'),
                    SimpleNamespace(device='offline', mountpoint='/offline', fstype='NTFS')]), \
                patch.object(util_sys.psutil, 'disk_usage', side_effect=[
                    SimpleNamespace(total=100, used=95, free=5, percent=95), PermissionError()]) as usage:
            info = util_sys.get_sys_info()
        util_sys.cache.clear()
        self.assertAlmostEqual(info['cpu_user_usage_percent'], .2)
        self.assertAlmostEqual(info['cpu_sys_usage_percent'], .1)
        self.assertAlmostEqual(info['cpu_all_usage_percent'], .3)
        self.assertAlmostEqual(info['cpu_free_percent'], .7)
        self.assertEqual(info['process_id'], 123)
        self.assertEqual(info['process_threads'], 4)
        self.assertEqual(info['process_memory_percent'], .025)
        self.assertEqual(info['sys_files'][0]['usage'], '95.0%')
        self.assertFalse(info['sys_files'][1]['readable'])
        self.assertEqual(info['sys_files'][1]['usage'], 'N/A')
        self.assertEqual([call.args[0] for call in usage.call_args_list], ['/mounted', '/offline'])
        self.assertIn('memory_available', info)
        self.assertIn('network_received', info)

    def test_monitor_layout_and_snapshot_contract(self):
        from plotly.utils import PlotlyJSONEncoder
        from server import server
        from common.utilities import util_sys
        from dash_view.application.dashboard_.monitor import render_content
        from dash_callback.application.dashboard_.monitor_c import build_snapshot, percent, descriptions, toggle_refresh

        with server.test_request_context('/'):
            serialized = json.dumps(render_content(None), cls=PlotlyJSONEncoder)
            util_sys.cache.clear()
            info = dict(util_sys.get_sys_info())
            info['sys_files'] = [dict(key='probe', dir_name='probe', sys_type_name='NTFS', type_name='probe',
                                     total='100GB', used='95GB', free='5GB', usage='95%', usage_percent=95, readable=True)]
            info['cpu_all_usage_percent'] = .3
            info['cpu_user_usage_percent'] = .2
            info['cpu_sys_usage_percent'] = .1
            summary, history, figure, disks, status = build_snapshot(info, [])
            self.assertEqual(len(summary), 4)
            self.assertEqual(figure['data'][0]['y'], [30])
            self.assertEqual(disks[0]['usage'].percent, 95)
            self.assertEqual(disks[0]['status'][0]['color'], 'red')
            self.assertEqual(len(build_snapshot(info, history)[1]), 1)
            self.assertEqual(len(build_snapshot(info, [{'time': '2000-01-01 00:00:00', 'cpu': 0, 'memory': 0}])[1]), 1)
            info['sys_files'][0].update(usage_percent=None, readable=False)
            unavailable = build_snapshot(info, [])[3][0]
            self.assertEqual(unavailable['usage'].children, 'N/A')
            with patch('common.utilities.util_menu_access.get_menu_access', return_value=SimpleNamespace(
                    has_access=lambda permission: True)):
                self.assertTrue(toggle_refresh(1)[0])
                self.assertFalse(toggle_refresh(2)[0])
            self.assertEqual(descriptions(info, [('CPU', 'cpu_all_usage_percent', True)])[0]['children'], '30.0%')
        self.assertEqual(percent(None), 'N/A')
        for identifier in ['system-monitor', 'monitor-summary', 'monitor-trend', 'monitor-disk-desc',
                           'monitor-io-desc', 'monitor-refresh', 'monitor-pause']:
            self.assertIn(identifier, serialized)


class ScaffoldTests(unittest.TestCase):
    def test_generated_pages_match_platform_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = scaffold_page.scaffold(root, 'analytics', 'Analytics', 'antd-appstore', 10, ['Overview:overview'])
            self.assertEqual(len(files), 5)
            registry = json.loads((root / 'src/config/applications.json').read_text(encoding='utf-8'))
            self.assertEqual(registry, ['analytics.overview'])
            source = (root / 'src/dash_view/application/analytics/overview.py').read_text(encoding='utf-8')
            self.assertIn('def render_content(menu_access: MenuAccess, **kwargs)', source)
            self.assertIn('platform-template-metrics', source)
            self.assertIn('platform-template-filters', source)
            self.assertIn("template_capabilities = ('metrics', 'filters', 'table')", source)
            self.assertIn("'data-template-capabilities': ','.join(template_capabilities)", source)
            self.assertIn('analytics-overview-content-table', source)
            self.assertIn('access_metas =', source)
            self.assertIn('import dash_callback.application.analytics.overview_c  # noqa', source)
            callback = (root / 'src/dash_callback/application/analytics/overview_c.py').read_text(encoding='utf-8')
            self.assertIn("if not access.has_access('Overview-页面')", callback)
            self.assertIn("Output('analytics-overview-content-table', 'data')", callback)
            self.assertIn('DEMO-001', callback)
            self.assertIn('def filter_sample_rows', callback)
            with self.assertRaises(ValueError):
                scaffold_page.scaffold(root, 'analytics', 'Other', '', 1, ['Other:other'])

    def test_generated_application_can_be_loaded_and_rendered(self):
        from config.access_factory import load_application_views
        import dash_view.application
        import dash_callback.application

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scaffold_page.scaffold(root, 'inventory_probe', 'Probe', 'antd-appstore', 10, ['Probe:overview'])
            with patch.object(dash_view.application, '__path__', [str(root / 'src/dash_view/application')]), \
                    patch.object(dash_callback.application, '__path__', [str(root / 'src/dash_callback/application')]):
                try:
                    pages = load_application_views(root / 'src/config/applications.json')
                    self.assertEqual(pages[0].render_content(None).id, 'inventory_probe-overview-content')
                    callback = sys.modules['dash_callback.application.inventory_probe.overview_c']
                    allowed = SimpleNamespace(has_access=lambda permission: permission == 'Probe-页面')
                    with patch.object(callback, 'get_menu_access', return_value=allowed):
                        rows, status = callback.filter_sample_rows('DEMO-001', 'all')
                        self.assertEqual([row['record_id'] for row in rows], ['DEMO-001'])
                        self.assertEqual(status, '筛选结果 1 条')
                        self.assertEqual(callback.reset_sample_filters(1), ('', 'all'))
                    from dash.exceptions import PreventUpdate

                    denied = SimpleNamespace(has_access=lambda _permission: False)
                    with patch.object(callback, 'get_menu_access', return_value=denied), \
                            self.assertRaises(PreventUpdate):
                        callback.filter_sample_rows('', 'all')
                finally:
                    for name in list(sys.modules):
                        if name.startswith(('dash_view.application.inventory_probe', 'dash_callback.application.inventory_probe')):
                            sys.modules.pop(name)

    def test_full_scaffold_generates_i18n_tests_and_page_type(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = scaffold_page.scaffold(
                root, 'analytics_full', 'Analytics', 'antd-appstore', 10,
                ['Overview:overview:1'], page_type='dashboard',
                with_i18n=True, with_tests=True,
            )
            self.assertEqual(len(files), 7)
            view = (root / 'src/dash_view/application/analytics_full/overview.py').read_text(encoding='utf-8')
            self.assertIn("template_type = 'dashboard'", view)
            self.assertIn("template_capabilities = ('metrics', 'charts', 'filters', 'table')", view)
            self.assertIn("className='platform-template-dashboard'", view)
            self.assertIn("locale_topic='analytics_full'", view)
            catalog = json.loads((root / 'src/translations/topic_locales/analytics_full.json').read_text(encoding='utf-8'))
            self.assertEqual(catalog['topic'], 'analytics_full')
            self.assertIn('记录列表', catalog['contents'])
            generated_test = root / 'tests/test_analytics_full_pages.py'
            self.assertTrue(generated_test.exists())
            self.assertEqual(check_pages.validate(root / 'src/config/applications.json'), ['analytics_full.overview'])
            form_source = scaffold_page._page_view_source(
                'form_probe', {'route': 'create', 'title': 'Create', 'icon': 'antd-file', 'order': 1},
                page_type='form',
            )
            self.assertIn("className='platform-template-form'", form_source)

    def test_preview_and_invalid_input_do_not_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scaffold_page.scaffold(root, 'analytics', 'Analytics', '', 1, ['Overview:overview'], dry_run=True)
            self.assertEqual(list(root.iterdir()), [])
            for module, pages in [('..', ['Page:page']), ('task_', ['Page:page']), ('app', []),
                                  ('app', ['Page:../page']), ('app', ['Page:first', 'Page:second'])]:
                with self.subTest(module=module, pages=pages), self.assertRaises(ValueError):
                    scaffold_page.scaffold(root, module, 'App', '', 1, pages)
            self.assertEqual(list(root.iterdir()), [])


class LoginTests(unittest.TestCase):
    def test_sidebar_preserves_navigation_and_collapse_contracts(self):
        from plotly.utils import PlotlyJSONEncoder

        from dash_view.framework.aside import render_aside_content

        menu = [
            {'component': 'SubMenu', 'props': {'key': '/person_', 'title': '用户画像'},
             'children': [{'component': 'Item', 'props': {'key': '/person_/personal_info'}}]},
            {'component': 'SubMenu', 'props': {'key': '/task_', 'title': '任务中心'}, 'children': []},
        ]
        layout = json.dumps(render_aside_content(SimpleNamespace(
            menu=menu, has_access=lambda permission: permission == '工作台-页面')), cls=PlotlyJSONEncoder)
        for identifier in ['main-menu', 'logo-text', 'platform-navigation', 'platform-navigation-close']:
            self.assertIn(identifier, layout)
        self.assertNotIn('/person_', layout)
        self.assertIn('/task_', layout)
        self.assertEqual(menu[0]['props']['key'], '/person_')
        self.assertNotIn('imgs/logo.png', layout)
        self.assertIn('digital-human-mark.svg', layout)
        self.assertIn('anita-wordmark.svg', layout)
        self.assertIn('"alt": "Anita"', layout)
        self.assertNotIn('platform-primary-link', layout)
        denied = json.dumps(render_aside_content(SimpleNamespace(
            menu=[], has_access=lambda permission: False)), cls=PlotlyJSONEncoder)
        self.assertNotIn('platform-primary-link', denied)

    def test_personal_center_layout_keeps_profile_and_security_contracts(self):
        from plotly.utils import PlotlyJSONEncoder
        from server import server
        from dash_view.application.person_.personal_info import render_content

        user = SimpleNamespace(user_name='profile_probe', user_full_name='Profile Probe', user_status=True,
                               user_sex='未知', user_email='probe@example.test', phone_number='555-0100',
                               user_remark='Isolated demo account')
        with server.test_request_context('/person_/personal_info'):
            with patch('dash_view.application.person_.personal_info.user_repository.get_user_info', return_value=[user]):
                layout = json.dumps(render_content(SimpleNamespace(user_name=user.user_name)),
                                    cls=PlotlyJSONEncoder, ensure_ascii=False)

        self.assertIn('personal-center-tabs', layout)
        self.assertIn('基本资料', layout)
        self.assertIn('账户安全', layout)
        for identifier in [
            'personal-info-avatar', 'personal-info-display-name', 'personal-info-user-full-name',
            'personal-info-user-full-name-save', 'personal-info-user-full-name-cancel',
            'personal-info-user-email-save', 'personal-info-phone-number-save',
            'personal-info-user-remark-save', 'personal-info-password-edit', 'personal-info-show-otp-modal',
        ]:
            self.assertIn(identifier, layout)
        self.assertNotIn('otpauth://', layout)
        from config.access_factory import AccessFactory
        self.assertEqual(AccessFactory.get_dict_access_meta2menu_item()['个人信息-页面'],
                         'person_.personal_info')

    def test_redirect_allows_only_local_paths_and_quotes_javascript(self):
        from dash_callback.pages.login_c import login_redirect_script
        from yarl import URL

        for target in ['https://example.org', '//example.org', '/\\example.org', 'javascript:alert(1)', '/\nexample']:
            href = str(URL('http://localhost/login').with_query(to=target))
            self.assertEqual(login_redirect_script(href), 'window.location.assign("/dashboard_/monitor");')
        target = "/example_app/subapp1?name='quoted'"
        href = str(URL('http://localhost/login').with_query(to=target))
        self.assertEqual(login_redirect_script(href), 'window.location.assign(' + json.dumps(target) + ');')

    def test_login_defaults_to_registered_monitor(self):
        from dash_callback.pages.login_c import login_redirect_script

        for href in [None, 'http://localhost/login', 'http://localhost/login?to=/',
                     'http://localhost/login?to=/login', 'http://localhost/login?to=/dashboard_/workbench']:
            with self.subTest(href=href):
                self.assertEqual(login_redirect_script(href), 'window.location.assign("/dashboard_/monitor");')

    def test_partial_otp_does_not_query_database(self):
        from dash_callback.pages.login_c import otp_login
        from dash.exceptions import PreventUpdate

        for value in [None, '', '123', 'abcdef', '１２３４５６']:
            with self.subTest(value=value), self.assertRaises(PreventUpdate):
                otp_login(value, 'someone', '/login')

    def test_login_layout_has_unique_component_ids(self):
        from server import server
        from dash_view.pages.login import render_content

        def collect_ids(component):
            if isinstance(component, (tuple, list)):
                return [identifier for child in component for identifier in collect_ids(child)]
            if not hasattr(component, 'to_plotly_json'):
                return []
            props = component.to_plotly_json()['props']
            identifiers = [props['id']] if 'id' in props else []
            identifiers.extend(collect_ids(props.get('children')))
            for item in props.get('items', []):
                if isinstance(item, dict):
                    identifiers.extend(collect_ids(item.get('children')))
            return identifiers

        with server.test_request_context('/'):
            identifiers = collect_ids(render_content())
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_login_uses_dynamic_scene_without_background_image(self):
        from plotly.utils import PlotlyJSONEncoder

        from dash_view.pages.login import render_content
        from server import server

        with server.test_request_context('/'):
            layout = json.dumps(render_content(), cls=PlotlyJSONEncoder, ensure_ascii=False)
        self.assertIn('login-pipeline', layout)
        self.assertIn('login-webgl', layout)
        self.assertIn('login-status', layout)
        self.assertIn('aria-live', layout)
        self.assertIn('"autoComplete": "on"', layout)
        self.assertNotIn('login_background', layout)

    def test_initial_loader_is_accessible_and_has_no_gif_reference(self):
        from common.utilities.util_dash import CustomDash

        app = CustomDash('loading_probe')
        markup = app.interpolate_index(metas='', title='Probe', css='', config='',
                                       scripts='', app_entry='', favicon='', renderer='')
        self.assertIn('id="react-entry-point"', markup)
        self.assertIn('role="status"', markup)
        self.assertIn('platform-loader', markup)
        self.assertIn('<noscript>', markup)
        stylesheet = (ROOT / 'src/assets/custom.css').read_text(encoding='utf-8')
        self.assertNotIn('ini_load_animation.gif', stylesheet)
        self.assertIn('prefers-reduced-motion', stylesheet)
        self.assertFalse((ROOT / 'src/assets/ini_load_animation.gif').exists())
        self.assertFalse((ROOT / 'src/assets/imgs/login_background.jpeg').exists())


class RoutingTests(unittest.TestCase):
    def test_shell_preserves_navigation_drawer_and_tabs_contracts(self):
        from plotly.utils import PlotlyJSONEncoder

        from dash_view.pages.main import render_content
        from server import app, server

        access = SimpleNamespace(menu=[], menu_items={'dashboard_.monitor'},
                                 has_access=lambda permission: True, user_name='ui_probe',
                                 user_info=SimpleNamespace(user_name='ui_probe', user_full_name='UI Probe'))
        with server.test_request_context('/'), patch(
                'database.sql_db.repositories.announcement_repository.get_announcement', return_value=None):
            layout = render_content(access, 'http://localhost/dashboard_/workbench')
            serialized = json.dumps(layout, cls=PlotlyJSONEncoder)
        self.assertEqual(layout.className, 'platform-shell')
        for identifier in ['platform-nav-backdrop', 'platform-navigation-close', 'menu-collapse-sider',
                           'btn-menu-collapse-sider-menu', 'tabs-container', 'agent-launcher', 'agent-panel']:
            self.assertIn(identifier, serialized)
        self.assertIn('var(--platform-tabs-height)', serialized)
        inputs = {(control['id'], control['property'])
                  for callback in app.callback_map.values() for control in callback.get('inputs', [])}
        for identifier in ['platform-nav-backdrop', 'platform-navigation-close']:
            self.assertIn((identifier, 'n_clicks'), inputs)

    def test_header_does_not_load_remote_star_badges(self):
        from plotly.utils import PlotlyJSONEncoder

        from dash_view.framework.head import render_head_content
        from server import server

        access = SimpleNamespace(user_name='ui_probe', user_info=SimpleNamespace(
            user_name='ui_probe', user_full_name='UI Probe'))
        with server.test_request_context('/'), patch(
                'database.sql_db.repositories.announcement_repository.get_announcement', return_value=None):
            header = json.dumps(render_head_content(access), cls=PlotlyJSONEncoder)
        self.assertNotIn('img.shields.io', header)
        self.assertNotIn('badge/star', header)
        self.assertIn('global-head-avatar', header)

    def test_only_registered_authorized_pages_are_resolved(self):
        from dash_callback.pages.main_c import resolve_page
        from dash_view.pages import page_403, page_404

        denied = SimpleNamespace(menu_items=set())
        self.assertIs(resolve_page('unknown.page', denied), page_404)
        self.assertIs(resolve_page('example_app.subapp1', denied), page_403)
        allowed = SimpleNamespace(menu_items={'example_app.subapp1'})
        self.assertEqual(resolve_page('example_app.subapp1', allowed).__name__, 'dash_view.application.example_app.subapp1')

    def test_retired_permissions_do_not_create_broken_menu_entries(self):
        from common.utilities.util_menu_access import MenuAccess

        self.assertEqual(MenuAccess.get_user_menu_items({'removed-permission', '工作台-页面', 'rpa_scheduler'}), {'task_.overview'})

    def test_old_workbench_urls_redirect_without_registering_a_page(self):
        import app  # noqa: F401
        from dash_callback.pages.main_c import parse_url
        from server import server

        self.assertEqual(parse_url('http://localhost/dashboard_/workbench')[0], '/dashboard_/monitor')
        response = server.test_client().get('/dashboard_/workbench')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/dashboard_/monitor')


class RpaTests(unittest.TestCase):
    def test_scheduler_status_hides_global_job_count_from_non_admins(self):
        from dash_callback.application.task_ import overview_c
        from server import server

        access = SimpleNamespace(user_name='viewer', has_access=lambda permission: False)
        status = {'state': 'running', 'status': {
            'process_id': 1234, 'started_at': '2026-10-09T10:00:00+08:00', 'job_count': 991231,
        }}
        with server.test_request_context('/'), patch.object(overview_c, 'get_menu_access', return_value=access), \
                patch.object(overview_c, '_scheduler_status_snapshot', return_value=status):
            rendered = overview_c.refresh_scheduler_status(1)
        text = ' '.join(str(child.children) for child in rendered.children)
        self.assertIn('调度服务运行中', text)
        self.assertNotIn('991231', text)

    def test_scheduler_startup_policy_save_rechecks_admin_permission_and_revision(self):
        from dash_callback.application.task_ import overview_c

        denied = SimpleNamespace(user_name='viewer', has_access=lambda permission: False)
        with patch.object(overview_c, 'save_scheduler_mode') as save:
            with self.assertRaises(PermissionError):
                overview_c._save_policy(denied, 'web_only', 0)
            save.assert_not_called()

        admin = SimpleNamespace(user_name='admin', has_access=lambda permission: permission == '调度启动配置')
        with patch.object(overview_c, 'load_startup_config', return_value={
                'revision': 2, 'scheduler_mode': 'web_only'}), \
                patch.object(overview_c, 'save_scheduler_mode') as save:
            with self.assertRaisesRegex(ValueError, '修改'):
                overview_c._save_policy(admin, 'paired_paused', 1)
            save.assert_not_called()

    def test_manual_run_uses_server_job_not_browser_script(self):
        from dash_callback.application.task_ import task_mgmt_c
        from server import server
        access = SimpleNamespace(has_access=lambda permission: True)
        with server.test_request_context('/'), patch.object(task_mgmt_c, 'get_menu_access', return_value=access), \
                patch('common.task_center.request_run', return_value={'status': 'submitted'}) as submit, \
                patch('dash.set_props'):
            task_mgmt_c.confirm_platform_action.__wrapped__(1, {'operation': 'run', 'task_id': 'trusted',
                'request_key': 'fixed-request', 'script_text': 'untrusted'})
            self.assertEqual(submit.call_args.args[1:], ('trusted', 'fixed-request'))
            self.assertNotIn('untrusted', str(submit.call_args))

    def test_unauthorized_operations_do_not_reach_scheduler(self):
        from common import task_center

        access = SimpleNamespace(user_name=None, has_access=lambda permission: False)
        with patch('common.utilities.util_apscheduler._add_job') as submit:
            with self.assertRaises(task_center.TaskCenterError):
                task_center.request_run(access, 'trusted', 'request')
            submit.assert_not_called()

    def test_offline_scheduler_keeps_previous_data(self):
        from dash_callback.application.task_ import task_mgmt_c
        import dash

        access = SimpleNamespace(has_access=lambda permission: True)
        with patch.object(task_mgmt_c, 'get_menu_access', return_value=access), \
                patch.object(task_mgmt_c, 'load_platform', side_effect=ConnectionError):
            result = task_mgmt_c.refresh_platform.__wrapped__(0, 1, '', '', '', '', 1, 20, {})
            self.assertIs(result[0], dash.no_update)
            self.assertEqual(result[-1].type, 'error')


class TaskPageTests(unittest.TestCase):
    def test_global_scheduler_policy_controls_render_only_for_admin_access(self):
        from dash_view.application.task_ import overview
        from server import server

        reader = SimpleNamespace(has_access=lambda permission: permission == 'rpa_scheduler')
        admin = SimpleNamespace(has_access=lambda permission: permission in {'rpa_scheduler', '调度启动配置'})
        with server.test_request_context('/'):
            reader_layout = overview.render_content(reader)
            admin_layout = overview.render_content(admin)
        self.assertNotIn('tc-scheduler-policy-mode', str(reader_layout))
        self.assertNotIn('tc-scheduler-keep-awake', str(reader_layout))
        self.assertIn('tc-scheduler-policy-mode', str(admin_layout))
        self.assertIn('tc-scheduler-keep-awake', str(admin_layout))

    def test_offline_table_renders_and_refresh_retains_data(self):
        import dash
        from dash_callback.application.task_ import task_mgmt_c
        from server import server

        from dash_view.application.task_ import task_mgmt
        with server.test_request_context('/'), patch.object(task_mgmt_c, 'load_platform', side_effect=ConnectionRefusedError):
            layout = task_mgmt.render_content(SimpleNamespace(user_info=None))
            self.assertEqual(layout.className, 'tc-page tc-platform-page')
            self.assertIs(task_mgmt_c.refresh_platform.__wrapped__(0, 1, '', '', '', '', 1, 20, {})[0], dash.no_update)


class RegistryTests(unittest.TestCase):
    def test_system_admin_receives_registered_template_permissions(self):
        from common.utilities.util_menu_access import MenuAccess
        from config.access_factory import AccessFactory

        with patch.object(AccessFactory, 'admin_access_meta', ('内置-权限',)), \
                patch.object(AccessFactory, 'get_dict_access_meta2menu_item',
                              return_value={'模板看板-页面': 'analytics.overview'}):
            self.assertEqual(
                set(MenuAccess.get_extra_access_meta(['admin'])),
                {'内置-权限', '模板看板-页面'},
            )

    def test_scheduler_policy_is_admin_only_and_unassignable(self):
        from config.access_factory import AccessFactory

        mapping = AccessFactory.get_dict_access_meta2menu_item()
        self.assertIsNone(mapping['调度启动配置'])
        self.assertIn('调度启动配置', AccessFactory.admin_access_meta)
        self.assertNotIn('调度启动配置', AccessFactory.assignable_access_meta)

    def test_all_registered_pages_have_valid_permissions(self):
        from config.access_factory import AccessFactory

        AccessFactory.cache_dict_access_meta2menu_item.clear()
        mapping = AccessFactory.get_dict_access_meta2menu_item()
        self.assertEqual(mapping['rpa_scheduler'], 'task_.overview')

    def test_permissions_are_checked_individually(self):
        from config.access_factory import AccessFactory

        first = SimpleNamespace(__name__='dash_view.application.first.page', access_metas=('shared', 'first'))
        second = SimpleNamespace(__name__='dash_view.application.second.page', access_metas=lambda: ('shared', 'second'))
        with patch.object(AccessFactory, 'views', [first, second]):
            AccessFactory.cache_dict_access_meta2menu_item.clear()
            with self.assertRaisesRegex(ValueError, 'Duplicate permission'):
                AccessFactory.get_dict_access_meta2menu_item()
        AccessFactory.cache_dict_access_meta2menu_item.clear()

    def test_registry_rejects_bad_paths_and_loads_pages(self):
        from config.access_factory import load_application_views

        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / 'applications.json'
            for entries in [['../page'], ['app.page', 'app.page'], {'page': 'app.page'}]:
                registry.write_text(json.dumps(entries), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_application_views(registry)
            registry.write_text('["example_app.subapp1"]', encoding='utf-8')
            self.assertEqual(load_application_views(registry)[0].__name__, 'dash_view.application.example_app.subapp1')


if __name__ == '__main__':
    unittest.main()