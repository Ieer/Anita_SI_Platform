import ast
import json
import re
import sys
import unittest
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))


def load_catalogs():
    catalogs = {}
    for path in (SRC / 'translations').rglob('*.json'):
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f'{path.name}: duplicate translation key {key!r}')
                result[key] = value
            return result

        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
        catalogs['t__' + data.get('topic', 'default')] = data['contents']
    return catalogs


class LocaleIntegrityTests(unittest.TestCase):
    def test_catalogs_have_unique_keys_english_and_matching_placeholders(self):
        formatter = Formatter()
        for topic, entries in load_catalogs().items():
            for source, translations in entries.items():
                with self.subTest(topic=topic, source=source):
                    english = translations.get('en-us')
                    self.assertIsInstance(english, str)
                    self.assertTrue(english.strip())
                    source_fields = [field for _, field, _, _ in formatter.parse(source) if field is not None]
                    english_fields = [field for _, field, _, _ in formatter.parse(english) if field is not None]
                    self.assertCountEqual(source_fields, english_fields)
                    # ServerChan is a service name, not untranslated UI prose.
                    self.assertIsNone(re.search(r'[\u4e00-\u9fff]', english.replace('Server酱', 'ServerChan')))

    def test_literal_translation_calls_have_catalog_entries(self):
        catalogs = load_catalogs()
        for folder in ('dash_view', 'dash_callback', 'dash_components'):
            for path in (SRC / folder).rglob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8-sig'))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                        continue
                    topic = node.func.id
                    if topic not in catalogs or not node.args:
                        continue
                    argument = node.args[0]
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        with self.subTest(file=str(path.relative_to(SRC)), line=node.lineno):
                            self.assertIn(argument.value, catalogs[topic])

    def test_ui_translations_are_not_frozen_at_callback_registration(self):
        for folder in ('dash_view', 'dash_callback', 'dash_components'):
            for path in (SRC / folder).rglob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8-sig'))
                parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) \
                            or not node.func.id.startswith('t__'):
                        continue
                    ancestor = parents.get(node)
                    while ancestor is not None and not isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        ancestor = parents.get(ancestor)
                    with self.subTest(file=str(path.relative_to(SRC)), line=node.lineno):
                        self.assertIsNotNone(ancestor, 'Evaluate translations inside a request, not at import time')
                        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self.assertGreaterEqual(node.lineno, ancestor.body[0].lineno,
                                                    'Callback decorators and defaults run before requests')

    def test_registered_menu_titles_and_permissions_are_translated(self):
        catalogs = load_catalogs()['t__access']
        tree = ast.parse((SRC / 'config' / 'access_factory.py').read_text(encoding='utf-8'))
        modules = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('dash_view.application.'):
                for alias in node.names:
                    modules[alias.asname or alias.name] = node.module + '.' + alias.name
        registered = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in ('views', 'apps') for target in node.targets
            ) and isinstance(node.value, ast.List):
                registered.extend(modules[item.id] for item in node.value.elts
                                  if isinstance(item, ast.Name) and item.id in modules)
        self.assertGreaterEqual(len(registered), 16)
        for module in registered:
            path = SRC.joinpath(*module.split('.')).with_suffix('.py')
            page = ast.parse(path.read_text(encoding='utf-8-sig'))
            title = next(node.value.value for node in page.body
                         if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                         and any(isinstance(target, ast.Name) and target.id == 'title' for target in node.targets))
            self.assertIn(title, catalogs, module)
            parent = ast.parse(path.with_name('__init__.py').read_text(encoding='utf-8-sig'))
            for node in parent.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and any(
                    isinstance(target, ast.Name) and target.id == 'title' for target in node.targets
                ):
                    self.assertIn(node.value.value, catalogs, module)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id.endswith('access_meta') for target in node.targets
            ) and isinstance(node.value, (ast.Tuple, ast.List)):
                for item in node.value.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        self.assertIn(item.value, catalogs)

    def test_registered_ui_has_no_direct_chinese_component_labels(self):
        paths = [
            *(SRC / 'dash_view' / 'pages').glob('*.py'),
            *(SRC / 'dash_view' / 'framework').glob('*.py'),
        ]
        for folder in ('access_', 'dashboard_', 'person_', 'message_', 'task_', 'setting_', 'example_app'):
            paths.extend((SRC / 'dash_view' / 'application' / folder).glob('*.py'))
            paths.extend((SRC / 'dash_callback' / 'application' / folder).glob('*.py'))
        display_properties = {
            'title', 'subTitle', 'placeholder', 'label', 'message', 'description',
            'okText', 'cancelText', 'children', 'content',
        }
        for path in paths:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                arguments = [kw.value for kw in node.keywords if kw.arg in display_properties]
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id in ('html', 'fac', 'fuc'):
                    arguments.extend(node.args[:1])
                for argument in arguments:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        if path.name == 'lang.py' and argument.value == 'English / 中文':
                            continue
                        with self.subTest(file=str(path.relative_to(SRC)), line=node.lineno):
                            self.assertIsNone(re.search(r'[\u4e00-\u9fff]', argument.value),
                                              'Wrap system display text in a request-scoped translator')

    def test_shared_chrome_and_error_pages_follow_each_request_locale(self):
        from flask import Flask
        from plotly.utils import PlotlyJSONEncoder
        from dash_view.pages import page_403, page_404, page_500
        from dash_view.framework import func_main

        server = Flask(__name__)
        for locale, expected, unexpected in [
            ('en-us', 'Page not found', '页面不存在'),
            ('zh-cn', '页面不存在', 'Page not found'),
            ('en-us', 'Page not found', '页面不存在'),
        ]:
            with server.test_request_context('/', headers={'Cookie': f'dash-i18n={locale}'}):
                output = json.dumps([page_403.render_content(), page_404.render_content(),
                                     page_500.render_content('RAW_ERROR_123'), func_main.render()],
                                    cls=PlotlyJSONEncoder, ensure_ascii=False)
                self.assertIn(expected, output)
                self.assertNotIn(unexpected, output)
                self.assertIn('RAW_ERROR_123', output)
                if locale == 'en-us':
                    self.assertIsNone(re.search(r'[\u4e00-\u9fff]', output))

    def test_default_feedback_is_localized_without_translating_custom_data(self):
        from flask import Flask
        from dash_components import feedback

        server = Flask(__name__)
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}), \
                patch.object(feedback, 'set_props') as setter:
            feedback.MessageManager.success()
            self.assertEqual(setter.call_args.args[1]['children']['props']['content'], 'Request successful')
            feedback.MessageManager.error(content='用户原始数据')
            self.assertEqual(setter.call_args.args[1]['children']['props']['content'], '用户原始数据')

    def test_locale_defaults_and_initial_loader_follow_cookie(self):
        from flask import Flask
        from i18n import get_current_locale, t__default
        from common.utilities.util_dash import CustomDash

        self.assertEqual(get_current_locale(), 'zh-cn')
        server = Flask(__name__)
        renderer = CustomDash('locale_loader_probe')
        for locale, label in [('en-us', 'Loading the platform'), ('zh-cn', '正在加载平台')]:
            with server.test_request_context('/', headers={'Cookie': f'dash-i18n={locale}'}):
                self.assertEqual(get_current_locale(), locale)
                markup = renderer.interpolate_index(
                    metas='', title='Probe', css='', config='', scripts='',
                    app_entry='', favicon='', renderer='')
                self.assertIn(label, markup)
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=invalid'}):
            self.assertEqual(get_current_locale(), 'zh-cn')
            self.assertEqual(t__default('页面不存在'), '页面不存在')

    def test_root_component_locale_follows_request(self):
        import app as platform_app

        for locale in ('en-us', 'zh-cn', 'en-us'):
            with platform_app.server.test_request_context('/', headers={'Cookie': f'dash-i18n={locale}'}):
                self.assertEqual(platform_app.app.layout().locale, locale)

    def test_access_display_translates_enums_but_preserves_identity_and_keys(self):
        from datetime import datetime
        from flask import Flask
        from dash_callback.application.access_ import user_mgmt_c, role_mgmt_c

        server = Flask(__name__)
        now = datetime.now()
        user = SimpleNamespace(user_name='自定义用户', user_roles=['自定义角色'], user_sex='未知',
                               user_status=True, update_datetime=now, create_datetime=now)
        role = SimpleNamespace(role_name='自定义角色', access_metas=['任务管理-页面'], role_status=True,
                               update_datetime=now, create_datetime=now)
        with server.test_request_context('/', headers={'Cookie': 'dash-i18n=en-us'}), \
                patch.object(user_mgmt_c.user_repository, 'get_user_info', return_value=[user]), \
                patch.object(user_mgmt_c.MenuAccess, 'get_extra_access_meta', return_value=['任务管理-页面']), \
                patch.object(role_mgmt_c.user_repository, 'get_role_info', return_value=[role]):
            user_row = user_mgmt_c.get_data()[0]
            role_row = role_mgmt_c.get_data()[0]
        self.assertEqual(user_row['key'], '自定义用户')
        self.assertEqual(user_row['user_sex'], 'Unknown')
        self.assertIn('自定义角色', user_row['user_roles'])
        self.assertNotIn('任务管理-页面', user_row['user_roles'])
        self.assertNotIn('任务管理-页面', role_row['access_metas'])
        self.assertEqual(role.access_metas, ['任务管理-页面'])
        self.assertEqual(user.user_sex, '未知')

    def test_login_feedback_and_progress_labels_are_request_scoped(self):
        from flask import Flask
        from plotly.utils import PlotlyJSONEncoder
        from dash_callback.pages import login_c
        from dash_view.pages import login

        server = Flask(__name__)
        for locale, error, progress in [
            ('en-us', 'Too many attempts', 'Verifying account'),
            ('zh-cn', '尝试次数过多', '正在验证账号'),
        ]:
            with server.test_request_context('/', headers={'Cookie': f'dash-i18n={locale}'}), \
                    patch.object(login_c, 'allow_login_attempt', return_value=False):
                result = login_c.login(1, None, None, 'test-user', 'not-empty', False,
                                       None, None, 0, {'display': 'none'}, False, '/login')
                serialized = json.dumps(result[1], cls=PlotlyJSONEncoder, ensure_ascii=False)
                self.assertIn(error, serialized)
                rendered = json.dumps(login.render_content(), cls=PlotlyJSONEncoder, ensure_ascii=False)
                self.assertIn(progress, rendered)


if __name__ == '__main__':
    unittest.main()
