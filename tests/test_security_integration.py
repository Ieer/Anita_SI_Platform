import sys
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def oauth_environment(tmp_path):
    import secrets
    from peewee import SqliteDatabase
    from app import app
    from database.sql_db import conn
    from database.sql_db.entity.table_oauth2 import OAuth2Client, OAuth2AuthorizationCode, OAuth2Token
    from database.sql_db.entity.table_user import SysUser, SysRole, SysUserRole, SysRoleAccessMeta, SysGroup, SysGroupRole, SysGroupUser
    from database.sql_db.dao import dao_oauth2
    from common.utilities.util_jwt import jwt_encode
    from common.utilities.util_menu_access import cache
    from datetime import datetime

    models = [OAuth2Client, OAuth2AuthorizationCode, OAuth2Token, SysUser, SysRole, SysUserRole, SysRoleAccessMeta, SysGroup, SysGroupRole, SysGroupUser]
    database = SqliteDatabase(tmp_path / 'oauth-test.db')
    secret = secrets.token_urlsafe(32)
    with database.bind_ctx(models), patch.object(conn, 'db', return_value=database), patch.object(dao_oauth2, 'db', return_value=database):
        database.create_tables(models)
        OAuth2Client.create(client_id='integration', client_secret=secret, redirect_uris='http://127.0.0.1:9876/callback?source=test', scope='userinfo')
        now = datetime.now()
        SysUser.create(user_name='oauth_probe', user_full_name='OAuth Probe', user_status=True, password_sha256='', user_sex='', user_email='', phone_number='', update_by='probe', update_datetime=now, create_by='probe', create_datetime=now, user_remark='', otp_secret='')
        client = app.server.test_client()
        client.set_cookie('Authorization', 'Bearer ' + jwt_encode({'user_name': 'oauth_probe'}))
        yield client, secret, database
        cache.clear()
        database.close()


def test_oauth_full_flow_and_replay(oauth_environment):
    from html.parser import HTMLParser
    from urllib.parse import urlencode, urlsplit, parse_qs
    from database.sql_db.entity.table_oauth2 import OAuth2AuthorizationCode

    class ConsentParser(HTMLParser):
        csrf = ''

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'input' and values.get('name') == 'csrf_token':
                self.csrf = values['value']

    client, secret, database = oauth_environment
    params = dict(client_id='integration', redirect_uri='http://127.0.0.1:9876/callback?source=test', response_type='code', scope='userinfo', state='probe-state')
    url = '/oauth/authorize?' + urlencode(params)
    response = client.get(url)
    assert response.status_code == 200
    parser = ConsentParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.csrf
    response = client.post(url, data={'confirm': 'on', 'csrf_token': parser.csrf})
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query['state'] == ['probe-state']
    assert query['source'] == ['test']
    form = dict(client_id='integration', client_secret=secret, grant_type='authorization_code', code=query['code'][0], redirect_uri=params['redirect_uri'])
    assert client.post('/oauth/token', data={**form, 'redirect_uri': 'http://evil.invalid'}).status_code == 400
    response = client.post('/oauth/token', data=form)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    token = response.json['access_token']
    info = client.get('/api/userinfo', headers={'Authorization': 'Bearer ' + token})
    assert info.status_code == 200
    assert info.json['user_name'] == 'oauth_probe'
    assert client.post('/oauth/token', data=form).status_code == 401
    with database.connection_context():
        assert OAuth2AuthorizationCode.select().count() == 0
    from common.utilities.util_authorization import auth_validate, AccessFailType
    from server import server
    with server.test_request_context('/', headers={'Authorization': 'Bearer ' + token}):
        assert auth_validate() == AccessFailType.INVALID


def test_oauth_rejects_untrusted_consent_and_redirects(oauth_environment):
    from urllib.parse import urlencode, urlsplit, parse_qs

    client, _, _ = oauth_environment
    params = dict(client_id='integration', redirect_uri='http://127.0.0.1:9876/callback?source=test', response_type='code', scope='userinfo', state='probe-state')
    url = '/oauth/authorize?' + urlencode(params)
    assert client.get(url + '&confirm=yes').status_code == 200
    assert client.post(url, data={'confirm': 'on'}).status_code == 403
    for changes in [{'scope': 'admin'}, {'redirect_uri': 'https://evil.invalid'}, {'client_id': 'unknown'}]:
        assert client.get('/oauth/authorize?' + urlencode({**params, **changes})).status_code == 400
    assert client.get('/?next=https://evil.invalid').status_code == 400
    client.delete_cookie('Authorization')
    response = client.get(url)
    assert response.status_code == 302
    assert urlsplit(response.location).path == '/login'
    assert parse_qs(urlsplit(response.location).query)['to'][0].startswith('/oauth/authorize?')


