import hashlib
import json
import csv
import io
from zoneinfo import ZoneInfo
from datetime import datetime
from uuid import uuid4

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database.sql_db.conn import db
from database.sql_db.dao import dao_task_center
from database.sql_db.entity.table_user import SysGroup, SysGroupUser, SysRole, SysUser, SysUserRole


ALLOWED_OPERATIONS = {
    'view', 'edit', 'run', 'pause', 'resume', 'delete', 'logs', 'export', 'claim', 'resolve', 'grant', 'transfer'
}
PAGE_PERMISSION = '任务管理-页面'
MAX_PAGE_SIZE = 100
MAX_LOG_CHARS = 1_000_000
RUN_STATUS_LABELS = {'requested': '待提交', 'submitted': '已提交', 'running': '运行中',
    'succeeded': '成功', 'failed': '失败', 'timeout': '超时', 'cancelled': '未执行 / 已取消',
    'unknown': '结果未知', 'skipped': '已跳过', 'ended': '人工确认结束', 'accepted': '原生命令已受理'}
SOURCE_LABELS = {'manual': '手动', 'schedule': '定时', 'agent': 'Agent审批', 'listen': '监听',
                 'retry': '重试', 'legacy': '旧历史'}


def display_timestamp(value, timezone='Asia/Shanghai'):
    if not value:
        return '未记录'
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        return parsed.astimezone(ZoneInfo(timezone)).strftime('%Y-%m-%d %H:%M:%S %Z')
    except (TypeError, ValueError):
        return str(value)


class TaskCenterError(ValueError):
    pass


def _user(access):
    username = getattr(access, 'user_name', None)
    if not isinstance(username, str) or not username:
        raise TaskCenterError('登录身份无效。')
    database = db()
    with database.connection_context():
        if not SysUser.select().where((SysUser.user_name == username)
                                      & (SysUser.user_status == True)).exists():
            raise TaskCenterError('账户不存在或已停用。')
    if not callable(getattr(access, 'has_access', None)) or not any(access.has_access(permission) for permission in
            (PAGE_PERMISSION, '任务日志-页面', 'rpa_scheduler', 'Windows任務管理-页面')):
        raise TaskCenterError('没有任务中心页面权限。')
    return username


def _is_admin(username):
    database = db()
    with database.connection_context():
        return (SysUserRole.select()
                .join(SysRole, on=(SysUserRole.role_name == SysRole.role_name))
                .join(SysUser, on=(SysUserRole.user_name == SysUser.user_name))
                .where((SysUserRole.user_name == username) & (SysRole.role_name == 'admin')
                       & (SysRole.role_status == True) & (SysUser.user_status == True)).exists())


def _groups(username):
    database = db()
    with database.connection_context():
        return list(SysGroup.select(SysGroup.group_name).join(
            SysGroupUser, on=(SysGroup.group_name == SysGroupUser.group_name)).join(
                SysUser, on=(SysGroupUser.user_name == SysUser.user_name)).where(
                    (SysGroupUser.user_name == username) & (SysGroup.group_status == True)
                    & (SysUser.user_status == True)))


def _group_active(group_name):
    database = db()
    with database.connection_context():
        return SysGroup.select().where((SysGroup.group_name == group_name)
                                       & (SysGroup.group_status == True)).exists()


def _require_group(access, group_name, operation):
    username = _user(access)
    if operation not in {'view', 'logs', 'export'} and not (
            access.has_access(PAGE_PERMISSION) or access.has_access('Windows任務管理-页面')):
        raise TaskCenterError('没有任务操作页面权限。')
    if _is_admin(username):
        if not _group_active(group_name):
            raise TaskCenterError('团队不存在或已停用。')
        return username
    if not any(group.group_name == group_name for group in _groups(username)):
        raise TaskCenterError('团队不存在或无权访问。')
    if operation not in dao_task_center.get_grant(group_name, username):
        raise TaskCenterError('团队未授予此操作。')
    return username


def _task_access(access, task, operation):
    if task is None or not task.claimed or task.deleted:
        raise TaskCenterError('任务不存在或无权访问。')
    return _require_group(access, task.group_name, operation)


def _run_access(access, run, operation):
    if run is None:
        raise TaskCenterError('运行不存在或无权访问。')
    username = _user(access)
    if operation in {'logs', 'export'} and not access.has_access('任务日志-页面'):
        raise TaskCenterError('没有任务日志页面权限。')
    if _is_admin(username):
        return username
    if not any(group.group_name == run.group_name for group in _groups(username)):
        raise TaskCenterError('运行不存在或无权访问。')
    if operation not in dao_task_center.get_grant(run.group_name, username):
        raise TaskCenterError('团队未授予此操作。')
    return username


def _page(page, page_size):
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise TaskCenterError('页码无效。')
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise TaskCenterError(f'每页数量必须在1至{MAX_PAGE_SIZE}之间。')
    return page, page_size


def _digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _platform_job_rows():
    from common.utilities.util_apscheduler import get_connect

    connection = get_connect()
    try:
        rows = json.loads(connection.root.get_jobs())
    finally:
        connection.close()
    from database.sql_db.dao import dao_listen_task
    for listener in dao_listen_task.get_activa_listen_job():
        payload = {key: getattr(listener, key, None) for key in
                   ['type', 'script_text', 'script_type', 'timeout', 'host', 'port', 'username',
                    'password', 'extract_names', 'notify_channels']}
        for key in ['extract_names', 'notify_channels']:
            payload[key] = json.loads(payload[key]) if isinstance(payload[key], str) and payload[key] else payload[key] or None
        payload['env_vars'] = None
        rows.append(dict(id=listener.job_id, status=bool(listener.status), trigger='listen',
                         next_run_time='', kwargs=payload, plan={
                             'listen_channels': json.loads(listener.listen_channels),
                             'listen_keyword': listener.listen_keyword}))
    return rows


