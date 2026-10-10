"""Compatibility query helper for the retired Windows scheduler page.

This module intentionally registers no Dash callbacks and performs no native task actions.
"""

from common import task_center
from common.utilities.util_menu_access import get_menu_access

TABLE_ID = 'windows-tasks-table'


class _LegacyAccess:
    def __init__(self, access):
        self.user_name = access.user_name
        self._access = access

    def has_access(self, permission):
        if permission == task_center.PAGE_PERMISSION:
            return self._access.has_access('Windows任務管理-页面')
        return self._access.has_access(permission)


def query_tasks(page=1, page_size=20, keyword='', status=''):
    """Return safe Windows snapshot rows; operational callbacks live under task_."""
    return task_center.list_tasks(_LegacyAccess(get_menu_access()), engine='windows', keyword=keyword,
                                  status=status, page=page, page_size=page_size)# 适用于显示本机端windows工作排程器

from server import app
from dash.dependencies import Input, Output, State
import dash
from dash_components.windows_tasks_manager import windows_tasks_manager
import feffery_antd_components as fac
import pandas as pd
import time
import socket
import getpass
import re
import io
import base64
from common.utilities.util_menu_access import get_menu_access
from functools import wraps
from dash.exceptions import PreventUpdate


def require_windows_access(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        if not get_menu_access().has_access('Windows任務管理-页面'):
            raise PreventUpdate
        return function(*args, **kwargs)
    return guarded

computer_name = socket.gethostname()
user_name = getpass.getuser()
author = f"{computer_name.upper()}\\{user_name}"

# 1. Query Tasks (Button Click & Interval)
@app.callback(
    [Output('windows-tasks-table', 'data'),
     Output('windows-tasks-last-update-time', 'children')],
    [Input('windows-tasks-query-button', 'nClicks'),
     Input('windows-tasks-auto-query-interval', 'n_intervals')],
    prevent_initial_call=False
)
@require_windows_access
def query_tasks(nClicks, n_intervals):
    # Logic to fetch tasks
    try:
        tasks = windows_tasks_manager.get_scheduled_tasks()
        if not tasks:
            current_time = time.strftime("%Y-%m-%d %H:%M:%S")
            return [], f"最后更新: {current_time}"
        # Filter out temporary tasks
        tasks = [task for task in tasks if not re.search(r'-\{[A-F0-9-]+\}', task.get('TaskName', ''))]
        
        # Add key for AntdTable and operation column
        for i, task in enumerate(tasks):
            task['key'] = task.get('TaskName', str(i))
            
            # Format Status
            status_raw = task.get('Status', '')
            if status_raw == 'Ready':
                task['Status'] = {'status': 'success', 'text': '就绪'}
            elif status_raw == 'Running':
                task['Status'] = {'status': 'processing', 'text': '运行中'}
            elif status_raw == 'Disabled':
                task['Status'] = {'status': 'default', 'text': '已禁用'}
            else:
                task['Status'] = {'status': 'default', 'text': status_raw or '未知'}
                
            # Format Last Result
            last_result = task.get('Last Result', '')
            if last_result == '0':
                task['Last Result'] = '成功'
            elif last_result in ('', None):
                task['Last Result'] = ''
            elif last_result == '267009':
                task['Last Result'] = '运行中'
            elif last_result == '267011':
                task['Last Result'] = '尚未运行'
            else:
                task['Last Result'] = f'失败 ({last_result})'
                
            # Format Operation Buttons (Compact)
            task['operation'] = [
                {
                    'content': '启动',
                    'type': 'link',
                    'custom': 'start',
                },
                {
                    'content': '启用' if status_raw == 'Disabled' else '暂停',
                    'type': 'link',
                    'custom': 'enable' if status_raw == 'Disabled' else 'pause',
                    'style': {'color': '#faad14'}
                },
                {
                    'content': '删除',
                    'type': 'link',
                    'danger': True,
                    'custom': 'delete',
                }
            ]
            
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        return tasks, f"最后更新: {current_time}"
    except Exception as e:
        return dash.no_update, f"更新失败: {str(e)}"

# 2. Handle Table Actions (Start, Pause, Delete)
@app.callback(
    [Output('windows-tasks-message', 'children'),
     Output('windows-tasks-confirm-modal', 'visible'),
     Output('windows-tasks-confirm-modal', 'children'),
     Output('windows-tasks-current-action-store', 'data')],
    Input('windows-tasks-table', 'nClicksButton'),
    State('windows-tasks-table', 'clickedCustom'),
    State('windows-tasks-table', 'recentlyButtonClickedRow'),
    prevent_initial_call=True
)
@require_windows_access
def handle_table_actions(nClicksButton, clickedCustom, row):
    # 注意：nClicksButton 在 fac.AntdTable 中是「计数」(int)，不能当 dict 用
    if not clickedCustom or not isinstance(clickedCustom, str):
        return [dash.no_update] * 4

    row = row or {}
    if not isinstance(row, dict):
        row = {}
    task_name = row.get('TaskName')
    if not task_name:
        return [dash.no_update] * 4

    button_custom = clickedCustom

    if button_custom == 'start':
        try:
            windows_tasks_manager.start_task(task_name)
            return fac.AntdMessage(content=f"任务 '{task_name}' 已启动", type='success'), False, dash.no_update, dash.no_update
        except Exception as e:
            return fac.AntdMessage(content=f"启动失败: {str(e)}", type='error'), False, dash.no_update, dash.no_update
            
    elif button_custom == 'enable':
        try:
            windows_tasks_manager.ready_task(task_name)
            return fac.AntdMessage(content=f"任务 '{task_name}' 已启用", type='success'), False, dash.no_update, dash.no_update
        except Exception as error:
            return fac.AntdMessage(content=f'启用失败: {error}', type='error'), False, dash.no_update, dash.no_update

    elif button_custom == 'pause':
        try:
            windows_tasks_manager.pause_task(task_name)
            return fac.AntdMessage(content=f"任务 '{task_name}' 已暂停", type='warning'), False, dash.no_update, dash.no_update
        except Exception as e:
            return fac.AntdMessage(content=f"暂停失败: {str(e)}", type='error'), False, dash.no_update, dash.no_update
            
    elif button_custom == 'delete':
        return (
            dash.no_update,
            True,
            f"确定要删除任务 '{task_name}' 吗？此操作不可恢复。",
            {'action': 'delete', 'task_name': task_name},
        )

    return [dash.no_update] * 4

# 3. Confirm Delete
@app.callback(
    [Output('windows-tasks-message', 'children', allow_duplicate=True),
     Output('windows-tasks-confirm-modal', 'visible', allow_duplicate=True),
     Output('windows-tasks-query-button', 'nClicks')], # Trigger refresh
    Input('windows-tasks-confirm-modal', 'okCounts'),
    State('windows-tasks-current-action-store', 'data'),
    State('windows-tasks-query-button', 'nClicks'),
    prevent_initial_call=True
)
@require_windows_access
def confirm_delete(okCounts, action_data, current_nClicks):
    if not action_data or action_data.get('action') != 'delete':
        return [dash.no_update] * 3
        
    task_name = action_data.get('task_name')
    try:
        windows_tasks_manager.delete_task(task_name)
        return fac.AntdMessage(content=f"任务 '{task_name}' 已删除", type='success'), False, (current_nClicks or 0) + 1
    except Exception as e:
        return fac.AntdMessage(content=f"删除失败: {str(e)}", type='error'), False, dash.no_update

# 4. Download Excel
@app.callback(
    Output('windows-tasks-download', 'data'),
    Input('windows-tasks-download-button', 'nClicks'),
    State('windows-tasks-table', 'data'),
    prevent_initial_call=True
)
@require_windows_access
def download_excel(nClicks, tasks):
    if nClicks and tasks:
        df = pd.DataFrame(tasks)
        # Remove internal keys
        if 'key' in df.columns:
            df = df.drop(columns=['key'])
        if 'operation' in df.columns:
            df = df.drop(columns=['operation'])

        # Prefer real xlsx if openpyxl is available, otherwise fallback to csv
        try:
            import openpyxl  # noqa: F401

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            content = base64.b64encode(buf.getvalue()).decode('utf-8')
            return {
                'content': content,
                'filename': 'windows_tasks.xlsx',
                'base64': True,
            }
        except Exception:
            csv_string = df.to_csv(index=False)
            return {
                'content': csv_string,
                'filename': 'windows_tasks.csv',
            }
    return None

# 5. Open Scheduler
@app.callback(
    Output('windows-tasks-message', 'children', allow_duplicate=True),
    Input('windows-tasks-open-scheduler-button', 'nClicks'),
    prevent_initial_call=True
)
@require_windows_access
def open_scheduler(nClicks):
    if nClicks:
        try:
            windows_tasks_manager.open_task_scheduler()
            return fac.AntdMessage(content="任务调度器已打开", type='success')
        except Exception as e:
            return fac.AntdMessage(content=f"打开失败: {str(e)}", type='error')
    return dash.no_update