@pytest.mark.skipif(os.environ.get('PLATFORM_LOCAL_SCHEDULER_PROBE') != '1', reason='real local execution is opt-in')
def test_real_local_scheduler_execution(tmp_path):
    from threading import Event
    from uuid import uuid4
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    from peewee import SqliteDatabase
    import app_apscheduler
    from database.sql_db.dao import dao_apscheduler
    from database.sql_db.entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning, SysApschedulerExtractValue

    database = SqliteDatabase(tmp_path / 'scheduler-test.db')
    models = [SysApschedulerResults, SysApschedulerRunning, SysApschedulerExtractValue]
    completed = Event()
    events = []
    scheduler = BackgroundScheduler()
    job_id = 'platform_probe_' + uuid4().hex

    def finished(event):
        events.append(event)
        completed.set()

    scheduler.add_listener(finished, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    with database.bind_ctx(models), patch.object(dao_apscheduler, 'db', return_value=database), \
            patch.object(app_apscheduler.tempfile, 'gettempdir', return_value=str(tmp_path)):
        database.create_tables(models)
        scheduler.add_job(app_apscheduler.run_script, 'date', id=job_id, kwargs=dict(type='local', script_text="print('PLATFORM_PROBE_OK')", script_type='Python', job_id=job_id, notify_channels=[], timeout=15))
        scheduler.start()
        try:
            assert completed.wait(25), 'Temporary job did not finish within deadline'
            assert events[0].exception is None
            result = SysApschedulerResults.get(SysApschedulerResults.job_id == job_id)
            assert result.status == 'success'
            assert 'PLATFORM_PROBE_OK' in result.log
            assert SysApschedulerRunning.select().count() == 0
            assert not list(tmp_path.glob('dashgo_*'))
        finally:
            scheduler.shutdown(wait=True)
            database.close()


@pytest.mark.skipif(os.environ.get('PLATFORM_LOCAL_SCHEDULER_PROBE') != '1', reason='real local execution is opt-in')
def test_local_scheduler_timeout_cleans_temporary_resources(tmp_path):
    from uuid import uuid4
    import app_apscheduler
    from peewee import SqliteDatabase
    from database.sql_db.dao import dao_apscheduler
    from database.sql_db.entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning, SysApschedulerExtractValue

    database = SqliteDatabase(tmp_path / 'scheduler-timeout-test.db')
    models = [SysApschedulerResults, SysApschedulerRunning, SysApschedulerExtractValue]
    job_id = 'platform_timeout_' + uuid4().hex
    with database.bind_ctx(models), patch.object(dao_apscheduler, 'db', return_value=database), \
            patch.object(app_apscheduler.tempfile, 'gettempdir', return_value=str(tmp_path)):
        database.create_tables(models)
        app_apscheduler.run_script(
            type='local',
            script_text='import time\ntime.sleep(10)',
            script_type='Python',
            job_id=job_id,
            notify_channels=[],
            timeout=1,
        )
        result = SysApschedulerResults.get(SysApschedulerResults.job_id == job_id)
        assert result.status == 'timeout'
        assert SysApschedulerRunning.select().count() == 0
        assert not list(tmp_path.glob('dashgo_*'))
        database.close()


@pytest.mark.parametrize(('remote_finished', 'expected_status'), [(False, 'unknown'), (True, 'success')])
def test_ssh_scheduler_completion_or_uncertainty_updates_run_guard(tmp_path, remote_finished, expected_status):
    from uuid import uuid4
    import app_apscheduler
    from peewee import SqliteDatabase
    from database.sql_db.dao import dao_apscheduler
    from database.sql_db.dao import dao_task_center
    from database.sql_db.entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning, SysApschedulerExtractValue
    from database.sql_db.entity.table_task_center import MODELS as TASK_MODELS, TaskDefinition, TaskRun
    from database.sql_db.entity.table_user import SysGroup
    from datetime import datetime

    class FakeChannel:
        def __init__(self):
            self.closed = False

        def exit_status_ready(self):
            return remote_finished

        def recv_exit_status(self):
            return 0

        def close(self):
            self.closed = True

    class FakeStream:
        def __init__(self, channel):
            self.channel = channel

        def readline(self):
            return b''

    class FakeSSH:
        def __init__(self, channel):
            self.channel = channel
            self.closed = False
            self.commands = []

        def exec_command(self, command, **kwargs):
            self.commands.append(command)
            if command.startswith('rm -f'):
                return None, None, None
            return None, FakeStream(self.channel), FakeStream(self.channel)

        def close(self):
            self.closed = True

    database = SqliteDatabase(tmp_path / 'ssh-timeout-test.db')
    models = [SysApschedulerResults, SysApschedulerRunning, SysApschedulerExtractValue, *TASK_MODELS, SysGroup]
    job_id = 'platform_ssh_timeout_' + uuid4().hex
    task_id = uuid4().hex
    run_id = uuid4().hex
    channel = FakeChannel()
    ssh = FakeSSH(channel)
    with database.bind_ctx(models), patch.object(dao_apscheduler, 'db', return_value=database), \
            patch.object(dao_task_center, 'db', return_value=database), \
            patch.object(app_apscheduler.tempfile, 'gettempdir', return_value=str(tmp_path)), \
            patch.object(app_apscheduler.util_ssh, 'connect_ssh', return_value=ssh), \
            patch.object(app_apscheduler.util_ssh, 'ftp_ssh'):
        database.create_tables(models)
        now = datetime.now()
        SysGroup.create(
            group_name='runner-probe', group_status=True, update_datetime=now, update_by='probe',
            create_datetime=now, create_by='probe', group_remark='',
        )
        from common.task_center import _digest
        digest = _digest(dict(type='ssh', script_text='sleep 10', script_type='Shell',
            notify_channels=[], extract_names=None, env_vars=None, timeout=10 if remote_finished else 1,
            host='example.invalid', port=22, username='probe', password='not-used'))
        dao_task_center.create_task(
            task_id, 'platform', job_id, 'runner-probe', enabled=True, claimed=True, actor='probe',
            digest=digest,
        )
        app_apscheduler.run_script(
            type='ssh',
            script_text='sleep 10',
            script_type='Shell',
            job_id=job_id,
            notify_channels=[],
            timeout=10 if remote_finished else 1,
            host='example.invalid',
            username='probe',
            password='not-used',
            task_id=task_id,
            run_id=run_id,
            source='schedule',
            source_job_id=job_id,
        )
        result = SysApschedulerResults.get(SysApschedulerResults.job_id == job_id)
        run = TaskRun.get_by_id(run_id)
        task = TaskDefinition.get_by_id(task_id)
        assert result.status == expected_status
        assert run.status == ('succeeded' if expected_status == 'success' else 'unknown')
        if expected_status == 'unknown':
            assert task.guard_state == 'unknown'
            assert task.active_run_id == run_id
        else:
            assert task.guard_state == 'clear'
            assert task.active_run_id is None
        from database.sql_db.entity.table_task_center import TaskRunLog
        assert TaskRunLog.select().where(TaskRunLog.run_id == run_id).count() == 0
        assert SysApschedulerRunning.select().count() == 0
        assert channel.closed
        assert ssh.closed
        assert any(command.startswith('rm -f -- /tmp/dashgo_') for command in ssh.commands)
        assert not list((tmp_path / 'dashgo_sshscript_cache').glob('dashgo_*'))
        database.close()


def test_date_job_can_be_serialized():
    from apscheduler.triggers.date import DateTrigger
    from app_apscheduler import SchedulerService

    job = SimpleNamespace(trigger=DateTrigger(), id='probe', next_run_time=None, kwargs={})
    assert SchedulerService.get_job_dict(job)['trigger'] == 'date'


@pytest.mark.parametrize('module', ['access_.user_mgmt_c', 'access_.role_mgmt_c', 'task_.task_mgmt_c', 'setting_.notify_api_c'])
def test_management_callback_guard_blocks_unauthorized_execution(module):
    from common.utilities.util_dash import guard_callback
    from dash.exceptions import PreventUpdate

    def sensitive_operation():
        pytest.fail('Unauthorized operation executed')

    sensitive_operation.__module__ = 'dash_callback.application.' + module
    with patch('common.utilities.util_menu_access.get_menu_access', return_value=SimpleNamespace(has_access=lambda permission: False)):
        with pytest.raises(PreventUpdate):
            guard_callback(sensitive_operation)()


def test_avatar_rejects_path_traversal():
    from common.utilities.util_file_trans import AvatarFile

    with pytest.raises(ValueError):
        AvatarFile.save_avatar_file('', 'png', '../probe')


def test_windows_command_failure_is_not_success():
    import subprocess
    from dash_components.windows_tasks_manager import WindowsTasksManager

    with patch('dash_components.windows_tasks_manager.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['schtasks'])):
        with pytest.raises(RuntimeError):
            WindowsTasksManager().run_command(['schtasks'])


def test_windows_task_import_normalizes_supported_schedule():
    from dash_components.windows_tasks_manager import WindowsTasksManager
    from tools.task_transformer import TaskScheduler
    import pandas as pd

    raw_task = {
        'TaskName': '\\Nightly',
        'Status': 'Ready',
        'Next Run Time': '2026-10-03 03:15:00',
        'Last Run Time': '2026-10-02 03:15:00',
        'Last Result': '0',
        'Author': 'HOST\\operator',
        'HostName': 'HOST',
        'Triggers': [{
            'Type': 'MSFT_TaskDailyTrigger',
            'StartBoundary': '2026-10-01T03:15:00',
            'EndBoundary': '2027-10-01T04:15:00',
            'DaysInterval': 1,
            'RepetitionInterval': '',
        }],
        'Actions': [{
            'Type': 'MSFT_TaskExecAction',
            'Execute': r'C:\Program Files\Acme\worker.exe',
            'Arguments': '--sync',
            'WorkingDirectory': r'C:\jobs',
        }],
        'ExecutionTimeLimit': 'PT1H30M',
    }

    normalized = WindowsTasksManager._normalize_import_task(raw_task)
    assert normalized['Import Supported'] is True
    assert normalized['Schedule Type'] == 'Daily'
    assert normalized['Start Time'] == '03:15:00'
    assert normalized['Start Date'] == '2026/10/01'
    assert normalized['Start DateTime'] == '2026-10-01T03:15:00'
    assert normalized['End DateTime'] == '2027-10-01T04:15:00'
    assert normalized['Task To Run'] == '"C:\\Program Files\\Acme\\worker.exe" --sync'
    assert normalized['Start In'] == r'C:\jobs'
    assert normalized['Execution Time'] == 5400

    transformer = TaskScheduler.__new__(TaskScheduler)
    transformer.scheduled_tasks_df = pd.DataFrame([normalized])
    transformer.process_tasks()
    assert transformer.scheduled_tasks_df.iloc[0]['Cron'] == '15 03 * * *'
    assert transformer.scheduled_tasks_df.iloc[0]['Execution Time'] == 5400
    assert transformer.unsupported_tasks.empty

    with patch('tools.task_transformer.add_local_cron_job') as add_job:
        transformer.register_jobs_to_apscheduler()
    add_job.assert_called_once()
    assert add_job.call_args.kwargs['start_date'] == '2026-10-01T03:15:00'
    assert add_job.call_args.kwargs['end_date'] == '2027-10-01T04:15:00'
    assert add_job.call_args.kwargs['is_pause'] is True


def test_windows_task_import_rejects_unsupported_trigger_shapes():
    from dash_components.windows_tasks_manager import WindowsTasksManager
    from tools.task_transformer import TaskScheduler
    import pandas as pd

    raw_task = {
        'TaskName': '\\MultiTrigger',
        'Triggers': [
            {'Type': 'MSFT_TaskDailyTrigger', 'StartBoundary': '2026-10-01T03:15:00'},
            {'Type': 'MSFT_TaskDailyTrigger', 'StartBoundary': '2026-10-02T03:15:00'},
        ],
        'Actions': [{'Type': 'MSFT_TaskExecAction', 'Execute': 'worker.exe'}],
        'ExecutionTimeLimit': 'PT5M',
    }

    normalized = WindowsTasksManager._normalize_import_task(raw_task)
    assert normalized['Import Supported'] is False
    assert 'exactly one trigger' in normalized['Import Issue']

    transformer = TaskScheduler.__new__(TaskScheduler)
    transformer.scheduled_tasks_df = pd.DataFrame([normalized])
    transformer.process_tasks()
    assert transformer.scheduled_tasks_df.empty
    assert transformer.unsupported_tasks.iloc[0]['TaskName'] == '\\MultiTrigger'


def test_windows_task_registration_defaults_to_paused():
    from tools.task_transformer import TaskScheduler

    assert TaskScheduler.register_jobs_to_apscheduler.__defaults__ == (True,)


def test_windows_disabled_task_stays_paused_when_activation_is_requested():
    from dash_components.windows_tasks_manager import WindowsTasksManager
    from tools.task_transformer import TaskScheduler
    import pandas as pd

    raw_task = {
        'TaskName': '\\DisabledTask',
        'Status': 'Disabled',
        'Triggers': [{
            'Type': 'MSFT_TaskDailyTrigger',
            'StartBoundary': '2026-10-01T03:15:00',
            'EndBoundary': '',
            'DaysInterval': 1,
        }],
        'Actions': [{'Type': 'MSFT_TaskExecAction', 'Execute': 'worker.exe'}],
    }
    normalized = WindowsTasksManager._normalize_import_task(raw_task)
    transformer = TaskScheduler.__new__(TaskScheduler)
    transformer.scheduled_tasks_df = pd.DataFrame([normalized])
    transformer.process_tasks()

    with patch('tools.task_transformer.add_local_cron_job') as add_job:
        transformer.register_jobs_to_apscheduler(is_pause=False)
    assert add_job.call_args.kwargs['is_pause'] is True


def test_windows_task_import_requires_schedule_contract():
    from tools.task_transformer import TaskScheduler
    import pandas as pd

    transformer = TaskScheduler.__new__(TaskScheduler)
    transformer.scheduled_tasks_df = pd.DataFrame([{'TaskName': '\\Incomplete'}])
    with pytest.raises(ValueError, match='Schedule Type'):
        transformer.process_tasks()


def test_windows_task_job_ids_use_full_path_and_fit_scheduler_limit():
    from tools.task_transformer import TaskScheduler

    first = TaskScheduler._build_job_id('\\FolderA\\Nightly')
    second = TaskScheduler._build_job_id('\\FolderB\\Nightly')
    assert first != second
    assert TaskScheduler._build_job_id('\\FolderA\\Nightly') == first
    assert len(TaskScheduler._build_job_id('\\' + 'a' * 500)) <= 191


def test_sqlite_configured_path_is_independent_of_current_directory(tmp_path, monkeypatch):
    from config.dashgo_conf import PathProj

    expected = (PathProj.ROOT_PATH.parent / 'app.db').resolve()
    monkeypatch.chdir(tmp_path)
    assert PathProj.resolve_sqlite_db_path('../app.db') == expected


def test_scheduler_process_lock_is_exclusive_and_released(tmp_path):
    from common.utilities.util_process_lock import ProcessFileLock

    path = tmp_path / 'scheduler.lock'
    first_lock = ProcessFileLock(path).acquire()
    try:
        with pytest.raises(RuntimeError, match='Another scheduler process'):
            ProcessFileLock(path).acquire()
    finally:
        first_lock.release()

    with ProcessFileLock(path):
        assert path.exists()


def test_listen_checkpoint_persists_and_updates(tmp_path):
    from datetime import datetime, timedelta
    from peewee import SqliteDatabase
    from database.sql_db.dao import dao_listen_task
    from database.sql_db.entity.table_listen_task import ApschedulerListenCheckpoint

    database = SqliteDatabase(tmp_path / 'listen-checkpoint.db')
    with database.bind_ctx([ApschedulerListenCheckpoint]), patch.object(dao_listen_task, 'db', return_value=database):
        dao_listen_task.ensure_listen_checkpoint_table()
        assert dao_listen_task.get_listen_checkpoint('mailbox') is None
        first = datetime(2026, 10, 2, 10, 0, 0)
        second = first + timedelta(minutes=1)
        dao_listen_task.set_listen_checkpoint('mailbox', first)
        assert dao_listen_task.get_listen_checkpoint('mailbox') == first
        dao_listen_task.set_listen_checkpoint('mailbox', second)
        assert dao_listen_task.get_listen_checkpoint('mailbox') == second
        database.close()


def test_listen_task_edit_updates_existing_row_atomically(tmp_path):
    from datetime import datetime
    from peewee import SqliteDatabase
    from database.sql_db.dao import dao_listen_task
    from database.sql_db.entity.table_listen_task import ApschedulerJobsActiveListen

    database = SqliteDatabase(tmp_path / 'listen-task-update.db')
    with database.bind_ctx([ApschedulerJobsActiveListen]), patch.object(dao_listen_task, 'db', return_value=database):
        database.create_tables([ApschedulerJobsActiveListen])
        now = datetime.now()
        ApschedulerJobsActiveListen.create(
            type='local', job_id='listen-edit', status=True, script_text='print(1)', script_type='Python',
            update_by='user', update_datetime=now, create_by='user', create_datetime=now,
            notify_channels='[]', extract_names='[]', timeout=10, host='', port=22,
            username='', password='', listen_channels='["mailbox"]', listen_keyword='old',
        )
        dao_listen_task.update_activa_listen_job(
            'listen-edit', script_text='print(2)', listen_keyword='new', update_datetime=now,
        )
        updated = ApschedulerJobsActiveListen.get(
            ApschedulerJobsActiveListen.job_id == 'listen-edit'
        )
        assert updated.script_text == 'print(2)'
        assert updated.listen_keyword == 'new'
        assert ApschedulerJobsActiveListen.select().count() == 1
        database.close()


def test_email_listen_dispatch_id_is_stable_and_scoped():
    from common.listen import _listen_run_job_id

    first = _listen_run_job_id('mail-a', 'task-1', 'uidl-1')
    assert _listen_run_job_id('mail-a', 'task-1', 'uidl-1') == first
    assert _listen_run_job_id('mail-a', 'task-2', 'uidl-1') != first
    assert _listen_run_job_id('mail-b', 'task-1', 'uidl-1') != first
    assert _listen_run_job_id('mail-a', 'task-1', 'uidl-2') != first


def test_email_listen_skips_already_dispatched_message():
    from common.listen import email_to_run_date_job

    email = {
        'uidl': 'uidl-probe',
        'subject': 'daily report',
        'datetime': __import__('datetime').datetime.now().astimezone(),
        'from': 'sender@example.test',
        'context': 'body',
    }
    jobs = [{'job_id': 'listener-1', 'listen_keyword': 'report', 'type': 'local'}]
    with patch('common.listen.dao_apscheduler.has_job_result', return_value=True), \
            patch('common.listen.get_job') as get_job, \
            patch('common.listen.add_local_date_job') as add_job:
        email_to_run_date_job(email, jobs, 'mailbox')
    get_job.assert_not_called()
    add_job.assert_not_called()


def test_email_listen_checkpoint_advances_only_after_success():
    import json
    from datetime import datetime
    from types import SimpleNamespace
    from common.listen import active_listen

    listen_task = SimpleNamespace(
        status=True,
        listen_channels=json.dumps(['mailbox']),
        job_id='listener-1',
        listen_keyword='report',
        type='local',
        script_text='print(1)',
        script_type='Python',
        notify_channels='[]',
        extract_names='[]',
        timeout=10,
        host='',
        port=22,
        username='',
        password='',
    )
    listen_api = SimpleNamespace(
        api_name='mailbox',
        api_type='邮件POP3协议',
        enable=True,
        params_json=json.dumps({
            'pop3_server': 'mail.example.test',
            'port': 995,
            'email_account': 'user@example.test',
            'password': 'not-used',
        }),
    )
    email = {
        'uidl': 'uidl-probe',
        'subject': 'daily report',
        'datetime': datetime.now().astimezone(),
        'from': 'sender@example.test',
        'context': 'body',
    }
    with patch('common.listen.get_activa_listen_job', return_value=[listen_task]), \
            patch('common.listen.get_listen_api_by_name', return_value=[listen_api]), \
            patch('common.listen.dao_listen_task.get_listen_checkpoint', return_value=None), \
            patch('common.listen.email_pop3.get_email_context_from_subject_during', return_value=[email]), \
            patch('common.listen.email_to_run_date_job') as dispatch, \
            patch('common.listen.dao_listen_task.set_listen_checkpoint') as checkpoint:
        active_listen()
    dispatch.assert_called_once()
    checkpoint.assert_called_once()

    with patch('common.listen.get_activa_listen_job', return_value=[listen_task]), \
            patch('common.listen.get_listen_api_by_name', return_value=[listen_api]), \
            patch('common.listen.dao_listen_task.get_listen_checkpoint', return_value=None), \
            patch('common.listen.email_pop3.get_email_context_from_subject_during', return_value=[email]), \
            patch('common.listen.email_to_run_date_job', side_effect=RuntimeError('dispatch failed')), \
            patch('common.listen.dao_listen_task.set_listen_checkpoint') as checkpoint:
        with pytest.raises(RuntimeError, match='dispatch failed'):
            active_listen()
    checkpoint.assert_not_called()


def test_pop3_reader_scans_older_messages_without_order_assumptions(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from email.message import EmailMessage
    from common.listen import email_pop3

    now = datetime.now(timezone.utc)
    messages = [
        EmailMessage(),
        EmailMessage(),
    ]
    messages[0]['From'] = 'first@example.test'
    messages[0]['Subject'] = 'newer message'
    messages[0]['Date'] = (now - timedelta(minutes=1)).strftime('%a, %d %b %Y %H:%M:%S %z')
    messages[0]['Message-ID'] = '<newer@example.test>'
    messages[0].set_content('newer body')
    messages[1]['From'] = 'second@example.test'
    messages[1]['Subject'] = 'older but later in mailbox'
    messages[1]['Date'] = (now - timedelta(minutes=10)).strftime('%a, %d %b %Y %H:%M:%S %z')
    messages[1]['Message-ID'] = '<older@example.test>'
    messages[1].set_content('older body')

    class FakePop3:
        def __init__(self, *_args):
            self.quit_called = False

        def user(self, _value):
            pass

        def pass_(self, _value):
            pass

        def stat(self):
            return 2, 100

        def list(self):
            return b'+OK', [b'1 50', b'2 50'], 100

        def uidl(self):
            return b'+OK', [b'1 uidl-new', b'2 uidl-old'], 40

        def retr(self, message_number):
            message = messages[message_number - 1]
            return b'+OK', message.as_bytes().splitlines(), len(message.as_bytes())

        def quit(self):
            self.quit_called = True

    pop3 = FakePop3()
    monkeypatch.setattr(email_pop3.poplib, 'POP3_SSL', lambda *_args: pop3)
    result = email_pop3.get_email_context_from_subject_during(
        pop3_server='mail.example.test',
        port=995,
        emal_account='user@example.test',
        password='not-used',
        since_time=now - timedelta(minutes=5),
        before_time=now,
    )
    assert [message['uidl'] for message in result] == ['uidl-new']
    assert pop3.quit_called


@pytest.mark.parametrize('valid_proof', [True, False])
def test_scheduler_rpc_challenge_authenticator(valid_proof):
    import hashlib
    import hmac
    import socket
    import threading
    from common.utilities.util_scheduler_rpc import SchedulerRPCAuthenticator
    from rpyc.utils.authenticators import AuthenticationError

    secret = 'test-shared-secret-that-is-long-enough'
    authenticator = SchedulerRPCAuthenticator(secret)
    server_sock, client_sock = socket.socketpair()
    result = []

    def authenticate():
        try:
            result.append(authenticator(server_sock))
        except Exception as error:
            result.append(error)

    worker = threading.Thread(target=authenticate)
    worker.start()
    nonce = client_sock.recv(32)
    proof = hmac.new(secret.encode(), b'smart-agent-scheduler-rpc-v1:' + nonce, hashlib.sha256).digest()
    client_sock.sendall(proof if valid_proof else bytes(len(proof)))
    worker.join(timeout=5)
    client_sock.close()
    server_sock.close()

    assert not worker.is_alive()
    if valid_proof:
        assert result[0][1] == {'authenticated': True}
    else:
        assert isinstance(result[0], AuthenticationError)


def test_scheduler_rpc_secret_must_be_configured(monkeypatch):
    from common.utilities.util_scheduler_rpc import RPC_SECRET_ENV, connect_scheduler_rpc

    monkeypatch.delenv(RPC_SECRET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=RPC_SECRET_ENV):
        connect_scheduler_rpc('127.0.0.1', 8091)


def test_scheduler_rpc_add_job_uses_fixed_runner_and_json_arguments(monkeypatch):
    import app_apscheduler

    class FakeJob:
        id = 'rpc-probe'

        def pause(self):
            pytest.fail('paused jobs must be created paused')

    class FakeScheduler:
        def add_job(self, function, trigger, trigger_args, kwargs, id, replace_existing=False, **options):
            self.call = (function, trigger, trigger_args, kwargs, id, replace_existing, options)
            return FakeJob()

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr(app_apscheduler, 'scheduler', fake_scheduler, raising=False)
    service = app_apscheduler.SchedulerService()
    job_id = service.exposed_add_job(
        'date', {'run_date': '2026-10-02T12:00:00'},
        {'type': 'local', 'script_text': 'print(1)', 'script_type': 'Python', 'timeout': 10},
        'rpc-probe', True,
    )
    assert job_id == 'rpc-probe'
    assert fake_scheduler.call[0] is app_apscheduler.run_script
    assert fake_scheduler.call[1] == 'date'
    assert fake_scheduler.call[3]['job_id'] == 'rpc-probe'
    assert fake_scheduler.call[3]['script_text'] == 'print(1)'
    assert fake_scheduler.call[5] is False
    assert fake_scheduler.call[6]['next_run_time'] is None
    assert fake_scheduler.call[6]['max_instances'] == 1
    assert fake_scheduler.call[6]['coalesce'] is True
    assert fake_scheduler.call[6]['misfire_grace_time'] == app_apscheduler.NO_CATCHUP_MISFIRE_GRACE_SECONDS

    service.exposed_add_job(
        'interval', {'seconds': 60},
        {'type': 'local', 'script_text': 'print(2)', 'script_type': 'Python', 'timeout': 10},
        'rpc-probe', False, True,
    )
    assert fake_scheduler.call[3]['script_text'] == 'print(2)'
    assert fake_scheduler.call[5] is True
    assert 'next_run_time' not in fake_scheduler.call[6]


def test_scheduler_rpc_exposes_non_sensitive_health_status(monkeypatch):
    import json
    import app_apscheduler
    from apscheduler.schedulers.base import STATE_PAUSED

    fake_scheduler = SimpleNamespace(running=True, get_jobs=lambda: [SimpleNamespace(id='job-a'), SimpleNamespace(id='job-b')])
    monkeypatch.setattr(app_apscheduler, 'scheduler', fake_scheduler, raising=False)
    monkeypatch.setitem(app_apscheduler.SCHEDULER_STATUS, 'started_at', '2026-10-02T10:00:00+08:00')
    monkeypatch.setitem(app_apscheduler.SCHEDULER_STATUS, 'last_job_error', 'TimeoutError')

    status = json.loads(app_apscheduler.SchedulerService().exposed_get_status())
    assert status['running'] is True
    assert status['ready'] is True
    assert status['state'] == 'running'
    assert status['service'] == 'si-platform-scheduler'
    assert status['job_count'] == 2
    assert status['last_job_error'] == 'TimeoutError'
    assert 'script_text' not in status
    assert 'password' not in status

    fake_scheduler.state = STATE_PAUSED
    paused_status = json.loads(app_apscheduler.SchedulerService().exposed_get_status())
    assert paused_status['running'] is True
    assert paused_status['ready'] is True
    assert paused_status['state'] == 'paused'


def test_v1_schema_allows_only_paused_scheduler_for_maintenance():
    import app_apscheduler

    assert app_apscheduler._allow_task_center_recovery(2, start_paused=False) is True
    assert app_apscheduler._allow_task_center_recovery(1, start_paused=True) is False
    with pytest.raises(RuntimeError, match='start the scheduler paused'):
        app_apscheduler._allow_task_center_recovery(1, start_paused=False)


@pytest.mark.parametrize('trigger,trigger_args,job_kwargs', [
    ('python:os.system', {}, {'type': 'local', 'script_text': 'x', 'script_type': 'Python', 'timeout': 10}),
    ('date', {'run_date': '2026-10-02', 'id': 'override'}, {'type': 'local', 'script_text': 'x', 'script_type': 'Python', 'timeout': 10}),
    ('date', {}, {'type': 'local', 'script_text': 'x', 'script_type': 'Python', 'timeout': 10, 'func': 'os.system'}),
])
def test_scheduler_rpc_rejects_unapproved_job_shapes(trigger, trigger_args, job_kwargs):
    import app_apscheduler

    monkeypatch_scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: pytest.fail('unsafe job reached scheduler'))
    with patch.object(app_apscheduler, 'scheduler', monkeypatch_scheduler, create=True):
        service = app_apscheduler.SchedulerService()
        with pytest.raises(ValueError):
            service.exposed_add_job(trigger, trigger_args, job_kwargs, 'rpc-probe')


def test_scheduler_rpc_client_handshake_over_loopback(monkeypatch):
    import threading
    from common.utilities.util_scheduler_rpc import RPC_SECRET_ENV, SchedulerRPCAuthenticator, connect_scheduler_rpc
    from rpyc.core.service import Service
    from rpyc.utils.server import ThreadedServer

    class ProbeService(Service):
        def exposed_ping(self):
            return 'pong'

    secret = 'loopback-test-secret-with-at-least-32-bytes'
    server = ThreadedServer(
        ProbeService,
        hostname='127.0.0.1',
        port=0,
        authenticator=SchedulerRPCAuthenticator(secret),
        protocol_config={'allow_pickle': False, 'allow_public_attrs': False},
    )
    server_thread = threading.Thread(target=server.start, daemon=True)
    server_thread.start()
    connection = None
    try:
        monkeypatch.setenv(RPC_SECRET_ENV, secret)
        connection = connect_scheduler_rpc(server.host, server.port)
        assert connection.root.ping() == 'pong'
        connection.close()
        connection = None

        monkeypatch.setenv(RPC_SECRET_ENV, 'wrong-secret-that-is-also-more-than-32-bytes')
        connection = connect_scheduler_rpc(server.host, server.port)
        with pytest.raises(Exception):
            connection.root.ping()
        connection.close()
        connection = None
    finally:
        if connection:
            connection.close()
        server.close()
        server_thread.join(timeout=5)
    assert not server_thread.is_alive()


def test_windows_page_uses_supported_table_properties():
    from dash_view.application.task_ import windows_scheduler

    layout = windows_scheduler.render_content(None)
    table = next(child for child in layout.children if getattr(child, 'id', None) == 'tc-windows-table')
    assert table.columns[-1]['renderOptions']['renderType'] == 'button'
    assert 'scroll' not in table.to_plotly_json()['props']


def test_log_page_renders_history_when_scheduler_offline():
    from dash_callback.application.task_ import task_log_c
    from server import server

    with server.test_request_context('/'), \
            patch.object(task_log_c, '_access', return_value=object()), \
            patch('common.task_center.list_runs', return_value={'rows': [], 'total': 0}) as query:
        rows, total, notice = task_log_c.refresh_runs.__wrapped__(0, 1, '', '', 1, 20, {})
    assert rows == [] and total == 0 and notice is None
    query.assert_called_once()


def test_log_job_ids_include_completed_and_running_tasks(tmp_path):
    from peewee import SqliteDatabase
    from datetime import datetime
    from database.sql_db.dao import dao_apscheduler
    from database.sql_db.entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning

    database = SqliteDatabase(tmp_path / 'history.db')
    models = [SysApschedulerResults, SysApschedulerRunning]
    with database.bind_ctx(models), patch.object(dao_apscheduler, 'db', return_value=database):
        database.create_tables(models)
        now = datetime.now()
        SysApschedulerResults.create(job_id='archived', status='success', log='', start_datetime=now, finish_datetime=now)
        SysApschedulerRunning.create(job_id='running', log='', start_datetime=now, order=0)
        assert dao_apscheduler.get_log_job_ids() == ['running', 'archived']
        database.close()


def test_scheduler_startup_recovers_stale_running_logs(tmp_path):
    from datetime import datetime
    from peewee import SqliteDatabase
    from database.sql_db.dao import dao_apscheduler
    from database.sql_db.entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning

    database = SqliteDatabase(tmp_path / 'stale-runs.db')
    models = [SysApschedulerResults, SysApschedulerRunning]
    start_datetime = datetime(2026, 10, 2, 10, 0, 0)
    with database.bind_ctx(models), patch.object(dao_apscheduler, 'db', return_value=database):
        database.create_tables(models)
        SysApschedulerRunning.create(job_id='interrupted-job', log='part-1', order=0, start_datetime=start_datetime)
        SysApschedulerRunning.create(job_id='interrupted-job', log='part-2', order=1, start_datetime=start_datetime)
        dao_apscheduler.recover_stale_runs()
        result = SysApschedulerResults.get(SysApschedulerResults.job_id == 'interrupted-job')
        assert result.status == 'error'
        assert result.log.startswith('part-1part-2')
        assert 'Scheduler process stopped' in result.log
        assert SysApschedulerRunning.select().count() == 0
        database.close()


def test_windows_rejects_task_outside_current_account():
    from dash_components.windows_tasks_manager import WindowsTasksManager

    manager = WindowsTasksManager()
    with patch.object(manager, 'get_scheduled_tasks', return_value=[]), patch.object(manager, 'run_command') as command:
        with pytest.raises(ValueError):
            manager.delete_task('foreign-task')
        command.assert_not_called()


def test_existing_encryption_format_roundtrip():
    from common.utilities.util_encrypt import encrypt_data, decrypt_data
    import secrets

    key = secrets.token_hex(32)
    assert decrypt_data(encrypt_data('integration-probe', key), key) == 'integration-probe'


def test_login_throttle_cannot_be_reset_by_client_counter():
    from dash_callback.pages.login_c import allow_login_attempt, login_attempts
    from server import server

    login_attempts.clear()
    with server.test_request_context('/'):
        assert all(allow_login_attempt('rate-probe') for attempt in range(10))
        assert not allow_login_attempt('rate-probe')
    login_attempts.clear()


def test_real_windows_task_lifecycle():
    import os
    import subprocess
    from uuid import uuid4
    from dash_components.windows_tasks_manager import WindowsTasksManager

    if os.name != 'nt' or os.environ.get('PLATFORM_WINDOWS_PROBE') != '1':
        pytest.skip('Set PLATFORM_WINDOWS_PROBE=1 to authorize temporary Windows task operations')
    name = 'PlatformProbe_' + uuid4().hex
    manager = WindowsTasksManager()
    register = f"$ErrorActionPreference='Stop'; $action=New-ScheduledTaskAction -Execute ($env:SystemRoot+'\\System32\\cmd.exe') -Argument '/c exit 0'; $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited; Register-ScheduledTask -TaskName '{name}' -Action $action -Principal $principal | Out-Null"
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', register], check=True, capture_output=True, timeout=30)
    try:
        task_name = '\\' + name
        assert any(task['TaskName'] == task_name for task in manager.get_scheduled_tasks())
        manager.start_task(task_name)
        wait = f"$ErrorActionPreference='Stop'; $service=New-Object -ComObject 'Schedule.Service'; $service.Connect(); $task=$service.GetFolder('\\').GetTask('{name}'); $deadline=[DateTime]::UtcNow.AddSeconds(15); while ($task.GetInstances(0).Count -gt 0) {{ if ([DateTime]::UtcNow -gt $deadline) {{ throw 'Probe did not finish' }}; [System.Threading.Thread]::Yield() | Out-Null }}; $task.LastTaskResult"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', wait], check=True, capture_output=True, text=True, timeout=20)
        assert result.stdout.strip() == '0'
        manager.pause_task(task_name)
        assert next(task for task in manager.get_scheduled_tasks() if task['TaskName'] == task_name)['Status'] == 'Disabled'
        manager.ready_task(task_name)
    finally:
        subprocess.run(['schtasks', '/Delete', '/TN', '\\' + name, '/F'], check=True, capture_output=True, timeout=30)
    assert not any(task['TaskName'] == '\\' + name for task in manager.get_scheduled_tasks())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def test_oauth_protected_handler_returns_response():
    from common.utilities.util_oauth2 import require_oauth

    token = SimpleNamespace(scope='profile', is_valid=lambda: True)
    with patch('common.utilities.util_oauth2.current_token', return_value=token):
        handler = require_oauth('profile')(lambda: ({'ok': True}, 200))
        assert handler() == ({'ok': True}, 200)


@pytest.mark.parametrize('scope,valid', [('other', True), ('profile', False)])
def test_oauth_rejects_invalid_scope_or_expired_token(scope, valid):
    from common.exception import OAuth2Error
    from common.utilities.util_oauth2 import require_oauth

    token = SimpleNamespace(scope=scope, is_valid=lambda: valid)
    with patch('common.utilities.util_oauth2.current_token', return_value=token):
        handler = require_oauth('profile')(lambda: pytest.fail('Unauthorized handler executed'))
        with pytest.raises(OAuth2Error):
            handler()