def _payload(job):
    kwargs = job.get('kwargs') or {}
    keys = ('type', 'script_text', 'script_type', 'timeout', 'extract_names', 'notify_channels', 'env_vars')
    result = {key: kwargs.get(key) for key in keys}
    if result['type'] == 'ssh':
        for key in ('host', 'port', 'username', 'password'):
            result[key] = kwargs.get(key)
    return result


def _task_row(task, backend=None, last_status=''):
    if backend is None:
        name = task.backend_ref
        return {
            'task_id': task.task_id, 'job_id': task.backend_ref, 'name': name, 'engine': task.engine,
            'group_name': task.group_name, 'claimed': task.claimed, 'enabled': task.enabled,
            'type': '', 'script_type': '', 'trigger': '', 'plan': {}, 'next_run': '', 'timeout': 0,
            'active_run_id': task.active_run_id, 'guard_state': task.guard_state, 'last_status': '',
        }
    kwargs = backend.get('kwargs') or {}
    return {
        'task_id': task.task_id if task else '', 'job_id': backend.get('id', ''),
        'name': backend.get('name') or backend.get('id', ''), 'engine': 'platform',
        'group_name': task.group_name if task and task.claimed else '',
        'claimed': bool(task and task.claimed), 'enabled': backend.get('status') is True,
        'type': kwargs.get('type', ''), 'script_type': kwargs.get('script_type', ''),
        'trigger': backend.get('trigger', ''), 'plan': backend.get('plan') or {},
        'next_run': backend.get('next_run_time') or '', 'timeout': kwargs.get('timeout', 0),
        'active_run_id': task.active_run_id if task else None,
        'guard_state': task.guard_state if task else 'clear',
        'last_status': last_status,
    }


def _last_status(task_id):
    rows, _ = dao_task_center.list_runs(task_ids=[task_id], page=1, page_size=1)
    return rows[0].status if rows else ''


def _filter_page(rows, total, page, page_size):
    start = (page - 1) * page_size
    return rows[start:start + page_size], total


def list_tasks(access, engine='platform', keyword='', status='', page=1, page_size=20,
               team='', task_type='', task_id=None):
    page, page_size = _page(page, page_size)
    username = _user(access)
    admin = _is_admin(username)
    if task_id:
        task = dao_task_center.get_task(task_id)
        _task_access(access, task, 'view')
        if task.engine != engine:
            raise TaskCenterError('任务引擎不匹配。')
        return {'rows': [get_task(access, task_id)], 'total': 1, 'online': True, 'error': ''}
    if engine not in {'platform', 'windows'}:
        raise TaskCenterError('不支持的任务引擎。')
    if status not in {'', 'enabled', 'paused', 'unknown'}:
        raise TaskCenterError('任务状态无效。')
    if engine == 'windows':
        try:
            from dash_components.windows_tasks_manager import WindowsTasksManager
            backend_rows = WindowsTasksManager().get_scheduled_tasks()
            online, error = True, ''
        except Exception as exc:
            backend_rows, online, error = [], False, type(exc).__name__
        return _list_windows(access, backend_rows, online, error, keyword, status, page, page_size)
    try:
        backend_rows = _platform_job_rows()
        online, error = True, ''
    except Exception as exc:
        backend_rows, online, error = [], False, type(exc).__name__
    if not online:
        group_names = None if admin else [group.group_name for group in _groups(username)
                                         if 'view' in dao_task_center.get_grant(group.group_name, username)]
        if team:
            group_names = [team] if admin or team in (group_names or []) else []
        if task_type:
            return {'rows': [], 'total': 0, 'online': False, 'error': error}
        tasks, count = dao_task_center.list_tasks(group_names=group_names, engine=engine,
                                                  keyword=keyword, status=status, page=page,
                                                  page_size=page_size)
        return {'rows': [_task_row(task) for task in tasks], 'total': count, 'online': False, 'error': error}
    result = []
    mappings = dao_task_center.engine_snapshot('platform')
    allowed_groups = {group.group_name for group in _groups(username)
                      if 'view' in dao_task_center.get_grant(group.group_name, username)} if not admin else None
    active_groups = {row['value'] for row in list_teams(access)} if admin else set()
    for backend in backend_rows:
        task = mappings.get(backend.get('id', ''))
        if task and task.claimed:
            if task.group_name not in (active_groups if admin else allowed_groups):
                continue
        elif not admin:
            continue
        row = _task_row(task, backend)
        if team and row['group_name'] != team or task_type and row['type'] != task_type:
            continue
        if keyword and keyword.casefold() not in (row['name'] + row['job_id']).casefold():
            continue
        if status == 'enabled' and not row['enabled']:
            continue
        if status == 'paused' and row['enabled']:
            continue
        if status == 'unknown' and (task is None or task.guard_state != 'unknown'):
            continue
        result.append(row)
    result.sort(key=lambda row: (row['group_name'], row['name'].casefold()))
    page_rows = _filter_page(result, len(result), page, page_size)[0]
    statuses = dao_task_center.latest_statuses([row['task_id'] for row in page_rows if row['task_id']])
    for row in page_rows:
        row['last_status'] = statuses.get(row['task_id'], '')
    return {'rows': page_rows,
            'total': len(result), 'online': True, 'error': ''}


