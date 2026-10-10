import argparse
import json
import os
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def serve(port=8097):
    sys.path.insert(0, str(ROOT / 'src'))
    from peewee import SqliteDatabase
    from database.sql_db import conn

    with tempfile.TemporaryDirectory(prefix='task-center-demo-') as folder, ExitStack() as stack:
        database = SqliteDatabase(Path(folder) / 'demo.db', timeout=20)
        stack.enter_context(patch.object(conn, 'sqlite_db', database))
        stack.enter_context(patch.object(conn, 'db', return_value=database))
        conn.create_rds_table()
        conn.init_rds_data()
        from database.sql_db.dao import dao_task_center
        from database.sql_db.entity.table_user import SysGroup, SysGroupUser, SysUser, SysUserRole
        from common import task_center
        from common.utilities import util_apscheduler
        from dash_components.windows_tasks_manager import WindowsTasksManager

        now = datetime.now()
        values = dict(SysUser.get_by_id('admin').__data__)
        values.update(user_name='demo_admin', user_full_name='演示管理员')
        SysUser.create(**values)
        SysUserRole.create(user_name='demo_admin', role_name='admin')
        for name in ['运营自动化', '数据工程']:
            SysGroup.get_or_create(group_name=name, defaults={
                'group_status': True, 'group_remark': '演示环境', 'create_by': 'demo_admin',
                'update_by': 'demo_admin', 'create_datetime': now, 'update_datetime': now})
            SysGroupUser.get_or_create(group_name=name, user_name='demo_admin', defaults={'is_admin': 1})
        backend_rows = []
        names = ['订单对账', '库存同步', '日报生成', '票据归档', '远程数据校验', '供应商结算', '质量巡检', '报表推送']
        for index, name in enumerate(names):
            payload = {'type': 'ssh' if index == 4 else 'local', 'script_text': "print('DEMO ONLY')",
                       'script_type': 'Python', 'timeout': 300, 'extract_names': None,
                       'notify_channels': None, 'env_vars': None}
            if payload['type'] == 'ssh':
                payload.update(host='demo-host.invalid', port=22, username='demo', password='demo-only')
            enabled = index != 5
            backend_rows.append({'id': name, 'status': enabled, 'trigger': 'cron',
                'plan': {'second': '0', 'minute': str(index * 5), 'hour': '*', 'day': '*',
                         'month': '*', 'day_of_week': '*', 'timezone': 'Asia/Shanghai'},
                'next_run_time': (now.astimezone() + timedelta(minutes=5 + index * 5)).isoformat()
                                 if enabled else '', 'kwargs': payload})
            task = dao_task_center.create_task(f'demo-task-{index}', 'platform', name,
                '数据工程' if index in {1, 4} else '运营自动化', digest=task_center._digest(payload), enabled=enabled)
            for attempt in range(3):
                run_id = f'demo-run-{index}-{attempt}'
                run, _ = dao_task_center.reserve_run(task.task_id, run_id, run_id, 'manual', 'demo_admin', task.digest)
                started = now - timedelta(hours=attempt * 8 + index, minutes=3)
                from database.sql_db.entity.table_task_center import TaskRun
                TaskRun.update(started_at=started, requested_at=started).where(TaskRun.run_id == run_id).execute()
                dao_task_center.append_log(run_id, f'[DEMO] {name}\n[INFO] batch={index:03}-{attempt}\n')
                dao_task_center.append_log(run_id, '[INFO] read 240 records\n[INFO] validated schema\n')
                status = 'failed' if index == 3 and attempt == 2 else 'succeeded'
                if index == 6 and attempt == 2:
                    status = 'unknown'
                dao_task_center.append_log(run_id, '[INFO] ' + status + '\n')
                dao_task_center.finish_run(run_id, status, 'isolated demo fixture')
                if status != 'unknown':
                    TaskRun.update(finished_at=started + timedelta(seconds=22 + index * 7), duration=22 + index * 7).where(
                        TaskRun.run_id == run_id).execute()
        native_rows = [{'TaskName': '\\Demo\\' + name, 'Status': 'Ready',
                        'Next Run Time': (now + timedelta(hours=index + 1)).isoformat(),
                        'Last Result': '0', 'Schedule Type': 'Daily',
                        'Principal': 'DEMO\\service', 'MultipleInstancesPolicy': 'IgnoreNew'}
                       for index, name in enumerate(['文件归档', '每日备份', '数据导出'])]
        for index, native in enumerate(native_rows):
            dao_task_center.create_task(f'demo-windows-{index}', 'windows', native['TaskName'],
                                        '运营自动化', enabled=True)

        running_task = dao_task_center.get_task('demo-task-7')
        running, _ = dao_task_center.reserve_run(running_task.task_id, 'demo-live-run',
            'demo-live-request', 'manual', 'demo_admin', running_task.digest)
        dao_task_center.mark_submitted(running.run_id)
        TaskRun.update(status='running', started_at=now - timedelta(minutes=3),
                       requested_at=now - timedelta(minutes=3)).where(TaskRun.run_id == running.run_id).execute()
        dao_task_center.append_log(running.run_id, '[DEMO] Simulated ongoing report delivery. No external action.\n')

        def all_jobs():
            return [SimpleNamespace(job_id=row['id'], status=row['status'], trigger=row['trigger'],
                plan=row['plan'], job_next_run_time=row['next_run_time'], **row['kwargs']) for row in backend_rows]

        def add_job(trigger, trigger_args, job_kwargs, job_id, is_pause=False, replace_existing=False):
            run_id = job_kwargs.get('run_id')
            if run_id:
                task_id = job_kwargs['task_id']
                dao_task_center.begin_execution(job_kwargs['source_job_id'], task_id=task_id,
                    run_id=run_id, source=job_kwargs['source'], actor='demo_admin')
                dao_task_center.append_log(run_id, '[DEMO] Simulated execution only. No script, SSH or native task executed.\n')
                dao_task_center.finish_run(run_id, 'succeeded', 'isolated demo executor')
            else:
                previous = next((row for row in backend_rows if row['id'] == job_id), None)
                if previous and not replace_existing:
                    raise ValueError('任务名称已存在')
                if previous:
                    backend_rows.remove(previous)
                backend_rows.append({'id': job_id, 'status': not is_pause, 'trigger': trigger,
                    'plan': trigger_args, 'next_run_time': '', 'kwargs': job_kwargs})
            return job_id

        def toggle(job_id, enabled, trigger):
            row = next(row for row in backend_rows if row['id'] == job_id)
            row['status'] = enabled

        def remove(job_id, trigger):
            backend_rows[:] = [row for row in backend_rows if row['id'] != job_id]

        stack.enter_context(patch.object(task_center, '_platform_job_rows', side_effect=lambda: backend_rows))
        stack.enter_context(patch.object(util_apscheduler, 'get_apscheduler_all_jobs', side_effect=all_jobs))
        stack.enter_context(patch.object(util_apscheduler, '_add_job', side_effect=add_job))
        stack.enter_context(patch.object(util_apscheduler, 'start_stop_job', side_effect=toggle))
        stack.enter_context(patch.object(util_apscheduler, 'remove_job', side_effect=remove))
        stack.enter_context(patch.object(util_apscheduler, 'get_scheduler_status', side_effect=lambda **kwargs: {
            'service': 'si-platform-scheduler', 'ready': True, 'state': 'running',
            'process_id': os.getpid(), 'started_at': now.astimezone().isoformat(),
            'job_count': len(backend_rows),
        }))
        from common.utilities import util_startup

        startup = {'schema': 1, 'revision': 0, 'scheduler_mode': 'paired_paused'}

        def save_demo_policy(mode, revision, *args, **kwargs):
            if revision != startup['revision']:
                raise util_startup.StartupConfigError('Demo startup policy changed')
            if mode not in util_startup.STARTUP_MODES:
                raise util_startup.StartupConfigError('Invalid demo startup policy')
            startup.update(scheduler_mode=mode, revision=revision + 1)
            return dict(startup)

        stack.enter_context(patch.object(util_startup, 'load_startup_config', side_effect=lambda *args, **kwargs: dict(startup)))
        stack.enter_context(patch.object(util_startup, 'save_scheduler_mode', side_effect=save_demo_policy))
        stack.enter_context(patch.dict(os.environ, {'SMART_AGENT_API_KEY': '', 'SMART_AGENT_RUN_ALLOWLIST': '[]'}))
        stack.enter_context(patch.object(WindowsTasksManager, 'get_scheduled_tasks', side_effect=lambda: native_rows))
        def native_operation(path, operation):
            row = next(row for row in native_rows if row['TaskName'] == path)
            if operation == 'delete_task':
                native_rows.remove(row)
            elif operation != 'start_task':
                row['Status'] = 'Disabled' if operation == 'pause_task' else 'Ready'
            return 'DEMO accepted'

        for operation in ['start_task', 'pause_task', 'ready_task', 'delete_task']:
            stack.enter_context(patch.object(WindowsTasksManager, operation,
                side_effect=lambda path, selected=operation: native_operation(path, selected)))
        import app as platform_app
        from common.utilities.util_jwt import jwt_encode
        from flask import make_response, redirect

        server = platform_app.server

        @server.get('/demo')
        def demo_login():
            response = make_response(redirect('/task_/overview'))
            response.set_cookie('Authorization', 'Bearer ' + jwt_encode(
                {'user_name': 'demo_admin', 'token_use': 'session'}), httponly=True, samesite='Lax')
            return response

        @server.after_request
        def demo_banner(response):
            if response.mimetype == 'text/html':
                text = response.get_data(as_text=True)
                response.set_data(text.replace('</body>', '<div id="task-demo-banner" '
                    'style="position:fixed;bottom:0;left:0;z-index:2000;background:#fef3c7;color:#713f12;'
                    'padding:4px 12px;font-size:12px;pointer-events:none">'
                    '演示环境 · 临时数据 · 所有执行均为模拟</div></body>'))
            return response

        print(f'TASK_DEMO_URL=http://127.0.0.1:{port}/demo', flush=True)
        server.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8097)
    serve(parser.parse_args().port)