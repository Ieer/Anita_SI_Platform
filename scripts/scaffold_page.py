"""Generate registered Dash applications without overwriting existing files."""

import argparse
import json
import keyword
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _page_view_source(module, page):
    root_id = f'{module}-{page["route"]}-content'
    callback_module = f'dash_callback.application.{module}.{page["route"]}_c'
    template = '''import feffery_antd_components as fac
from dash import html
from common.utilities.util_menu_access import MenuAccess
import __CALLBACK_MODULE__  # noqa
from __CALLBACK_MODULE__ import SAMPLE_ROWS

title = __TITLE__
icon = __ICON__
order = __ORDER__
access_metas = (__PERMISSION__,)


def render_content(menu_access: MenuAccess, **kwargs):
    attention_count = sum(row['status_key'] == 'attention' for row in SAMPLE_ROWS)
    active_count = sum(row['status_key'] == 'active' for row in SAMPLE_ROWS)
    return html.Main([
        html.Header([
            html.Div([
                html.Span('OPERATIONS / TEMPLATE', className='platform-template-kicker'),
                html.H1(title, className='platform-template-title'),
            ]),
            html.Div('示例数据', className='platform-template-sample-tag'),
        ], className='platform-template-heading'),
        html.Section([
            html.Article([
                html.Span(label, className='platform-template-metric-label'),
                html.Strong(value, className='platform-template-metric-value'),
                html.Span(note, className='platform-template-metric-note'),
            ], className='platform-template-metric')
            for label, value, note in [
                ('记录总数', str(len(SAMPLE_ROWS)), '当前数据集'),
                ('待处理', str(attention_count), '需要关注'),
                ('处理中', str(active_count), '正在跟进'),
                ('数据时间', '09:30', '示例快照'),
            ]
        ], className='platform-template-metrics', **{'aria-label': '关键指标'}),
        html.Section([
            html.Label('搜索记录', htmlFor='__SEARCH_ID__'),
            fac.AntdInput(id='__SEARCH_ID__', placeholder='名称、编号或负责人', allowClear=True,
                          prefix=fac.AntdIcon(icon='antd-search')),
            html.Label('处理状态', htmlFor='__FILTER_ID__'),
            fac.AntdSelect(id='__FILTER_ID__', value='all', options=[
                {'label': '全部状态', 'value': 'all'}, {'label': '待处理', 'value': 'attention'},
                {'label': '处理中', 'value': 'active'}, {'label': '正常', 'value': 'normal'},
            ], style={'width': '100%'}),
            fac.AntdButton('重置', id='__RESET_ID__', type='default',
                           icon=fac.AntdIcon(icon='antd-reload')),
        ], className='platform-template-filters', **{'aria-label': '记录筛选'}),
        html.Section([
            html.Div([
                html.H2('记录列表', className='platform-template-section-title'),
                html.Span(f'展示 {len(SAMPLE_ROWS)} 条示例记录', id='__STATUS_ID__', role='status'),
            ], className='platform-template-table-heading'),
            fac.AntdTable(
                id='__TABLE_ID__',
                columns=[
                    {'title': '记录编号', 'dataIndex': 'record_id', 'width': 160},
                    {'title': '名称', 'dataIndex': 'name'},
                    {'title': '处理状态', 'dataIndex': 'status', 'width': 130},
                    {'title': '负责人', 'dataIndex': 'owner', 'width': 140},
                    {'title': '更新时间', 'dataIndex': 'updated_at', 'width': 170},
                ],
                data=SAMPLE_ROWS, size='small', bordered=False,
                pagination={'pageSize': 8, 'showSizeChanger': False},
            ),
        ], className='platform-template-table-section'),
    ], id='__ROOT_ID__', className='platform-template-page')
'''
    replacements = {
        '__CALLBACK_MODULE__': callback_module,
        '__TITLE__': repr(page['title']),
        '__ICON__': repr(page['icon']),
        '__ORDER__': str(page['order']),
        '__PERMISSION__': repr(page['title'] + '-页面'),
        '__SEARCH_ID__': root_id + '-search',
        '__FILTER_ID__': root_id + '-status-filter',
        '__RESET_ID__': root_id + '-reset',
        '__STATUS_ID__': root_id + '-result-status',
        '__TABLE_ID__': root_id + '-table',
        '__ROOT_ID__': root_id,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def _page_callback_source(page):
    template = '''"""Deterministic sample data and permission-checked page callbacks."""

from dash import Input, Output
from dash.exceptions import PreventUpdate
from common.utilities.util_menu_access import get_menu_access
from server import app


SAMPLE_ROWS = [
    {'record_id': 'DEMO-001', 'name': '接口延迟告警', 'status': '待处理',
     'status_key': 'attention', 'owner': '值班组', 'updated_at': '2026-10-10 09:30'},
    {'record_id': 'DEMO-002', 'name': '归档校验', 'status': '正常',
     'status_key': 'normal', 'owner': '数据组', 'updated_at': '2026-10-10 09:18'},
    {'record_id': 'DEMO-003', 'name': '人工确认队列', 'status': '处理中',
     'status_key': 'active', 'owner': '值班组', 'updated_at': '2026-10-10 09:12'},
]


@app.callback(
    Output('__ROOT_ID__-table', 'data'),
    Output('__ROOT_ID__-result-status', 'children'),
    Input('__ROOT_ID__-search', 'value'),
    Input('__ROOT_ID__-status-filter', 'value'),
)
def filter_sample_rows(search, status):
    access = get_menu_access()
    if not access.has_access(__PERMISSION__):
        raise PreventUpdate
    query = (search or '').strip().casefold()
    rows = [row for row in SAMPLE_ROWS
            if (status in (None, '', 'all') or row['status_key'] == status)
            and (not query or any(query in str(row[key]).casefold()
                                  for key in ('record_id', 'name', 'owner')))]
    label = f'筛选结果 {len(rows)} 条' if query or status not in (None, '', 'all') else f'展示 {len(rows)} 条示例记录'
    return rows, label


@app.callback(
    Output('__ROOT_ID__-search', 'value'),
    Output('__ROOT_ID__-status-filter', 'value'),
    Input('__ROOT_ID__-reset', 'nClicks'),
    prevent_initial_call=True,
)
def reset_sample_filters(_clicks):
    access = get_menu_access()
    if not access.has_access(__PERMISSION__):
        raise PreventUpdate
    return '', 'all'
'''
    return template.replace('__ROOT_ID__', f'{page["route"]}-content').replace(
        '__PERMISSION__', repr(page['title'] + '-页面'))


def validate_identifier(value):
    if not value.isascii() or not value.isidentifier() or keyword.iskeyword(value) or value.startswith('_'):
        raise ValueError(f'Invalid module identifier: {value!r}')
    return value


def parse_subpages(sp_list):
    result = []
    for raw in sp_list:
        parts = raw.split(':')
        if not 2 <= len(parts) <= 4 or not parts[0].strip():
            raise ValueError(f'Expected title:page[:order[:icon]], got {raw!r}')
        name = validate_identifier(parts[1])
        order = int(parts[2]) if len(parts) >= 3 else 1
        title = parts[0].strip()
        if len(title + '-页面') > 32:
            raise ValueError('Page permission names must not exceed 32 characters')
        result.append(dict(title=title, route=name, order=order,
                           icon=parts[3] if len(parts) == 4 else 'antd-file', filename=f'{name}.py'))
    if not result:
        raise ValueError('At least one --subpage is required')
    if len({page['route'] for page in result}) != len(result) or len({page['title'] for page in result}) != len(result):
        raise ValueError('Duplicate page names or titles')
    return result


def scaffold(root, module, title, icon, order, subpages, dry_run=False):
    module = validate_identifier(module)
    if module.endswith('_'):
        raise ValueError('Names ending in _ are reserved for built-in applications')
    if not title.strip():
        raise ValueError('Application title must not be empty')
    pages = parse_subpages(subpages)
    root = Path(root)
    view_dir = root / 'src' / 'dash_view' / 'application' / module
    callback_dir = root / 'src' / 'dash_callback' / 'application' / module
    if view_dir.exists() or callback_dir.exists():
        raise ValueError(f'Application already exists: {module}')
    registry_path = root / 'src' / 'config' / 'applications.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8')) if registry_path.exists() else []
    if not isinstance(registry, list) or not all(isinstance(entry, str) for entry in registry):
        raise ValueError('applications.json must be a list of module paths')
    paths = [f'{module}.{page["route"]}' for page in pages]
    if set(paths) & set(registry):
        raise ValueError('Application pages are already registered')
    files = {
        view_dir / '__init__.py': f'title = {title!r}\nicon = {icon!r}\norder = {order}\n',
        callback_dir / '__init__.py': '',
    }
    for page in pages:
        name = page['route']
        files[view_dir / page['filename']] = _page_view_source(module, page)
        files[callback_dir / f'{name}_c.py'] = _page_callback_source(page)
    files[registry_path] = json.dumps(registry + paths, ensure_ascii=False, indent=2) + '\n'
    for path, content in files.items():
        if path.suffix == '.py':
            compile(content, str(path), 'exec')
    if not dry_run:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
    return list(files)


def main():
    parser = argparse.ArgumentParser(description='Create Dash pages, callbacks and application registration')
    parser.add_argument('--module', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--icon', default='antd-appstore')
    parser.add_argument('--order', type=int, default=10)
    parser.add_argument('--subpage', action='append', required=True, help='title:page[:order[:icon]]')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing files')
    args = parser.parse_args()
    try:
        files = scaffold(ROOT, args.module, args.title, args.icon, args.order, args.subpage, args.dry_run)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    for path in files:
        print(('PLAN ' if args.dry_run else 'CREATED ') + str(path.relative_to(ROOT)))
    print('Restart the web process and grant page permissions to the intended roles.')


if __name__ == '__main__':
    main()