def _list_windows(access, backend_rows, online, error, keyword, status, page, page_size):
    username = _user(access)
    admin = _is_admin(username)
    result = []
    for backend in backend_rows:
        ref = backend.get('TaskName', '')
        task = dao_task_center.find_task('windows', ref)
        if task and task.claimed:
            try:
                _task_access(access, task, 'view')
            except TaskCenterError:
                continue
        elif not admin:
            continue
        task_id = task.task_id if task else ''
        next_run = backend.get('Next Run Time', '')
        row = {
            'task_id': task_id, 'job_id': ref, 'name': ref, 'engine': 'windows',
            'group_name': task.group_name if task and task.claimed else '',
            'claimed': bool(task and task.claimed), 'enabled': backend.get('Status') in {'Ready', 'Running'},
            'type': 'windows', 'script_type': '', 'trigger': backend.get('Schedule Type', ''),
            'plan': {'start': backend.get('Start DateTime', '')}, 'next_run': next_run,
            'timeout': backend.get('Execution Time', 0),
            'active_run_id': task.active_run_id if task else None, 'last_status': backend.get('Last Result', ''),
            'native_status': backend.get('Status', ''),
            'principal': backend.get('Principal', ''),
            'multiple_instances_policy': backend.get('MultipleInstancesPolicy', '不可用'),
        }
        if keyword and keyword.casefold() not in ref.casefold():
            continue
        if status == 'enabled' and not row['enabled'] or status == 'paused' and row['enabled']:
            continue
        result.append(row)
    result.sort(key=lambda row: row['name'].casefold())
    return {'rows': _filter_page(result, len(result), page, page_size)[0],
            'total': len(result), 'online': online, 'error': error}


def export_windows(access, keyword='', status=''):
    username = _user(access)
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['完整路径', '团队', '原生状态', '下次触发', 'LastTaskResult', '运行身份', '原生并发策略'])
    page = 1
    exported = 0
    while page <= 100:
        result = list_tasks(access, engine='windows', keyword=keyword, status=status,
                            page=page, page_size=100)
        if not result['online']:
            raise TaskCenterError('Windows服务不可用，未生成导出。')
        for row in result['rows']:
            task = dao_task_center.get_task(row['task_id']) if row['task_id'] else None
            if task is None:
                continue
            try:
                _task_access(access, task, 'export')
            except TaskCenterError:
                continue
            fields = [row[key] for key in ['name', 'group_name', 'native_status', 'next_run',
                                          'last_status', 'principal', 'multiple_instances_policy']]
            writer.writerow(["'" + str(value) if str(value).lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n'))
                             else str(value) for value in fields])
            exported += 1
        if page * 100 >= result['total']:
            break
        page += 1
    if result['total'] > 10_000:
        raise TaskCenterError('导出超过10000条，请先缩小筛选范围。')
    dao_task_center.audit(username, 'export_windows', 'engine', 'windows', result='ok', reason=f'rows={exported}')
    return '\ufeff' + output.getvalue()


def list_runs(access, task_id=None, keyword='', status='', page=1, page_size=20, run_id=None, since=None):
    page, page_size = _page(page, page_size)
    username = _user(access)
    admin = _is_admin(username)
    group_names = None if admin else [
        group.group_name for group in _groups(username)
        if 'view' in dao_task_center.get_grant(group.group_name, username)]
    task_ids = None
    if task_id:
        task = dao_task_center.get_task(task_id)
        _task_access(access, task, 'view')
        task_ids = [task_id]
    elif not admin:
        if not group_names:
            return {'rows': [], 'total': 0}
    rows, total = dao_task_center.list_runs(task_ids=task_ids, group_names=group_names,
                                             keyword=keyword, status=status,
                                             page=page, page_size=page_size, run_id=run_id, since=since)
    return {'rows': [
        {'run_id': run.run_id, 'task_id': run.task_id, 'job_id': run.job_id, 'name': run.name,
         'group_name': run.group_name, 'source': run.source, 'actor': run.actor, 'status': run.status,
         'requested_at': run.requested_at.isoformat() if run.requested_at else '',
         'started_at': run.started_at.isoformat() if run.started_at else '',
         'finished_at': run.finished_at.isoformat() if run.finished_at else '', 'duration': run.duration}
        for run in rows], 'total': total}


def claim_task(access, job_id, group_name, engine='platform'):
    username = _require_group(access, group_name, 'claim')
    if engine == 'platform':
        try:
            from common.utilities.util_apscheduler import get_apscheduler_all_jobs, start_stop_job
            job = next((item for item in get_apscheduler_all_jobs() if item.job_id == job_id), None)
            if job is None:
                raise TaskCenterError('任务不存在。')
            mapping = dao_task_center.find_task(engine, job_id)
            if mapping is None or not mapping.claimed:
                start_stop_job(job_id, False, job.trigger)
            digest = _digest(_payload(next(row for row in _platform_job_rows() if row.get('id') == job_id)))
            enabled = False if mapping is None or not mapping.claimed else job.status
        except TaskCenterError:
            raise
        except Exception as exc:
            raise TaskCenterError('调度服务不可用，未认领任务。') from exc
    elif engine == 'windows':
        from dash_components.windows_tasks_manager import WindowsTasksManager
        manager = WindowsTasksManager()
        row = next((item for item in manager.get_scheduled_tasks() if item.get('TaskName') == job_id), None)
        if row is None:
            raise TaskCenterError('Windows任务不存在。')
        mapping = dao_task_center.find_task(engine, job_id)
        digest, enabled = '', row.get('Status') in {'Ready', 'Running'}
    else:
        raise TaskCenterError('不支持的任务引擎。')
    task = dao_task_center.claim_task(uuid4().hex, engine, job_id, group_name, digest, enabled,
                                     username, include_legacy=_is_admin(username))
    return task.task_id


