import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


class TaskCenterTests(unittest.TestCase):
    def setUp(self):
        from peewee import SqliteDatabase

        from database.sql_db.dao import dao_task_center
        from database.sql_db.entity.table_task_center import MODELS
        from database.sql_db.entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning
        from database.sql_db.entity.table_user import SysGroup, SysGroupUser, SysRole, SysUser, SysUserRole

        self.folder = tempfile.TemporaryDirectory()
        self.database = SqliteDatabase(Path(self.folder.name) / 'tasks.db', timeout=20)
        self.models = [*MODELS, SysApschedulerResults, SysApschedulerRunning,
                   SysGroup, SysGroupUser, SysRole, SysUser, SysUserRole]
        self.binding = self.database.bind_ctx(self.models)
        self.binding.__enter__()
        self.database.create_tables(self.models)
        self.patcher = patch.object(dao_task_center, 'db', return_value=self.database)
        self.patcher.start()
        self.dao = dao_task_center
        SysGroup.create(group_name='team-a', group_status=True, update_datetime=__import__('datetime').datetime.now(),
                        update_by='admin', create_datetime=__import__('datetime').datetime.now(),
                        create_by='admin', group_remark='')
        SysUser.create(user_name='alice', user_full_name='Alice', user_status=True,
                       password_sha256='', user_sex='', user_email='', phone_number='', update_by='admin',
                       update_datetime=__import__('datetime').datetime.now(), create_by='admin',
                       create_datetime=__import__('datetime').datetime.now(), user_remark='', otp_secret='')
        SysGroupUser.create(group_name='team-a', user_name='alice', is_admin=0)

    def tearDown(self):
        self.patcher.stop()
        self.binding.__exit__(None, None, None)
        self.database.close()
        self.folder.cleanup()

    def test_unclaimed_schedule_refused_and_claimed_runs_are_guarded(self):
        task = self.dao.create_task('task-1', 'platform', 'job-1', 'team-a', digest='digest', enabled=True,
                                    claimed=False)
        self.assertIsNone(self.dao.begin_execution('job-1', source='schedule'))
        claimed = self.dao.claim_task('task-2', 'platform', 'job-1', 'team-a', 'digest', True, 'alice')
        self.assertEqual(claimed.task_id, task.task_id)

        run, created = self.dao.reserve_run(task.task_id, 'run-1', 'request-1', 'manual', 'alice', 'digest')
        self.assertTrue(created)
        repeated, created = self.dao.reserve_run(task.task_id, 'run-2', 'request-1', 'manual', 'alice', 'digest')
        self.assertFalse(created)
        self.assertEqual(repeated.run_id, run.run_id)
        self.assertTrue(self.dao.mark_submitted(run.run_id))
        self.assertIsNotNone(self.dao.begin_execution('job-1', task_id=task.task_id, run_id=run.run_id,
                                                      source='manual', actor='alice'))
        self.assertTrue(self.dao.finish_run(run.run_id, 'unknown', 'lost receipt'))
        self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'unknown')
        skipped = self.dao.begin_execution('job-1', task_id=task.task_id, source='schedule')
        self.assertEqual(skipped['status'], 'skipped')
        self.dao.resolve_run(run.run_id, 'not_executed', 'verified absent', 'alice')
        self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'clear')

    def test_log_cursor_and_team_grants_are_persistent(self):
        task = self.dao.create_task('task-2', 'platform', 'job-2', 'team-a', digest='d', enabled=True)
        run, _ = self.dao.reserve_run(task.task_id, 'run-2', 'request-2', 'manual', 'alice', 'd')
        self.assertEqual(self.dao.append_log(run.run_id, 'one'), 1)
        self.assertEqual(self.dao.append_log(run.run_id, 'two'), 2)
        chunks, cursor, loaded = self.dao.get_log_chunks(run.run_id, 1, 1)
        self.assertEqual(chunks, ['two'])
        self.assertEqual(cursor, 2)
        self.assertEqual(loaded.status, 'requested')
        self.dao.upsert_grant('team-a', 'alice', ['view', 'logs'], 'admin')
        self.assertEqual(self.dao.get_grant('team-a', 'alice'), {'view', 'logs'})

    def test_service_enforces_explicit_team_grant_and_returns_safe_task_dto(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        task = self.dao.create_task('task-3', 'platform', 'job-3', 'team-a', digest='d', enabled=True)
        self.dao.upsert_grant('team-a', 'alice', ['view'], 'admin')
        backend = {'id': 'job-3', 'status': True, 'next_run_time': '', 'trigger': 'interval',
                   'plan': {'seconds': 60}, 'kwargs': {'type': 'local', 'script_text': 'private-script',
                                                        'script_type': 'Python', 'timeout': 20}}
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', return_value=[backend]):
            result = task_center.list_tasks(access)
            self.assertEqual(result['rows'][0]['task_id'], task.task_id)
            self.assertNotIn('private-script', str(result))
            self.dao.upsert_grant('team-a', 'alice', [], 'admin')
            denied = task_center.list_tasks(access)
            self.assertEqual(denied['rows'], [])

    def test_platform_list_shows_unclaimed_tasks_without_operation_buttons(self):
        from common import task_center
        from dash_callback.application.task_ import task_mgmt_c

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        claimed = self.dao.create_task('claimed-task', 'platform', 'claimed-job', 'team-a',
                                       digest='d', enabled=False, claimed=True)
        unclaimed = self.dao.create_task('unclaimed-task', 'platform', 'unclaimed-job', '',
                                         digest='d', enabled=False, claimed=False)
        records = [task_center._task_row(task, {
            'id': reference, 'status': False, 'trigger': 'interval',
            'plan': {'seconds': 60}, 'kwargs': {},
        }) for task, reference in [
            (claimed, 'claimed-job'), (unclaimed, 'unclaimed-job'), (None, 'unmapped-job')]]
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_is_admin', return_value=True):
            for ordered in [records, list(reversed(records))]:
                with self.subTest(order=[row['job_id'] for row in ordered]), \
                        patch.object(task_center, 'list_tasks', return_value={
                            'rows': ordered, 'total': 3, 'online': True, 'error': ''}), \
                        patch.object(task_center, 'task_capabilities',
                                     wraps=task_center.task_capabilities) as capabilities:
                    rows, total, _ = task_mgmt_c.load_platform(access)
                    self.assertEqual(total, 3)
                    self.assertEqual(len(rows), 3)
                    capabilities.assert_called_once_with(access, claimed.task_id)
                    for row in rows:
                        if row['claimed']:
                            self.assertIn('transfer:' + claimed.task_id,
                                          [action['custom'] for action in row['actions']])
                        else:
                            self.assertEqual(row['actions'], [])
                            self.assertEqual(row['group_name'], '未认领')
            with self.assertRaisesRegex(task_center.TaskCenterError, '任务不存在或无权访问'):
                task_center.task_capabilities(access, unclaimed.task_id)

    def test_group_admin_flag_is_not_a_global_admin_role(self):
        from common.task_center import _is_admin
        from database.sql_db.entity.table_user import SysGroupUser, SysRole, SysUserRole

        with patch('common.task_center.db', return_value=self.database):
            self.assertFalse(_is_admin('alice'))
            SysGroupUser.update(is_admin=1).where(SysGroupUser.user_name == 'alice').execute()
            self.assertFalse(_is_admin('alice'))
            SysRole.create(role_name='admin', role_status=True, update_datetime=__import__('datetime').datetime.now(),
                           update_by='admin', create_datetime=__import__('datetime').datetime.now(),
                           create_by='admin', role_remark='')
            SysUserRole.create(user_name='alice', role_name='admin')
            self.assertTrue(_is_admin('alice'))

    def test_admin_claim_maps_legacy_history_by_exact_job_and_start(self):
        from datetime import datetime, timedelta
        from database.sql_db.entity.table_apscheduler import SysApschedulerResults

        started = datetime.now() - timedelta(hours=1)
        SysApschedulerResults.create(job_id='legacy-job', status='ok', log='exact legacy output',
                                     start_datetime=started, finish_datetime=started + timedelta(seconds=3))
        task = self.dao.create_task('task-legacy', 'platform', 'legacy-job', 'team-a', digest='d',
                                    claimed=False)
        self.dao.claim_task('new-id', 'platform', 'legacy-job', 'team-a', 'd', False,
                            'admin', include_legacy=True)
        rows, total = self.dao.list_runs(task_ids=[task.task_id], page=1, page_size=10)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].source, 'legacy')
        self.assertEqual(rows[0].legacy_start, started)
        chunks, _, _ = self.dao.get_log_chunks(rows[0].run_id, 0, 10)
        self.assertEqual(chunks, ['exact legacy output'])

    def test_deleted_definition_can_be_recreated_without_reusing_task_id(self):
        original = self.dao.create_task('old-task', 'platform', 'reused-job', 'team-a', digest='old')
        self.dao.set_task_state(original.task_id, deleted=True, actor='alice')
        replacement = self.dao.create_task('new-task', 'platform', 'reused-job', 'team-a', digest='new')
        self.assertNotEqual(original.task_id, replacement.task_id)
        self.assertEqual(self.dao.find_task('platform', 'reused-job').task_id, 'new-task')

    def test_edit_values_hide_password_and_save_preserves_advanced_config(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        payload = dict(type='ssh', script_text='print(1)', script_type='Python', timeout=30,
                       host='example.invalid', port=22, username='demo', password='private-password',
                       extract_names=['count'], notify_channels=['mail'], env_vars=None)
        task = self.dao.create_task('edit-task', 'platform', 'edit-job', 'team-a',
                                    digest=task_center._digest(payload))
        self.dao.upsert_grant('team-a', 'alice', ['edit'], 'admin')
        backend = dict(id='edit-job', status=False, trigger='cron', plan={'second': '0'}, kwargs=payload)
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', return_value=[backend]), \
                patch('common.utilities.util_apscheduler._add_job', return_value='edit-job') as save:
            values = task_center.get_edit_values(access, task.task_id)
            self.assertNotIn('private-password', str(values))
            values.pop('extract_names')
            values.pop('notify_channels')
            values['password'] = ''
            task_center.save_task(access, values, task.task_id)
            saved = save.call_args.args[2]
            self.assertEqual(saved['password'], 'private-password')
            self.assertEqual(saved['extract_names'], ['count'])
            self.assertEqual(saved['notify_channels'], ['mail'])
            self.assertTrue(save.call_args.kwargs['is_pause'])

    def test_zero_and_negative_intervals_are_rejected(self):
        from common.task_center import TaskCenterError, preview_schedule

        for seconds in [0, -1, True]:
            with self.subTest(seconds=seconds), self.assertRaises(TaskCenterError):
                preview_schedule('interval', {'seconds': seconds})

    def test_first_claim_imports_legacy_and_preserves_outer_transaction(self):
        from datetime import datetime, timedelta
        from database.sql_db.entity.table_apscheduler import SysApschedulerResults

        start = datetime.now() - timedelta(hours=2)
        SysApschedulerResults.create(job_id='first-claim', status='ok', log='original',
                                    start_datetime=start, finish_datetime=start + timedelta(seconds=1))
        task = self.dao.claim_task('first-task', 'platform', 'first-claim', 'team-a', '', False,
                                   'admin', include_legacy=True)
        self.assertEqual(self.dao.list_runs(task_ids=[task.task_id])[1], 1)
        with self.database.atomic() as transaction:
            self.dao.upsert_grant('team-a', 'alice', ['view'], 'admin')
            transaction.rollback()
        self.assertEqual(self.dao.get_grant('team-a', 'alice'), set())

    def test_migration_dry_run_is_read_only_and_apply_backups_before_pause(self):
        import importlib.util
        from common import task_center

        specification = importlib.util.spec_from_file_location('task_migration', ROOT / 'scripts' / 'migrate_task_center.py')
        migration = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration)
        jobs = [dict(id='legacy', status=True, trigger='interval', kwargs={})]
        before = self.database.get_tables()
        report = migration.inventory(self.database, jobs)
        self.assertEqual(report['windows_changes'], 0)
        self.assertEqual(self.database.get_tables(), before)
        self.assertIsNone(self.dao.find_task('platform', 'legacy'))
        backups = Path(self.folder.name) / 'backups'
        with patch.object(task_center, '_is_admin', return_value=True), \
                patch('common.utilities.util_apscheduler.start_stop_job') as pause:
            result = migration.apply_migration(self.database, jobs, backups, 'admin', confirmed=True)
            pause.assert_called_once_with('legacy', False, 'interval')
            self.assertTrue(Path(result['backup']).is_file())
            self.assertFalse(self.dao.find_task('platform', 'legacy').claimed)
            self.assertEqual(result['status'], 'paused_pending_claim')

    def test_run_direct_lookup_and_offline_idempotent_receipt(self):
        from common import task_center

        task = self.dao.create_task('direct-task', 'platform', 'direct-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'direct-run', 'same-key', 'manual', 'alice', 'd')
        self.dao.finish_run(run.run_id, 'unknown')
        self.dao.upsert_grant('team-a', 'alice', ['view', 'run'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', side_effect=TimeoutError()) as backend:
            result = task_center.request_run(access, task.task_id, 'same-key')
            self.assertEqual(result['run_id'], 'direct-run')
            self.assertEqual(task_center.list_runs(access, run_id='direct-run')['total'], 1)
            self.assertEqual(task_center.list_runs(access, run_id='absent')['total'], 0)
            backend.assert_not_called()

    def test_request_run_is_idempotent_and_passes_stable_runner_identity(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        payload = {'type': 'local', 'script_text': 'print(1)', 'script_type': 'Python', 'timeout': 20,
                   'extract_names': None, 'notify_channels': None, 'env_vars': None}
        digest = task_center._digest(payload)
        task = self.dao.create_task('task-run', 'platform', 'job-run', 'team-a', digest=digest)
        self.dao.upsert_grant('team-a', 'alice', ['run'], 'admin')
        backend = {'id': 'job-run', 'status': True, 'kwargs': payload}
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', return_value=[backend]), \
                patch('common.utilities.util_apscheduler._add_job', return_value='run-stable') as submit:
            result = task_center.request_run(access, task.task_id, 'same-request', source='agent',
                                             expected_digest=digest, run_id='run-stable')
            duplicate = task_center.request_run(access, task.task_id, 'same-request', source='agent',
                                                expected_digest=digest, run_id='different-run')
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(duplicate['run_id'], 'run-stable')
        self.assertEqual(submit.call_count, 1)
        kwargs = submit.call_args.args[2]
        self.assertEqual(kwargs['task_id'], task.task_id)
        self.assertEqual(kwargs['run_id'], 'run-stable')
        self.assertEqual(kwargs['source'], 'agent')
        self.assertEqual(kwargs['source_job_id'], 'job-run')

    def test_ambiguous_scheduler_receipt_locks_run_without_retry(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        payload = {'type': 'local', 'script_text': 'print(2)', 'script_type': 'Python', 'timeout': 20,
                   'extract_names': None, 'notify_channels': None, 'env_vars': None}
        digest = task_center._digest(payload)
        task = self.dao.create_task('task-unknown', 'platform', 'job-unknown', 'team-a', digest=digest)
        self.dao.upsert_grant('team-a', 'alice', ['run'], 'admin')
        backend = {'id': 'job-unknown', 'status': True, 'kwargs': payload}
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', return_value=[backend]), \
                patch('common.utilities.util_apscheduler._add_job', side_effect=TimeoutError()) as submit:
            result = task_center.request_run(access, task.task_id, 'uncertain-request',
                                             expected_digest=digest, run_id='unknown-run')
            repeated = task_center.request_run(access, task.task_id, 'uncertain-request',
                                               expected_digest=digest, run_id='retry-run')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(repeated['run_id'], 'unknown-run')
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'unknown')

    def test_offline_listing_requires_explicit_view_grant(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        self.dao.create_task('offline-task', 'platform', 'offline-job', 'team-a', digest='d')
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', side_effect=ConnectionError):
            self.assertEqual(task_center.list_tasks(access)['rows'], [])
            self.dao.upsert_grant('team-a', 'alice', ['view'], 'admin')
            self.assertEqual(task_center.list_tasks(access)['total'], 1)

    def test_filters_are_applied_before_pagination(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        self.dao.upsert_grant('team-a', 'alice', ['view'], 'admin')
        backends = []
        for index in range(105):
            job_id = f'job-{index:03}'
            self.dao.create_task(f'task-{index}', 'platform', job_id, 'team-a', enabled=True)
            backends.append({'id': job_id, 'status': True, 'kwargs': {'type': 'ssh' if index == 104 else 'local'}})
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', return_value=backends):
            result = task_center.list_tasks(access, task_type='ssh', page_size=20)
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['rows'][0]['job_id'], 'job-104')

    def test_windows_claim_does_not_pause_native_task_and_submit_is_idempotent(self):
        from common import task_center
        from dash_components.windows_tasks_manager import WindowsTasksManager

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        self.dao.upsert_grant('team-a', 'alice', ['claim', 'run'], 'admin')
        native = {'TaskName': '\\Demo\\Native', 'Status': 'Ready'}
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(WindowsTasksManager, 'get_scheduled_tasks', return_value=[native]), \
                patch.object(WindowsTasksManager, 'pause_task') as pause, \
                patch.object(WindowsTasksManager, 'start_task') as start:
            task_id = task_center.claim_task(access, native['TaskName'], 'team-a', engine='windows')
            result = task_center.request_run(access, task_id, 'native-request')
            again = task_center.request_run(access, task_id, 'native-request')
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(result['run_id'], again['run_id'])
            pause.assert_not_called()
            start.assert_called_once_with(native['TaskName'])

    def test_complete_log_reads_all_pages_and_denies_revoked_access(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        task = self.dao.create_task('log-task', 'platform', 'log-job', 'team-a', digest='d')
        self.dao.upsert_grant('team-a', 'alice', ['logs'], 'admin')
        run, _ = self.dao.reserve_run(task.task_id, 'log-run', 'log-request', 'manual', 'alice', 'd')
        for index in range(101):
            self.dao.append_log(run.run_id, f'line {index}\n')
        self.dao.finish_run(run.run_id, 'succeeded')
        with patch.object(task_center, 'db', return_value=self.database):
            first = task_center.read_log(access, run.run_id, 0, 100)
            self.assertFalse(first['complete'])
            last = task_center.read_log(access, run.run_id, first['cursor'], 100)
            self.assertEqual(last['chunks'], ['line 100\n'])
            self.assertTrue(last['complete'])
            self.dao.upsert_grant('team-a', 'alice', [], 'admin')
            with self.assertRaises(task_center.TaskCenterError):
                task_center.read_log(access, run.run_id)

    def test_windows_export_requeries_authorization_and_neutralizes_formulas(self):
        from common import task_center
        from dash_components.windows_tasks_manager import WindowsTasksManager

        native = dict(TaskName='=FORMULA', Status='Running', **{'Last Result': '0', 'Principal': '@user',
                                                              'MultipleInstancesPolicy': 'IgnoreNew'})
        self.dao.create_task('export-native', 'windows', native['TaskName'], 'team-a')
        self.dao.upsert_grant('team-a', 'alice', ['view', 'export'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(WindowsTasksManager, 'get_scheduled_tasks', return_value=[native]):
            csv_text = task_center.export_windows(access)
            self.assertIn("'=FORMULA", csv_text)
            self.assertIn("'@user", csv_text)
            self.assertIn('IgnoreNew', csv_text)
            self.dao.upsert_grant('team-a', 'alice', ['view'], 'admin')
            self.assertNotIn('FORMULA', task_center.export_windows(access))

    def test_listener_save_stays_paused_and_keeps_event_definition(self):
        from common import task_center

        self.dao.upsert_grant('team-a', 'alice', ['claim'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        with patch.object(task_center, 'db', return_value=self.database), \
                patch('database.sql_db.dao.dao_listen_task.insert_activa_listen_job') as save, \
                patch('common.utilities.util_apscheduler._add_job') as scheduler:
            result = task_center.save_task(access, dict(job_id='listener', group_name='team-a',
                trigger='listen', type='local', script_text='print(1)', script_type='Python', timeout=20,
                plan=dict(listen_channels=['mailbox'], listen_keyword='approved')))
            self.assertEqual(result['status'], 'paused')
            self.assertFalse(save.call_args.kwargs['status'])
            self.assertEqual(save.call_args.kwargs['listen_keyword'], 'approved')
            scheduler.assert_not_called()

    def test_listener_event_is_persistently_idempotent_and_uses_source_id(self):
        from common import task_center

        payload = dict(type='local', script_text='print(1)', script_type='Python', timeout=20,
                       extract_names=None, notify_channels=None, env_vars=None)
        task = self.dao.create_task('listen-task', 'platform', 'mail-task', 'team-a', enabled=True,
                                    digest=task_center._digest(payload))
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_backend_for', return_value=dict(kwargs=payload, trigger='listen', status=True)), \
                patch('common.utilities.util_apscheduler._add_job', side_effect=lambda *args, **kwargs: args[3]) as submit:
            first = task_center.dispatch_listen('mail-task', 'event-one', {'__title__': 'approved'})
            second = task_center.dispatch_listen('mail-task', 'event-one', {'__title__': 'approved'})
            self.assertEqual(first['run_id'], second['run_id'])
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[2]['source_job_id'], 'mail-task')
            accepted = self.dao.begin_execution('mail-task', task_id=task.task_id,
                run_id=first['run_id'], source='listen', actor='listener')
            self.assertEqual(accepted['status'], 'running')

    def test_executor_uses_guard_without_running_real_script(self):
        import app_apscheduler
        from common import task_center
        from database.sql_db.entity.table_user import SysGroup

        payload = dict(type='local', script_text='never run', script_type='Python', timeout=20,
                       extract_names=None, notify_channels=[], env_vars=None)
        task = self.dao.create_task('exec-task', 'platform', 'exec-job', 'team-a', enabled=True,
                                    digest=task_center._digest(payload))
        with patch.object(app_apscheduler, '_execute_script', return_value='success') as execute:
            self.assertEqual(app_apscheduler.run_script('local', 'never run', 'Python', 'exec-job', []), 'success')
            execute.assert_called_once()
            self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'clear')
            SysGroup.update(group_status=False).where(SysGroup.group_name == 'team-a').execute()
            app_apscheduler.run_script('local', 'never run', 'Python', 'exec-job', [])
            self.assertEqual(execute.call_count, 1)

    def test_executor_rechecks_actor_grant_and_payload_before_launch(self):
        import app_apscheduler
        from common import task_center

        payload = dict(type='local', script_text='safe', script_type='Python', timeout=20,
                       extract_names=None, notify_channels=[], env_vars=None)
        task = self.dao.create_task('recheck-task', 'platform', 'recheck-job', 'team-a', enabled=True,
                                    digest=task_center._digest(payload))
        run, _ = self.dao.reserve_run(task.task_id, 'recheck-run', 'key', 'manual', 'alice', task.digest)
        with patch.object(task_center, 'db', return_value=self.database), \
                patch('database.sql_db.dao.dao_user.get_user_access_meta', return_value={'任务管理-页面'}), \
                patch.object(app_apscheduler, '_execute_script') as execute:
            app_apscheduler.run_script('local', 'safe', 'Python', 'recheck-run', [],
                task_id=task.task_id, run_id=run.run_id, source='manual', source_job_id='recheck-job')
            execute.assert_not_called()
            self.assertEqual(self.dao.get_run(run.run_id).status, 'skipped')
            app_apscheduler.run_script('local', 'changed', 'Python', 'recheck-job', [])
            execute.assert_not_called()

    def test_unknown_guard_blocks_other_requests(self):
        task = self.dao.create_task('guard-task', 'platform', 'guard-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'guard-run', 'one', 'manual', 'alice', 'd')
        self.dao.finish_run(run.run_id, 'unknown', 'uncertain')
        with self.assertRaises(ValueError):
            self.dao.reserve_run(task.task_id, 'second-run', 'two', 'manual', 'alice', 'd')

    def test_two_database_connections_allow_only_one_active_request(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        task = self.dao.create_task('race-task', 'platform', 'race-job', 'team-a', digest='d')
        barrier = Barrier(2)

        def reserve(identity):
            with self.database.connection_context():
                barrier.wait(timeout=5)
                try:
                    return self.dao.reserve_run(task.task_id, identity, identity, 'manual', 'alice', 'd')[1]
                except ValueError:
                    return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ['race-a', 'race-b']))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.dao.list_runs(task_ids=[task.task_id])[1], 1)

    def test_definition_operation_lock_blocks_runs_and_can_be_resolved(self):
        task = self.dao.create_task('locked-task', 'platform', 'locked-job', 'team-a', digest='d')
        self.assertTrue(self.dao.begin_definition_update(task.task_id, 'd'))
        with self.assertRaises(ValueError):
            self.dao.reserve_run(task.task_id, 'blocked-run', 'blocked', 'manual', 'alice', 'd')
        self.dao.mark_definition_unknown(task.task_id)
        self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'unknown')
        resolved = self.dao.resolve_definition(task.task_id, 'verified', 'admin', 'external state checked')
        self.assertEqual(resolved.guard_state, 'clear')
        self.assertFalse(resolved.enabled)

    def test_retention_preserves_unknown_runs_and_team_history(self):
        from datetime import datetime, timedelta
        from database.sql_db.entity.table_task_center import TaskRun, TaskOperationAudit
        from database.sql_db.dao import dao_user

        task = self.dao.create_task('retention-task', 'platform', 'retention-job', 'team-a', digest='d')
        first, _ = self.dao.reserve_run(task.task_id, 'expired-run', 'one', 'manual', 'alice', 'd')
        self.dao.append_log(first.run_id, 'expired output')
        self.dao.finish_run(first.run_id, 'succeeded')
        TaskRun.update(finished_at=datetime.now() - timedelta(days=40)).where(TaskRun.run_id == first.run_id).execute()
        unknown, _ = self.dao.reserve_run(task.task_id, 'unknown-retained', 'two', 'manual', 'alice', 'd')
        self.dao.append_log(unknown.run_id, 'must survive')
        self.dao.finish_run(unknown.run_id, 'unknown')
        self.assertEqual(self.dao.prune_logs(30), 1)
        self.assertEqual(self.dao.get_log_chunks(unknown.run_id)[0], ['must survive'])
        self.assertIsNotNone(self.dao.get_run(first.run_id))
        self.assertTrue(TaskOperationAudit.select().exists())
        with patch.object(dao_user, 'db', return_value=self.database):
            self.assertFalse(dao_user.delete_group('team-a'))

    def test_capacity_paged_metadata_and_runs_stay_bounded(self):
        from datetime import datetime, timedelta
        from time import perf_counter
        from common import task_center
        from database.sql_db.entity.table_task_center import TaskDefinition, TaskRun

        now = datetime.now()
        with self.database.atomic():
            for offset in range(0, 1000, 100):
                TaskDefinition.insert_many([dict(task_id=f'capacity-{index}', backend_ref=f'job-{index}',
                    engine='platform', group_name='team-a', claimed=True, enabled=True)
                    for index in range(offset, offset + 100)]).execute()
            for offset in range(0, 100000, 500):
                TaskRun.insert_many([dict(run_id=f'capacity-run-{index}', task_id=f'capacity-{index % 1000}',
                    job_id=f'job-{index % 1000}', group_name='team-a', source='schedule', status='succeeded',
                    requested_at=now - timedelta(seconds=index), finished_at=now - timedelta(seconds=index))
                    for index in range(offset, offset + 500)]).execute()
        self.dao.upsert_grant('team-a', 'alice', ['view'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        backends = [dict(id=f'job-{index}', status=True, kwargs={'type': 'local'}) for index in range(1000)]
        samples = []
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(task_center, '_platform_job_rows', return_value=backends):
            for _ in range(5):
                start = perf_counter()
                tasks = task_center.list_tasks(access, page=2, page_size=20)
                runs = task_center.list_runs(access, page=500, page_size=20)
                samples.append(perf_counter() - start)
                self.assertEqual((tasks['total'], len(tasks['rows'])), (1000, 20))
                self.assertEqual((runs['total'], len(runs['rows'])), (100000, 20))
        print(f'CAPACITY SQLite: 1000 tasks / 100000 runs, five page pairs, max={max(samples):.3f}s')
        self.assertLess(max(samples), 2.0)

    def test_new_run_timestamps_are_stored_and_returned_in_utc(self):
        from datetime import timedelta

        task = self.dao.create_task('utc-task', 'platform', 'utc-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'utc-run', 'utc-request', 'manual', 'alice', 'd')
        self.assertEqual(run.requested_at.utcoffset(), timedelta(0))
        stored = self.database.execute_sql('SELECT requested_at FROM sys_task_run WHERE run_id = ?', ('utc-run',)).fetchone()[0]
        self.assertTrue(stored.endswith('+00:00'))

    def test_sse_per_user_limit_and_disconnect_cleanup(self):
        with patch('config.access_factory.AccessFactory.check_access_meta'):
            import app
        import server
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        payload = {'chunks': ['one'], 'cursor': 1, 'complete': False, 'status': 'running'}
        client = app.server.test_client()
        with patch('common.utilities.util_menu_access.get_menu_access', return_value=access), \
                patch.object(task_center, 'read_log', return_value=payload):
            responses = []
            try:
                for _ in range(2):
                    responses.append(client.post('/task_log_sse', headers={'run-id': 'sse-run'}, buffered=False))
                    self.assertEqual(responses[-1].status_code, 200)
                self.assertEqual(client.post('/task_log_sse', headers={'run-id': 'sse-run'}).status_code, 429)
                responses.pop().close()
                response = client.post('/task_log_sse', headers={'run-id': 'sse-run'}, buffered=False)
                responses.append(response)
                self.assertEqual(response.status_code, 200)
            finally:
                for response in reversed(responses):
                    response.close()
            self.assertNotIn('alice', server._task_sse_connections)

    def test_latest_completed_log_not_hidden_by_newer_pending_runs(self):
        from datetime import datetime, timedelta
        from common import task_center
        from database.sql_db.entity.table_task_center import TaskRun

        task = self.dao.create_task('latest-task', 'platform', 'latest-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'completed-run', 'key', 'manual', 'alice', 'd')
        self.dao.append_log(run.run_id, 'completed output')
        self.dao.finish_run(run.run_id, 'succeeded')
        TaskRun.insert_many([dict(run_id=f'pending-{index}', task_id=task.task_id, job_id='latest-job',
            group_name='team-a', source='schedule', status='skipped', requested_at=datetime.now() + timedelta(seconds=index))
            for index in range(101)]).execute()
        self.dao.upsert_grant('team-a', 'alice', ['view', 'logs', 'export'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        with patch.object(task_center, 'db', return_value=self.database):
            self.assertEqual(task_center.latest_log(access, 'latest-job')['run_id'], run.run_id)
            with patch.object(task_center, 'MAX_LOG_CHARS', 5):
                self.assertIn('EXPORT TRUNCATED', task_center.export_log(access, run.run_id))

    def test_definition_version_blocks_same_script_stale_plan(self):
        task = self.dao.create_task('version-task', 'platform', 'version-job', 'team-a', digest='same')
        self.assertTrue(self.dao.begin_definition_update(task.task_id, 'same', 1))
        self.dao.finish_definition_update(task.task_id, 'same', 'same', 'alice')
        self.assertFalse(self.dao.begin_definition_update(task.task_id, 'same', 1))
        self.assertTrue(self.dao.begin_definition_update(task.task_id, 'same', 2))

    def test_grant_delegate_cannot_elevate_or_delegate_grant(self):
        from common import task_center

        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        self.dao.upsert_grant('team-a', 'alice', ['view', 'grant'], 'admin')
        with patch.object(task_center, 'db', return_value=self.database):
            self.assertTrue(task_center.can_manage_grants(access))
            with self.assertRaises(task_center.TaskCenterError):
                task_center.grant_team(access, 'team-a', 'alice', ['run'])
            with self.assertRaises(task_center.TaskCenterError):
                task_center.grant_team(access, 'team-a', 'alice', ['grant'])

    def test_unknown_resolution_rejects_blank_reason(self):
        task = self.dao.create_task('unknown-reason-task', 'platform', 'unknown-reason-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'reason-run', 'key', 'manual', 'alice', 'd')
        self.dao.finish_run(run.run_id, 'unknown')
        with self.assertRaises(ValueError):
            self.dao.resolve_run(run.run_id, 'ended', '   ', 'alice')
        self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'unknown')

    def test_windows_definition_resolution_requires_native_disabled_state(self):
        from common import task_center
        from dash_components.windows_tasks_manager import WindowsTasksManager

        task = self.dao.create_task('native-definition', 'windows', '\\Demo\\native', 'team-a')
        self.dao.begin_definition_update(task.task_id, '')
        self.dao.mark_definition_unknown(task.task_id)
        self.dao.upsert_grant('team-a', 'alice', ['resolve'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        with patch.object(task_center, 'db', return_value=self.database), \
                patch.object(WindowsTasksManager, 'get_scheduled_tasks', return_value=[
                    {'TaskName': task.backend_ref, 'Status': 'Ready'}]) as snapshot:
            with self.assertRaises(task_center.TaskCenterError):
                task_center.resolve_task_definition(access, task.task_id, 'checked native task')
            snapshot.return_value[0]['Status'] = 'Disabled'
            task_center.resolve_task_definition(access, task.task_id, 'confirmed native disabled')
        self.assertEqual(self.dao.get_task(task.task_id).guard_state, 'clear')

    def test_run_unknown_capability_does_not_use_definition_lock_rules(self):
        from common import task_center

        task = self.dao.create_task('resolve-cap-task', 'platform', 'resolve-cap-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'resolve-cap-run', 'key', 'manual', 'alice', 'd')
        self.dao.finish_run(run.run_id, 'unknown')
        self.dao.upsert_grant('team-a', 'alice', ['view', 'logs', 'resolve'], 'admin')
        access = SimpleNamespace(user_name='alice', has_access=lambda permission: True)
        with patch.object(task_center, 'db', return_value=self.database):
            self.assertNotIn('resolve', task_center.task_capabilities(access, task.task_id))
            self.assertIn('resolve', task_center.run_capabilities(access, run.run_id))
            self.assertNotIn('export', task_center.run_capabilities(access, run.run_id))
            self.dao.upsert_grant('team-a', 'alice', ['view', 'logs'], 'admin')
            self.assertNotIn('resolve', task_center.run_capabilities(access, run.run_id))

    def test_version_one_migration_requires_timezone_and_normalizes_legacy_time(self):
        import importlib.util
        from datetime import datetime, timezone
        from common import task_center
        from database.sql_db.entity.table_task_center import TaskSchemaVersion

        specification = importlib.util.spec_from_file_location('utc_migration', ROOT / 'scripts' / 'migrate_task_center.py')
        migration = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration)
        task = self.dao.create_task('old-time-task', 'platform', 'old-time-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'old-time-run', 'key', 'manual', 'alice', 'd')
        self.dao.finish_run(run.run_id, 'succeeded')
        self.database.execute_sql('UPDATE sys_task_run SET requested_at = ? WHERE run_id = ?',
                                  ('2020-01-01 00:00:00', run.run_id))
        TaskSchemaVersion.get_or_create(component='task_center', defaults={'version': 1})
        TaskSchemaVersion.update(version=1).where(TaskSchemaVersion.component == 'task_center').execute()
        with patch.object(task_center, '_is_admin', return_value=True):
            with self.assertRaises(ValueError):
                migration.apply_migration(self.database, [], Path(self.folder.name) / 'utc-backup', 'admin', True)
            migration.apply_migration(self.database, [], Path(self.folder.name) / 'utc-backup', 'admin', True,
                                       legacy_timezone='Asia/Shanghai')
        self.assertEqual(self.dao.get_run(run.run_id).requested_at,
                         datetime(2019, 12, 31, 16, tzinfo=timezone.utc))

    def test_transfer_keeps_historical_team_and_refuses_active_task(self):
        task = self.dao.create_task('transfer-task', 'platform', 'transfer-job', 'team-a', digest='d')
        run, _ = self.dao.reserve_run(task.task_id, 'transfer-run', 'key', 'manual', 'alice', 'd')
        with self.assertRaises(ValueError):
            self.dao.transfer_task(task.task_id, 'team-b', 'admin', 'handover')
        self.dao.finish_run(run.run_id, 'succeeded')
        self.dao.transfer_task(task.task_id, 'team-b', 'admin', 'handover')
        self.assertEqual(self.dao.get_task(task.task_id).group_name, 'team-b')
        self.assertEqual(self.dao.get_run(run.run_id).group_name, 'team-a')


class SchedulePreviewTests(unittest.TestCase):
    def test_empty_timeline_is_compact_and_preserves_time_window(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from dash_callback.application.task_ import overview_c
        from dash_callback.application.task_.overview_c import _timeline

        start = datetime(2026, 10, 9, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        end = start + timedelta(days=1)
        tasks = [{'task_id': 'paused', 'name': '暂停任务', 'enabled': False}]
        for mode, expected in [('schedule', '任务已暂停，暂无计划触发'),
                               ('actual', '此时间范围无运行记录')]:
            with self.subTest(mode=mode):
                figure = _timeline(tasks, [], [], mode, start, end, 1)
                self.assertEqual(figure['data'], [])
                self.assertEqual(figure['layout']['height'], 220)
                self.assertFalse(figure['layout']['xaxis']['visible'])
                self.assertFalse(figure['layout']['yaxis']['visible'])
                self.assertEqual(figure['layout']['xaxis']['range'], [start.isoformat(), end.isoformat()])
                self.assertEqual(figure['layout']['annotations'][0]['text'], expected)
                with patch.object(overview_c.dash, 'ctx', SimpleNamespace(triggered_id='tc-overview-refresh')), \
                        patch.object(overview_c, 'get_menu_access', return_value=None), \
                        patch.object(overview_c, '_OverviewAccess', return_value=None), \
                        patch.object(overview_c, 'build_overview', return_value=(None, figure, [], [], 0, {})):
                    outputs = overview_c.refresh_overview(0, 1, 0, mode, '1d', 1, 20)
                self.assertEqual(len(outputs), 10)
                self.assertEqual(outputs[8], 'tc-timeline-scroll is-empty')
                self.assertEqual(outputs[9], {'display': 'none'})
                self.assertEqual(outputs[1]['layout']['height'], 220)

    def test_overview_controls_match_time_direction_and_render(self):
        from dash_callback.application.task_.overview_c import overview_controls
        from dash_view.application.task_.overview import render_content

        for mode, direction in [('schedule', '未来'), ('actual', '过去')]:
            options, legend, style, page = overview_controls(mode, '7d')
            self.assertEqual([row['value'] for row in options], ['1d', '7d', '30d'])
            self.assertTrue(all(row['label'].startswith(direction) for row in options))
            self.assertTrue(legend)
            self.assertEqual(style, {})
            self.assertEqual(page, 1)
        self.assertEqual(overview_controls('list', '1d')[2], {'display': 'none'})
        self.assertIsNotNone(render_content(None))

    def test_timeline_keeps_full_window_and_unique_run_rows(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from dash_callback.application.task_.overview_c import _timeline

        end = datetime(2026, 10, 6, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        start = end - timedelta(days=7)
        runs = [{'run_id': f'demo-run-{index}', 'name': '同名任务', 'status': 'succeeded',
                 '_start': end - timedelta(hours=1), '_finish': end - timedelta(hours=1) + timedelta(seconds=2)}
                for index in range(20)]
        figure = _timeline([], [], runs, 'actual', start, end, 7)
        self.assertEqual(figure['layout']['xaxis']['range'], [start.isoformat(), end.isoformat()])
        self.assertEqual(len(set(figure['layout']['yaxis']['tickvals'])), 20)
        self.assertGreaterEqual(figure['layout']['height'], 20 * 34)
        bars, markers = figure['data']
        self.assertEqual(bars['x'], [2000] * 20)
        self.assertEqual(markers['marker']['size'], 14)
        self.assertEqual(markers['customdata'], [run['run_id'] for run in runs])
        self.assertIn('2.0 秒', markers['hovertext'][0])

    def test_timeline_aggregates_preview_counts_and_marks_unknown(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from dash_callback.application.task_.overview_c import _timeline

        start = datetime(2026, 10, 6, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        tasks = [{'task_id': 'task-a', 'name': '同名任务', 'enabled': True},
                 {'task_id': 'task-b', 'name': '同名任务', 'enabled': True, 'guard_state': 'unknown'}]
        points = [{'task_id': task['task_id'], 'at': (start + timedelta(hours=index)).isoformat()}
                  for task in tasks for index in range(50)]
        figure = _timeline(tasks, points, [], 'schedule', start, start + timedelta(days=7), 7)
        self.assertEqual(len(figure['data']), 2)
        for trace in figure['data']:
            self.assertEqual(trace['text'], ['24', '24', '2'])
            self.assertEqual(len(set(trace['y'])), 1)
            self.assertIn('最多50次', trace['hovertext'][0])
        self.assertNotEqual(figure['data'][0]['marker']['color'], figure['data'][1]['marker']['color'])
        self.assertEqual(figure['layout']['yaxis']['tickvals'], ['task-a', 'task-b'])

    def test_timeline_does_not_invent_interval_without_start_or_finish(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from dash_callback.application.task_.overview_c import _timeline

        start = datetime(2026, 10, 6, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        runs = [{'run_id': 'pending', 'name': '任务', 'status': 'unknown',
                 '_start': start, '_finish': None},
                {'run_id': 'missing-start', 'name': '任务', 'status': 'failed',
                 '_start': start, '_finish': start + timedelta(seconds=5), '_start_recorded': False}]
        figure = _timeline([], [], runs, 'actual', start, start + timedelta(days=1), 1)
        self.assertEqual(len(figure['data']), 1)
        self.assertEqual(figure['data'][0]['marker']['symbol'], ['circle-open', 'circle-open'])
        self.assertIn('申请：', figure['data'][0]['hovertext'][1])
        self.assertIn('未记录完整运行区间', figure['data'][0]['hovertext'][1])

    def test_overview_normalizes_legacy_naive_time(self):
        from dash_callback.application.task_.overview_c import _stamp
        from datetime import datetime

        self.assertIsNotNone(_stamp('2026-10-05T12:00:00').tzinfo)
        self.assertLess(_stamp('2020-01-01T00:00:00'), datetime.now().astimezone())

    def test_preview_returns_at_most_five_timezone_aware_values(self):
        from common.task_center import preview_schedule

        result = preview_schedule('interval', {'seconds': 60}, 'Asia/Shanghai')
        self.assertEqual(len(result), 5)
        self.assertTrue(all(value.endswith('+08:00') for value in result))
        with self.assertRaises(ValueError):
            preview_schedule('cron', {'day_of_week': 'not-a-day'})


if __name__ == '__main__':
    unittest.main()