def get_task(access, task_id, operation='view'):
    if operation not in ALLOWED_OPERATIONS:
        raise TaskCenterError('任务操作无效。')
    task = dao_task_center.get_task(task_id)
    _task_access(access, task, operation)
    try:
        if task.engine == 'platform':
            backend = next((row for row in _platform_job_rows() if row.get('id') == task.backend_ref), None)
            if backend is None:
                raise TaskCenterError('后端任务已不存在。')
            return _task_row(task, backend)
        return _task_row(task)
    except TaskCenterError:
        raise
    except Exception as exc:
        raise TaskCenterError('任务详情暂不可用。') from exc


def task_capabilities(access, task_id):
    task = dao_task_center.get_task(task_id)
    username = _task_access(access, task, 'view')
    operations = set(ALLOWED_OPERATIONS) if _is_admin(username) else dao_task_center.get_grant(task.group_name, username)
    if not access.has_access('任务日志-页面'):
        operations -= {'logs', 'export'}
    if not (access.has_access(PAGE_PERMISSION) or access.has_access('Windows任務管理-页面')):
        operations &= {'view', 'logs', 'export'}
    if task.active_run_id or task.guard_state != 'clear':
        operations -= {'run', 'resume', 'edit', 'delete', 'transfer'}
    if task.enabled:
        operations.discard('transfer')
    if task.guard_state not in {'editing', 'unknown'} or task.active_run_id:
        operations.discard('resolve')
    return operations


def get_edit_values(access, task_id):
    task = dao_task_center.get_task(task_id)
    _task_access(access, task, 'edit')
    if task.engine != 'platform':
        raise TaskCenterError('Windows任务定义由原生计划程序维护。')
    backend = _backend_for(task.backend_ref)
    if backend is None:
        raise TaskCenterError('调度服务不可用，不能编辑任务。')
    payload = _payload(backend)
    if task.digest != _digest(payload):
        raise TaskCenterError('后端定义已变化，请管理员核实后重新认领。')
    payload.pop('password', None)
    plan = backend.get('plan') or {}
    return dict(payload, job_id=task.backend_ref, group_name=task.group_name,
                trigger=backend.get('trigger'), plan=plan,
                timezone=plan.get('timezone', 'Asia/Shanghai'), expected_digest=task.digest,
                expected_version=task.definition_version)


def run_capabilities(access, run_id):
    run = dao_task_center.get_run(run_id)
    operations = set()
    for operation in {'logs', 'export', 'resolve'}:
        try:
            _run_access(access, run, operation)
        except TaskCenterError:
            continue
        if operation != 'resolve' or run.status == 'unknown':
            operations.add(operation)
    return operations


def request_run(access, task_id, request_key, source='manual', expected_digest=None, run_id=None):
    username = _user(access)
    task = dao_task_center.get_task(task_id)
    _task_access(access, task, 'run')
    if not isinstance(request_key, str) or not request_key or len(request_key) > 191:
        raise TaskCenterError('运行请求键无效。')
    if run_id is not None and (not isinstance(run_id, str) or not run_id or len(run_id) > 32):
        raise TaskCenterError('运行ID无效。')
    if source not in {'manual', 'agent'}:
        raise TaskCenterError('不支持此运行来源。')
    duplicate = dao_task_center.get_requested_run(task_id, username, request_key)
    if duplicate:
        return {'status': duplicate.status, 'run_id': duplicate.run_id,
                'message': '已返回原运行；同一请求不会重复提交。'}
    if task.engine == 'windows':
        if source != 'manual':
            raise TaskCenterError('Agent不能提交Windows原生任务。')
        from dash_components.windows_tasks_manager import WindowsTasksManager
        manager = WindowsTasksManager()
        backend = next((row for row in manager.get_scheduled_tasks()
                        if row.get('TaskName') == task.backend_ref), None)
        if not backend or backend.get('Status') not in {'Ready', 'Running'}:
            raise TaskCenterError('Windows任务不存在或已禁用。')
        run, created = dao_task_center.reserve_run(task_id, run_id or uuid4().hex, request_key,
                                                   source, username, task.digest, task.backend_ref)
        if not created:
            return {'status': run.status, 'run_id': run.run_id, 'message': '同一请求不会重复提交。'}
        try:
            manager.start_task(task.backend_ref)
        except Exception as exc:
            dao_task_center.finish_run(run.run_id, 'unknown', type(exc).__name__)
            return {'status': 'unknown', 'run_id': run.run_id, 'message': '原生提交结果未知，请人工核实；不会自动重试。'}
        dao_task_center.finish_run(run.run_id, 'accepted', 'Windows accepted command; completion not verified')
        return {'status': 'accepted', 'run_id': run.run_id, 'message': 'Windows已受理启动命令，不代表执行完成。'}
    try:
        backend = next((row for row in _platform_job_rows() if row.get('id') == task.backend_ref), None)
    except Exception as exc:
        raise TaskCenterError('调度服务不可用，未提交运行。') from exc
    if backend is None or backend.get('status') is not True:
        raise TaskCenterError('任务不存在或已暂停。')
    payload = _payload(backend)
    digest = _digest(payload)
    if expected_digest and expected_digest != digest or task.digest and task.digest != digest:
        raise TaskCenterError('任务定义已变化，请刷新后重试。')
    run_id = run_id or uuid4().hex
    run, created = dao_task_center.reserve_run(task_id, run_id, request_key, source, username, digest,
                                               task.backend_ref)
    if not created:
        return {'status': run.status, 'run_id': run.run_id,
                'message': '已返回相同幂等键对应的运行，不会重复提交。'}
    try:
        from common.utilities import util_apscheduler
        job_kwargs = dict(payload, task_id=task_id, run_id=run_id, source=source,
                          source_job_id=task.backend_ref)
        receipt = util_apscheduler._add_job('date', {'run_date': datetime.now().astimezone().isoformat()},
                                            job_kwargs, run_id)
        if receipt != run_id:
            raise TaskCenterError('调度器提交回执不匹配。')
        if not dao_task_center.mark_submitted(run_id):
            raise TaskCenterError('运行状态在调度提交期间发生变化。')
    except Exception:
        dao_task_center.finish_run(run_id, 'unknown', 'scheduler receipt was missing or ambiguous')
        return {'status': 'unknown', 'run_id': run_id,
                'message': '提交结果未知，已锁定任务且不会自动重试。'}
    return {'status': 'submitted', 'run_id': run_id,
            'message': '调度器已接收请求，不代表运行成功。'}


def dispatch_listen(job_id, event_key, env_vars):
    task = dao_task_center.find_task('platform', job_id)
    if task is None or not task.claimed or not task.enabled or not _group_active(task.group_name):
        raise TaskCenterError('监听任务未认领、已暂停或团队已停用。')
    duplicate = dao_task_center.get_requested_run(task.task_id, 'listener', event_key)
    if duplicate:
        return {'run_id': duplicate.run_id, 'status': duplicate.status}
    backend = _backend_for(job_id)
    if backend is None or backend.get('trigger') != 'listen' or backend.get('status') is not True:
        raise TaskCenterError('监听定义不可用。')
    payload = _payload(backend)
    if _digest(payload) != task.digest:
        raise TaskCenterError('监听定义已变化。')
    run_id = hashlib.sha256(event_key.encode('utf-8')).hexdigest()[:32]
    run, created = dao_task_center.reserve_run(task.task_id, run_id, event_key, 'listen', 'listener', task.digest)
    if not created:
        return {'run_id': run.run_id, 'status': run.status}
    from common.utilities.util_apscheduler import _add_job
    try:
        receipt = _add_job('date', {'run_date': datetime.now().astimezone().isoformat()},
            dict(payload, env_vars=env_vars, task_id=task.task_id, run_id=run_id,
                 source='listen', source_job_id=job_id), run_id)
        if receipt != run_id or not dao_task_center.mark_submitted(run_id):
            raise ValueError('listener receipt mismatch')
    except Exception:
        dao_task_center.finish_run(run_id, 'unknown', 'listener submission outcome was ambiguous')
        raise TaskCenterError('监听提交结果未知，禁止自动重试。')
    return {'run_id': run_id, 'status': 'submitted'}


def operate_task(access, task_id, operation, request_key=None):
    _user(access)
    if operation not in {'pause', 'resume', 'delete'}:
        raise TaskCenterError('不支持的任务操作。')
    task = dao_task_center.get_task(task_id)
    action = 'delete' if operation == 'delete' else operation
    username = _task_access(access, task, action)
    if operation == 'delete' and (task.active_run_id or task.guard_state != 'clear'):
        raise TaskCenterError('任务正在运行或状态未知，不能删除。')
    if operation == 'resume' and (task.active_run_id or task.guard_state != 'clear'):
        raise TaskCenterError('任务正在运行或状态未知，不能恢复。')
    if request_key is not None and (not isinstance(request_key, str) or not request_key or len(request_key) > 191):
        raise TaskCenterError('操作请求键无效。')
    from common.utilities import util_apscheduler
    locked = operation in {'resume', 'delete'}
    if locked and not dao_task_center.begin_definition_update(task_id, task.digest):
        raise TaskCenterError('任务状态已变化，请刷新后重试。')
    if operation == 'pause':
        dao_task_center.set_task_state(task_id, enabled=False, actor=username, action='pause_requested')
    try:
        if task.engine == 'windows':
            from dash_components.windows_tasks_manager import WindowsTasksManager
            manager = WindowsTasksManager()
            method = {'pause': manager.pause_task, 'resume': manager.ready_task,
                      'delete': manager.delete_task}[operation]
            method(task.backend_ref)
            result = dao_task_center.set_task_state(
                task_id, enabled=operation == 'resume' if operation != 'delete' else None,
                deleted=True if operation == 'delete' else None, actor=username, action=operation,
                expected_guard='editing' if locked else 'clear')
        elif operation == 'delete':
            backend = _backend_for(task.backend_ref)
            if backend is None:
                raise TaskCenterError('后端任务不存在。')
            util_apscheduler.remove_job(task.backend_ref, backend.get('trigger', 'cron'))
            result = dao_task_center.set_task_state(task_id, deleted=True, actor=username, action=operation,
                                                   expected_guard='editing')
        else:
            job = _backend_for(task.backend_ref)
            if job is None:
                raise TaskCenterError('后端任务不存在。')
            util_apscheduler.start_stop_job(task.backend_ref, operation == 'resume', job['trigger'])
            result = dao_task_center.set_task_state(task_id, enabled=operation == 'resume',
                                                   actor=username, action=operation,
                                                   expected_guard='editing' if locked else 'clear')
    except Exception as exc:
        if locked:
            dao_task_center.mark_definition_unknown(task_id)
        dao_task_center.audit(username, operation, 'task', task_id, task.group_name,
                              result='error', reason=type(exc).__name__)
        raise TaskCenterError('任务操作失败。') from exc
    return {'status': 'ok', 'task_id': result.task_id, 'enabled': result.enabled,
            'deleted': result.deleted}


def resolve_task_definition(access, task_id, reason):
    task = dao_task_center.get_task(task_id)
    username = _task_access(access, task, 'resolve')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise TaskCenterError('必须提供人工核实依据。')
    if task.active_run_id:
        raise TaskCenterError('请先核实未决运行。')
    if task.engine == 'windows':
        from dash_components.windows_tasks_manager import WindowsTasksManager
        rows = WindowsTasksManager().get_scheduled_tasks()
        backend = next((row for row in rows if row.get('TaskName') == task.backend_ref), None)
        if backend is None:
            dao_task_center.set_task_state(task_id, deleted=True, actor=username,
                                           action='resolve_deleted_definition', expected_guard='unknown')
            return {'status': 'deleted', 'message': '原生任务已不存在，平台映射已审计关闭。'}
        if backend.get('Status') != 'Disabled':
            raise TaskCenterError('请先在原生计划程序确认任务已禁用，再核实解锁；平台不会自动停用它。')
        dao_task_center.resolve_definition(task_id, task.digest, username, reason.strip())
        return {'status': 'paused', 'message': '已核实原生禁用状态，平台映射保持暂停。'}
    backend = _backend_for(task.backend_ref)
    if backend is None:
        rows = _platform_job_rows()
        if any(row.get('id') == task.backend_ref for row in rows):
            raise TaskCenterError('后端定义状态已变化，请刷新。')
        dao_task_center.set_task_state(task_id, deleted=True, actor=username,
                                       action='resolve_deleted_definition', expected_guard='unknown')
        return {'status': 'deleted', 'message': '已核实后端定义不存在，平台映射已审计关闭。'}
    from common.utilities.util_apscheduler import start_stop_job
    start_stop_job(task.backend_ref, False, backend['trigger'])
    dao_task_center.resolve_definition(task_id, _digest(_payload(backend)), username, reason.strip())
    return {'status': 'paused', 'message': '已核实定义并保持暂停，请核对计划后显式恢复。'}


def transfer_task(access, task_id, destination, reason):
    task = dao_task_center.get_task(task_id)
    username = _task_access(access, task, 'transfer')
    _require_group(access, destination, 'claim')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise TaskCenterError('转移必须提供原因。')
    dao_task_center.transfer_task(task_id, destination, username, reason.strip())
    return {'status': 'paused', 'message': '任务归属已转移，历史运行团队快照不变，任务保持暂停。'}


def can_manage_grants(access):
    try:
        username = _user(access)
        return _is_admin(username) or any('grant' in dao_task_center.get_grant(group.group_name, username)
                                         for group in _groups(username))
    except TaskCenterError:
        return False


def resolve_run(access, run_id, outcome, reason):
    run = dao_task_center.get_run(run_id)
    username = _run_access(access, run, 'resolve')
    resolved = dao_task_center.resolve_run(run_id, outcome, reason, username)
    return {'outcome': outcome, 'run_id': resolved.run_id, 'status': resolved.status,
            'message': '运行已人工核实，审计记录已保存。'}


def read_log(access, run_id, cursor=0, limit=100):
    run = dao_task_center.get_run(run_id)
    _run_access(access, run, 'logs')
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise TaskCenterError('日志游标无效。')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise TaskCenterError(f'日志数量必须在1至{MAX_PAGE_SIZE}之间。')
    chunks, next_cursor, run = dao_task_center.get_log_chunks(run_id, cursor, limit)
    return {'chunks': chunks, 'cursor': next_cursor, 'status': run.status,
            'complete': next_cursor >= run.log_sequence and run.status in {
                'succeeded', 'failed', 'timeout', 'cancelled', 'ended', 'skipped', 'accepted', 'unknown'}}


def export_log(access, run_id):
    run = dao_task_center.get_run(run_id)
    username = _run_access(access, run, 'export')
    chunks = []
    cursor = 0
    size = 0
    while size < MAX_LOG_CHARS:
        page, next_cursor, _ = dao_task_center.get_log_chunks(run_id, cursor, MAX_PAGE_SIZE)
        if not page:
            break
        for chunk in page:
            remaining = MAX_LOG_CHARS - size
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
            if size >= MAX_LOG_CHARS:
                break
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    text = ''.join(chunks)
    if size >= MAX_LOG_CHARS:
        text += '\n[EXPORT TRUNCATED: size limit reached; narrow the requested output.]\n'
    dao_task_center.audit(username, 'export_log', 'run', run_id, run.group_name,
                          result='ok', reason=f'chars={len(text)}')
    return text


def latest_log(access, job_id):
    task = dao_task_center.find_task('platform', job_id)
    _task_access(access, task, 'logs')
    rows, _ = dao_task_center.list_runs(task_ids=[task.task_id], page=1, page_size=1,
                                      statuses=['succeeded', 'failed', 'timeout', 'cancelled', 'ended'])
    run = rows[0] if rows else None
    if run is None:
        return None
    text = export_log(access, run.run_id)
    return {'status': 'ok', 'job_id': job_id, 'run_id': run.run_id, 'run_status': run.status,
            'start_datetime': run.started_at.isoformat() if run.started_at else '',
            'finish_datetime': run.finished_at.isoformat() if run.finished_at else '', 'log': text}


def grant_team(access, group_name, user_name, operations):
    username = _user(access)
    if not isinstance(operations, (list, tuple, set)) or any(
            not isinstance(item, str) or item not in ALLOWED_OPERATIONS for item in operations):
        raise TaskCenterError('团队操作授权列表无效。')
    if not _is_admin(username):
        _require_group(access, group_name, 'grant')
        if not set(operations).issubset(dao_task_center.get_grant(group_name, username) - {'grant'}):
            raise TaskCenterError('团队授权管理员只能委派自身已有的操作，不能提升权限。')
    if not _group_active(group_name):
        raise TaskCenterError('团队不存在或已停用。')
    database = db()
    with database.connection_context():
        if not SysUser.select().where((SysUser.user_name == user_name)
                                      & (SysUser.user_status == True)).exists():
            raise TaskCenterError('用户不存在或已停用。')
        if not SysGroupUser.select().where((SysGroupUser.group_name == group_name)
                                           & (SysGroupUser.user_name == user_name)).exists():
            raise TaskCenterError('用户不是该团队成员。')
    dao_task_center.upsert_grant(group_name, user_name, operations, username)


def list_teams(access):
    username = _user(access)
    database = db()
    with database.connection_context():
        query = SysGroup.select(SysGroup.group_name).where(SysGroup.group_status == True)
        if not _is_admin(username):
            query = query.join(SysGroupUser, on=(SysGroup.group_name == SysGroupUser.group_name)).where(
                SysGroupUser.user_name == username)
        return [{'label': row.group_name, 'value': row.group_name}
                for row in query.order_by(SysGroup.group_name)]


def save_task(access, values, task_id=None):
    if not isinstance(values, dict):
        raise TaskCenterError('任务定义无效。')
    username = _user(access)
    group_name = values.get('group_name')
    action = 'edit' if task_id else 'claim'
    _require_group(access, group_name, action)
    engine = values.get('engine', 'platform')
    if engine != 'platform':
        raise TaskCenterError('Windows任务定义由Windows任务计划程序维护。')
    job_id = values.get('job_id') or (dao_task_center.get_task(task_id).backend_ref if task_id else '')
    if not isinstance(job_id, str) or not job_id or len(job_id) > 191:
        raise TaskCenterError('任务ID无效。')
    existing = dao_task_center.get_task(task_id) if task_id else None
    previous = None
    if task_id:
        _task_access(access, existing, 'edit')
        if job_id != existing.backend_ref:
            raise TaskCenterError('编辑不能更改稳定任务ID。')
        previous = _backend_for(existing.backend_ref)
        if previous is None:
            raise TaskCenterError('调度服务不可用，不能编辑任务。')
        if existing.digest != _digest(_payload(previous)):
            raise TaskCenterError('后端定义已变化，请刷新后重试。')
    previous_payload = _payload(previous) if previous else {}
    payload = {
        'type': values.get('type', 'local'), 'script_text': values.get('script_text', ''),
        'script_type': values.get('script_type', 'Python'), 'timeout': values.get('timeout', 300),
        'extract_names': values.get('extract_names', previous_payload.get('extract_names')) or None,
        'notify_channels': values.get('notify_channels', previous_payload.get('notify_channels')) or None,
        'env_vars': values.get('env_vars', previous_payload.get('env_vars'))
                if values.get('type', 'local') == 'local' else None,
    }
    if payload['type'] == 'ssh':
        payload.update(host=values.get('host', ''), port=values.get('port', 22),
                       username=values.get('username', ''),
                       password=values.get('password', ''))
        if task_id and not payload['password'] and previous:
            payload['password'] = (previous.get('kwargs') or {}).get('password', '')
    if payload['type'] not in {'local', 'ssh'} or not isinstance(payload['script_text'], str):
        raise TaskCenterError('脚本配置无效。')
    if payload['script_type'] not in {'Python', 'Bat', 'Shell'}:
        raise TaskCenterError('不支持的脚本类型。')
    if isinstance(payload['timeout'], bool) or not isinstance(payload['timeout'], int) or not 1 <= payload['timeout'] <= 86400:
        raise TaskCenterError('超时秒数必须是1至86400的整数。')
    if payload['extract_names'] is not None and (not isinstance(payload['extract_names'], list)
            or any(not isinstance(item, (str, dict)) for item in payload['extract_names'])):
        raise TaskCenterError('提取配置必须是字符串或对象组成的列表。')
    if payload['notify_channels'] is not None and (not isinstance(payload['notify_channels'], list)
            or any(not isinstance(item, str) for item in payload['notify_channels'])):
        raise TaskCenterError('通知渠道必须是字符串列表。')
    if payload['env_vars'] is not None and (not isinstance(payload['env_vars'], dict)
            or any(not isinstance(key, str) or not isinstance(value, (str, int, float, bool))
                   for key, value in payload['env_vars'].items())):
        raise TaskCenterError('环境变量必须是字符串键和标量值组成的对象。')
    trigger = values.get('trigger')
    plan = values.get('plan') or {}
    if trigger == 'listen':
        if payload['env_vars']:
            raise TaskCenterError('监听任务环境变量由监听事件生成，不能配置静态环境变量。')
        payload['env_vars'] = None
        channels = plan.get('listen_channels')
        keyword = plan.get('listen_keyword')
        if not isinstance(channels, list) or not channels or any(not isinstance(item, str) or not item for item in channels):
            raise TaskCenterError('监听渠道必须是非空字符串列表。')
        if not isinstance(keyword, str) or not keyword.strip():
            raise TaskCenterError('监听关键词不能为空。')
        trigger_args = dict(listen_channels=channels, listen_keyword=keyword)
    else:
        trigger_args = _trigger_args(trigger, plan, values.get('timezone', 'Asia/Shanghai'))
    digest = _digest(payload)
    if task_id and existing is None:
        raise TaskCenterError('任务不存在或无权访问。')
    expected = values.get('expected_digest')
    if existing:
        _task_access(access, existing, 'edit')
        if existing.group_name != group_name:
            raise TaskCenterError('编辑任务不能同时转移团队。')
        if not expected or existing.digest != expected:
            raise TaskCenterError('任务定义已变化，请刷新后重试。')
        if values.get('expected_version', existing.definition_version) != existing.definition_version:
            raise TaskCenterError('任务计划版本已变化，请刷新后重试。')
        if (previous.get('trigger') == 'listen') != (trigger == 'listen'):
            raise TaskCenterError('监听与时间调度不能直接互换，请新建任务。')
    now = datetime.now().isoformat()
    job_kwargs = dict(payload, update_by=username, update_datetime=now, create_by=username, create_datetime=now)
    if not existing:
        job_kwargs.pop('update_by', None)
        job_kwargs.pop('update_datetime', None)
    else:
        previous = _backend_for(job_id)
        previous_kwargs = (previous or {}).get('kwargs') or {}
        job_kwargs['create_by'] = previous_kwargs.get('create_by', username)
        job_kwargs['create_datetime'] = previous_kwargs.get('create_datetime', now)
    from common.utilities.util_apscheduler import _add_job
    locked = False
    try:
        if existing:
            locked = dao_task_center.begin_definition_update(task_id, expected, existing.definition_version)
            if not locked:
                raise TaskCenterError('任务正在运行或定义已变化，请刷新后重试。')
        if trigger == 'listen':
            from database.sql_db.dao import dao_listen_task
            listener_values = {key: value for key, value in job_kwargs.items() if key != 'env_vars'}
            for key in ['extract_names', 'notify_channels']:
                listener_values[key] = json.dumps(listener_values.get(key), ensure_ascii=False)
            listener_values.update(status=False, listen_channels=json.dumps(channels, ensure_ascii=False),
                                   listen_keyword=keyword)
            if existing:
                dao_listen_task.update_activa_listen_job(job_id, **listener_values)
            else:
                dao_listen_task.insert_activa_listen_job(job_id=job_id,
                    update_by=username, update_datetime=now, **listener_values)
            receipt = job_id
        else:
            receipt = _add_job(trigger, trigger_args, job_kwargs, job_id,
                               is_pause=True, replace_existing=bool(existing))
        if receipt != job_id:
            raise TaskCenterError('调度器保存回执不匹配。')
        if existing:
            result = dao_task_center.finish_definition_update(task_id, expected, digest, username)
        else:
            result = dao_task_center.create_task(uuid4().hex, 'platform', job_id, group_name,
                                                 digest, enabled=False, claimed=True, actor=username)
    except Exception as exc:
        if locked:
            dao_task_center.mark_definition_unknown(task_id)
        dao_task_center.audit(username, 'save_task', 'task', job_id, group_name,
                              result='unknown', reason=type(exc).__name__)
        raise TaskCenterError('任务保存结果未知；任务保持暂停，请检查后再操作。') from exc
    return {'task_id': result.task_id, 'job_id': job_id, 'status': 'paused', 'digest': digest}


def _backend_for(job_id):
    try:
        return next((row for row in _platform_job_rows() if row.get('id') == job_id), None)
    except Exception:
        return None


def _trigger_args(trigger, plan, timezone):
    if not isinstance(plan, dict):
        raise TaskCenterError('任务计划无效。')
    values = dict(plan)
    values['timezone'] = timezone
    try:
        if trigger == 'cron':
            allowed = {'year', 'month', 'day', 'week', 'day_of_week', 'hour', 'minute', 'second',
                       'start_date', 'end_date', 'timezone'}
            if set(values) - allowed:
                raise TaskCenterError('Cron计划包含不支持的字段。')
            args = {key: value for key, value in values.items() if key in allowed}
            CronTrigger(**args)
            return args
        if trigger == 'interval':
            allowed = {'weeks', 'days', 'hours', 'minutes', 'seconds', 'start_date', 'end_date', 'timezone'}
            if set(values) - allowed:
                raise TaskCenterError('Interval计划包含不支持的字段。')
            args = {key: value for key, value in values.items() if key in allowed}
            constructor = IntervalTrigger(**args)
            durations = [args.get(key, 0) for key in ('weeks', 'days', 'hours', 'minutes', 'seconds')]
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                   for value in durations) or sum(durations) <= 0 or constructor.interval.total_seconds() <= 0:
                raise TaskCenterError('周期必须为正数。')
            return args
        if trigger == 'date':
            if set(values) - {'run_date', 'timezone'}:
                raise TaskCenterError('Date计划包含不支持的字段。')
            if not values.get('run_date'):
                raise TaskCenterError('Date计划必须指定run_date。')
            args = {'run_date': values.get('run_date'), 'timezone': timezone}
            DateTrigger(**args)
            return args
    except Exception as exc:
        raise TaskCenterError('触发计划无效。') from exc
    raise TaskCenterError('仅支持 cron、interval 和 date 触发器。')


def preview_schedule(trigger, plan, timezone='Asia/Shanghai', limit=5, until=None):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise TaskCenterError('预览数量无效。')
    args = _trigger_args(trigger, plan, timezone)
    now = datetime.now().astimezone()
    if trigger == 'cron':
        constructor = CronTrigger(**args)
    elif trigger == 'interval':
        constructor = IntervalTrigger(**args)
    else:
        constructor = DateTrigger(**args)
    points = []
    previous = None
    for _ in range(limit):
        point = constructor.get_next_fire_time(previous, previous or now)
        if point is None or until and point > until:
            break
        points.append(point.isoformat())
        previous = point
    return points