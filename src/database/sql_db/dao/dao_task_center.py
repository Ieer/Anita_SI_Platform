import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from peewee import IntegrityError, SqliteDatabase, fn

from ..conn import db
from ..entity.table_task_center import MODELS, TaskDefinition, TaskRun, TaskRunLog, TaskSchemaVersion, TaskTeamGrant


def ensure_schema():
    database = db()
    with database.connection_context():
        if not all(database.table_exists(model._meta.table_name) for model in MODELS):
            database.create_tables(MODELS, safe=True)
        try:
            version, _ = TaskSchemaVersion.get_or_create(component='task_center', defaults={'version': 2})
            if version.version != 2:
                raise ValueError('Task-center schema requires explicit maintenance migration to version 2.')
        except IntegrityError:
            TaskSchemaVersion.get_or_none(TaskSchemaVersion.component == 'task_center')


@contextmanager
def _session():
    database = db()
    if not database.is_closed():
        yield database
        return
    ensure_schema()
    with database.connection_context():
        yield database


def find_task(engine, backend_ref):
    with _session():
        return TaskDefinition.get_or_none(
            (TaskDefinition.engine == engine) & (TaskDefinition.backend_ref == backend_ref)
            & (TaskDefinition.deleted == False))


def engine_snapshot(engine):
    with _session():
        return {row.backend_ref: row for row in TaskDefinition.select().where(
            (TaskDefinition.engine == engine) & (TaskDefinition.deleted == False))}


def latest_statuses(task_ids):
    if not task_ids:
        return {}
    with _session():
        recent = TaskRun.select(TaskRun.task_id, fn.MAX(TaskRun.requested_at).alias('latest_time')).where(
            TaskRun.task_id.in_(task_ids)).group_by(TaskRun.task_id).alias('recent')
        rows = TaskRun.select(TaskRun.task_id, TaskRun.status).join(recent, on=(
            (TaskRun.task_id == recent.c.task_id) & (TaskRun.requested_at == recent.c.latest_time)))
        return {row.task_id: row.status for row in rows.order_by(TaskRun.run_id)}


def get_task(task_id):
    with _session():
        return TaskDefinition.get_or_none((TaskDefinition.task_id == task_id) & (TaskDefinition.deleted == False))


def list_tasks(group_names=None, include_unclaimed=False, engine='platform', keyword='', status='', page=1, page_size=20):
    with _session():
        query = TaskDefinition.select().where(
            (TaskDefinition.engine == engine) & (TaskDefinition.deleted == False))
        if group_names is not None:
            query = query.where(TaskDefinition.claimed == True, TaskDefinition.group_name.in_(group_names))
        elif not include_unclaimed:
            query = query.where(TaskDefinition.claimed == True)
        if keyword:
            query = query.where(TaskDefinition.backend_ref.contains(keyword))
        if status == 'enabled':
            query = query.where(TaskDefinition.enabled == True)
        elif status == 'paused':
            query = query.where(TaskDefinition.enabled == False)
        elif status == 'unknown':
            query = query.where(TaskDefinition.guard_state == 'unknown')
        total = query.count()
        rows = list(query.order_by(TaskDefinition.updated_at.desc()).paginate(page, page_size))
        return rows, total


def list_runs(task_ids=None, group_names=None, keyword='', status='', page=1, page_size=20,
              run_id=None, since=None, statuses=None):
    with _session():
        query = TaskRun.select()
        if task_ids is not None:
            if not task_ids:
                return [], 0
            query = query.where(TaskRun.task_id.in_(task_ids))
        if group_names is not None:
            if not group_names:
                return [], 0
            query = query.where(TaskRun.group_name.in_(group_names))
        if keyword:
            query = query.where((TaskRun.job_id.contains(keyword)) | (TaskRun.name.contains(keyword))
                                | TaskRun.run_id.contains(keyword))
        if run_id:
            query = query.where(TaskRun.run_id == run_id)
        if since:
            query = query.where(TaskRun.requested_at >= since)
        if status:
            query = query.where(TaskRun.status == status)
        if statuses:
            query = query.where(TaskRun.status.in_(statuses))
        total = query.count()
        rows = list(query.order_by(TaskRun.requested_at.desc(), TaskRun.run_id.desc()).paginate(page, page_size))
        return rows, total


def create_task(task_id, engine, backend_ref, group_name, digest='', enabled=False, claimed=True,
                legacy_cutoff=None, actor=''):
    now = datetime.now(timezone.utc)
    with _session() as database, database.atomic():
        row = TaskDefinition.create(task_id=task_id, engine=engine, backend_ref=backend_ref,
                                    group_name=group_name, digest=digest, enabled=enabled, claimed=claimed,
                                    claimed_at=now if claimed else None, legacy_cutoff=legacy_cutoff,
                                    created_at=now, updated_at=now)
        audit(actor, 'claim' if claimed else 'create', 'task', task_id, group_name)
        return row


def claim_task(task_id, engine, backend_ref, group_name, digest, enabled, actor, include_legacy=False):
    now = datetime.now(timezone.utc)
    with _session() as database, database.atomic():
        row = (TaskDefinition.select().where((TaskDefinition.engine == engine)
                             & (TaskDefinition.backend_ref == backend_ref)
                             & (TaskDefinition.deleted == False))
               .order_by(TaskDefinition.created_at.desc()).first())
        if row is None:
            row = TaskDefinition.create(task_id=task_id, engine=engine, backend_ref=backend_ref,
                                        group_name=group_name, digest=digest, enabled=enabled, claimed=True,
                                        claimed_at=now, legacy_cutoff=now.astimezone().replace(tzinfo=None), updated_at=now)
            if include_legacy and engine == 'platform':
                imported = _attach_legacy_runs(row, now)
                audit(actor, 'claim_legacy_history', 'task', row.task_id, group_name,
                      after_state=f'imported={imported}')
        elif row.deleted:
            raise ValueError('已删除的任务不能重新认领，请使用新的后端任务 ID。')
        elif row.claimed:
            if row.group_name != group_name:
                raise ValueError('任务已由其他团队认领。')
            if include_legacy:
                imported = _attach_legacy_runs(row, row.legacy_cutoff or now)
                audit(actor, 'claim_legacy_history', 'task', row.task_id, group_name,
                      after_state=f'imported={imported}')
        else:
            if include_legacy:
                from ..entity.table_apscheduler import SysApschedulerRunning
                if SysApschedulerRunning.select().where(
                        SysApschedulerRunning.job_id == backend_ref).exists():
                    raise ValueError('旧任务仍有未完成运行，已暂停但不能认领历史。')
            updated = TaskDefinition.update(
                group_name=group_name, digest=digest, enabled=enabled, claimed=True,
                claimed_at=now, legacy_cutoff=now.astimezone().replace(tzinfo=None), updated_at=now,
            ).where((TaskDefinition.task_id == row.task_id) & (TaskDefinition.claimed == False)
                    & (TaskDefinition.deleted == False)).execute()
            if not updated:
                raise ValueError('任务认领状态已变化，请刷新后重试。')
            row = TaskDefinition.get_by_id(row.task_id)
            if include_legacy and engine == 'platform':
                imported = _attach_legacy_runs(row, now)
                audit(actor, 'claim_legacy_history', 'task', row.task_id, group_name,
                      after_state=f'imported={imported}')
        audit(actor, 'claim', 'task', row.task_id, group_name, result='ok')
        return row


def _attach_legacy_runs(task, cutoff):
    from ..entity.table_apscheduler import SysApschedulerResults, SysApschedulerRunning
    cutoff = cutoff.astimezone().replace(tzinfo=None) if cutoff.tzinfo else cutoff

    if task.engine != 'platform':
        return 0
    if SysApschedulerRunning.select().where(SysApschedulerRunning.job_id == task.backend_ref).exists():
        raise ValueError('旧任务仍有未完成运行，不能映射历史。')
    imported = 0
    rows = SysApschedulerResults.select().where(
        (SysApschedulerResults.job_id == task.backend_ref)
        & (SysApschedulerResults.start_datetime <= cutoff)
    ).order_by(SysApschedulerResults.start_datetime)
    for legacy in rows:
        if TaskRun.select().where((TaskRun.job_id == legacy.job_id)
                                  & (TaskRun.legacy_start == legacy.start_datetime)).exists():
            continue
        identity = f'{legacy.job_id}\0{legacy.start_datetime.isoformat()}'
        run_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        status = 'succeeded' if legacy.status.lower() in {'ok', 'success', 'succeeded'} else 'failed'
        duration = max(0, int((legacy.finish_datetime - legacy.start_datetime).total_seconds()))
        TaskRun.create(
            run_id=run_id, task_id=task.task_id, job_id=legacy.job_id, group_name=task.group_name,
            name=task.backend_ref, source='legacy', actor='', status=status, request_key=None,
            digest=task.digest, requested_at=legacy.start_datetime, started_at=legacy.start_datetime,
            finished_at=legacy.finish_datetime, duration=duration, legacy_start=legacy.start_datetime)
        if legacy.log:
            TaskRunLog.create(run_id=run_id, sequence=1, text=legacy.log)
            TaskRun.update(log_sequence=1).where(TaskRun.run_id == run_id).execute()
        imported += 1
    return imported


def update_definition(task_id, expected_digest, digest, enabled, actor):
    with _session() as database, database.atomic():
        task = TaskDefinition.get_or_none((TaskDefinition.task_id == task_id)
                                          & (TaskDefinition.deleted == False))
        if task is None:
            raise ValueError('任务不存在。')
        changed = TaskDefinition.update(
            digest=digest, enabled=enabled, definition_version=TaskDefinition.definition_version + 1,
            updated_at=datetime.now(timezone.utc),
        ).where((TaskDefinition.task_id == task_id) & (TaskDefinition.digest == expected_digest)
                & (TaskDefinition.guard_state == 'clear')).execute()
        if not changed:
            raise ValueError('任务定义或运行状态已变化，请刷新后重试。')
        audit(actor, 'edit', 'task', task_id, task.group_name)
        return TaskDefinition.get_by_id(task_id)


def begin_definition_update(task_id, expected_digest, expected_version=None):
    with _session():
        condition = (
            (TaskDefinition.task_id == task_id) & (TaskDefinition.digest == expected_digest)
            & (TaskDefinition.guard_state == 'clear') & TaskDefinition.active_run_id.is_null()
            & (TaskDefinition.deleted == False))
        if expected_version is not None:
            condition &= TaskDefinition.definition_version == expected_version
        return TaskDefinition.update(guard_state='editing', updated_at=datetime.now(timezone.utc)).where(condition).execute() == 1


def finish_definition_update(task_id, expected_digest, digest, actor):
    with _session() as database, database.atomic():
        task = TaskDefinition.get_or_none(TaskDefinition.task_id == task_id)
        if task is None:
            raise ValueError('任务不存在。')
        changed = TaskDefinition.update(
            digest=digest, enabled=False, guard_state='clear',
            definition_version=TaskDefinition.definition_version + 1, updated_at=datetime.now(timezone.utc),
        ).where((TaskDefinition.task_id == task_id) & (TaskDefinition.digest == expected_digest)
                & (TaskDefinition.guard_state == 'editing')).execute()
        if not changed:
            raise ValueError('任务定义锁已失效，任务仍保持暂停。')
        audit(actor, 'edit', 'task', task_id, task.group_name)
        return TaskDefinition.get_by_id(task_id)


def set_task_state(task_id, enabled=None, deleted=None, actor='', action='operate', expected_guard='clear'):
    with _session() as database, database.atomic():
        task = TaskDefinition.get_or_none((TaskDefinition.task_id == task_id)
                                          & (TaskDefinition.deleted == False))
        if task is None:
            raise ValueError('任务不存在。')
        values = {'updated_at': datetime.now(timezone.utc)}
        if expected_guard == 'editing':
            values['guard_state'] = 'clear'
        if enabled is not None:
            values['enabled'] = enabled
        if deleted is not None:
            values['deleted'] = deleted
            if deleted:
                values['backend_ref'] = f'__deleted__:{task_id}'
        condition = TaskDefinition.task_id == task_id
        if deleted or enabled is True:
            condition &= (TaskDefinition.guard_state == expected_guard) & TaskDefinition.active_run_id.is_null()
        changed = TaskDefinition.update(**values).where(condition).execute()
        if not changed:
            raise ValueError('任务正在运行或状态未知，不能执行此操作。')
        audit(actor, action, 'task', task_id, task.group_name)
        return TaskDefinition.get_by_id(task_id)


def mark_definition_unknown(task_id):
    with _session():
        return TaskDefinition.update(guard_state='unknown', enabled=False, updated_at=datetime.now(timezone.utc)).where(
            (TaskDefinition.task_id == task_id) & (TaskDefinition.guard_state == 'editing')
            & TaskDefinition.active_run_id.is_null()).execute()


def resolve_definition(task_id, digest, actor, reason):
    with _session() as database, database.atomic():
        task = TaskDefinition.get_by_id(task_id)
        changed = TaskDefinition.update(digest=digest, enabled=False, guard_state='clear',
            definition_version=TaskDefinition.definition_version + 1, updated_at=datetime.now(timezone.utc)).where(
                (TaskDefinition.task_id == task_id) & TaskDefinition.guard_state.in_(['editing', 'unknown'])
                & TaskDefinition.active_run_id.is_null() & (TaskDefinition.deleted == False)).execute()
        if not changed:
            raise ValueError('定义不存在未决锁，或仍有在途运行。')
        audit(actor, 'resolve_definition', 'task', task_id, task.group_name, reason=reason)
        return TaskDefinition.get_by_id(task_id)


def begin_execution(job_id, task_id=None, run_id=None, source='schedule', actor=''):
    now = datetime.now(timezone.utc)
    run_id = run_id or __import__('uuid').uuid4().hex
    with _session() as database, database.atomic():
        task = (TaskDefinition.get_or_none((TaskDefinition.task_id == task_id)
                                           & (TaskDefinition.deleted == False)) if task_id
                else TaskDefinition.get_or_none((TaskDefinition.engine == 'platform')
                                                & (TaskDefinition.backend_ref == job_id)
                                                & (TaskDefinition.deleted == False)))
        if task is None or not task.claimed or not task.enabled:
            if task is not None:
                _insert_run(run_id, task, job_id, source, actor, 'skipped', now, reason='unclaimed task')
            return None
        if source in {'manual', 'agent', 'listen'} and run_id and TaskRun.select().where(TaskRun.run_id == run_id).exists():
            run = TaskRun.get_or_none((TaskRun.run_id == run_id) & (TaskRun.task_id == task.task_id))
            if run is None or run.status not in {'requested', 'submitted'}:
                return None
            accepted = TaskDefinition.select().where(
                (TaskDefinition.task_id == task.task_id) & (TaskDefinition.active_run_id == run_id)
                & (TaskDefinition.guard_state == 'active')).exists()
            if not accepted:
                return None
            changed = TaskRun.update(status='running', started_at=now).where(
                (TaskRun.run_id == run_id) & TaskRun.status.in_(['requested', 'submitted'])).execute()
            if not changed:
                return None
            return {'task_id': task.task_id, 'run_id': run_id, 'job_id': job_id, 'status': 'running'}
        if task.guard_state != 'clear' or task.active_run_id:
            _insert_run(run_id, task, job_id, source, actor, 'skipped', now, reason='task already active')
            audit(actor, 'skip_overlap', 'run', run_id, task.group_name, result='skipped')
            return {'task_id': task.task_id, 'run_id': run_id, 'job_id': job_id, 'status': 'skipped'}
        claimed = TaskDefinition.update(active_run_id=run_id, guard_state='active', updated_at=now).where(
            (TaskDefinition.task_id == task.task_id) & (TaskDefinition.claimed == True)
            & (TaskDefinition.deleted == False) & (TaskDefinition.guard_state == 'clear')
            & TaskDefinition.active_run_id.is_null()).execute()
        if not claimed:
            _insert_run(run_id, task, job_id, source, actor, 'skipped', now, reason='task already active')
            return {'task_id': task.task_id, 'run_id': run_id, 'job_id': job_id, 'status': 'skipped'}
        _insert_run(run_id, task, job_id, source, actor, 'running', now, started_at=now)
        return {'task_id': task.task_id, 'run_id': run_id, 'job_id': job_id, 'status': 'running'}


def _insert_run(run_id, task, job_id, source, actor, status, requested_at, started_at=None, reason=''):
    try:
        return TaskRun.create(run_id=run_id, task_id=task.task_id, job_id=job_id,
                              group_name=task.group_name, name=job_id, source=source, actor=actor,
                              status=status, digest=task.digest, requested_at=requested_at,
                              started_at=started_at, reason=reason)
    except IntegrityError:
        return TaskRun.get_or_none(TaskRun.run_id == run_id)


def reserve_run(task_id, run_id, request_key, source, actor, digest, name=''):
    now = datetime.now(timezone.utc)
    database = db()
    transaction = database.atomic('IMMEDIATE') if isinstance(database, SqliteDatabase) else database.atomic()
    with _session(), transaction:
        task = TaskDefinition.get_or_none((TaskDefinition.task_id == task_id)
                                          & (TaskDefinition.deleted == False)
                                          & (TaskDefinition.claimed == True))
        if task is None:
            raise ValueError('任务不存在或尚未认领。')
        duplicate = TaskRun.get_or_none((TaskRun.task_id == task_id) & (TaskRun.actor == actor)
                                        & (TaskRun.request_key == request_key))
        if duplicate:
            return duplicate, False
        if task.guard_state != 'clear' or task.active_run_id:
            raise ValueError(f'任务存在未完成或结果未知的运行：{task.active_run_id or "unknown"}')
        try:
            TaskRun.create(run_id=run_id, task_id=task_id, job_id=task.backend_ref,
                           group_name=task.group_name, name=name or task.backend_ref, source=source,
                           actor=actor, status='requested', request_key=request_key,
                           digest=digest, requested_at=now)
        except IntegrityError:
            duplicate = TaskRun.get_or_none((TaskRun.task_id == task_id) & (TaskRun.actor == actor)
                                            & (TaskRun.request_key == request_key))
            if duplicate:
                return duplicate, False
            raise
        changed = TaskDefinition.update(active_run_id=run_id, guard_state='active', updated_at=now).where(
            (TaskDefinition.task_id == task_id) & (TaskDefinition.guard_state == 'clear')
            & TaskDefinition.active_run_id.is_null() & (TaskDefinition.digest == digest)).execute()
        if not changed:
            TaskRun.delete().where(TaskRun.run_id == run_id).execute()
            raise ValueError('任务定义或运行状态已变化，请刷新后重试。')
        audit(actor, 'request_run', 'run', run_id, task.group_name)
        return TaskRun.get_by_id(run_id), True


def mark_submitted(run_id):
    with _session() as database, database.atomic():
        changed = TaskRun.update(status='submitted').where(
            (TaskRun.run_id == run_id) & (TaskRun.status == 'requested')).execute()
        if changed:
            return True
        run = TaskRun.get_or_none(TaskRun.run_id == run_id)
        return bool(run and run.status in {'running', 'succeeded', 'failed', 'timeout', 'cancelled'})


def append_log(run_id, text):
    with _session() as database, database.atomic():
        run = TaskRun.get_or_none(TaskRun.run_id == run_id)
        if run is None:
            return None
        changed = TaskRun.update(log_sequence=TaskRun.log_sequence + 1).where(
            TaskRun.run_id == run_id).execute()
        if not changed:
            return None
        sequence = TaskRun.get_by_id(run_id).log_sequence
        TaskRunLog.create(run_id=run_id, sequence=sequence, text=str(text))
        return sequence


def finish_run(run_id, status, reason=''):
    if status not in {'succeeded', 'failed', 'timeout', 'cancelled', 'unknown', 'skipped', 'accepted'}:
        raise ValueError('无效的运行状态。')
    now = datetime.now(timezone.utc)
    with _session() as database, database.atomic():
        run = TaskRun.get_or_none(TaskRun.run_id == run_id)
        if run is None:
            return False
        task = TaskDefinition.get_or_none(TaskDefinition.task_id == run.task_id)
        finished = status != 'unknown'
        changed = TaskRun.update(status=status, reason=str(reason)[:2000],
                                 finished_at=now if finished else None,
                                 duration=max(0, int((now - run.started_at).total_seconds()))
                                 if finished and run.started_at else None).where(
            (TaskRun.run_id == run_id) & TaskRun.status.in_(['requested', 'submitted', 'running'])).execute()
        if not changed:
            return False
        if task:
            if status == 'unknown':
                TaskDefinition.update(guard_state='unknown', active_run_id=run_id,
                                      updated_at=now).where(TaskDefinition.task_id == task.task_id).execute()
            else:
                TaskDefinition.update(guard_state='clear', active_run_id=None,
                                      updated_at=now).where((TaskDefinition.task_id == task.task_id)
                                                           & (TaskDefinition.active_run_id == run_id)).execute()
        audit(run.actor, 'finish_run', 'run', run_id, run.group_name, result=status, reason=reason)
        return True


def recover_runs():
    with _session() as database, database.atomic():
        run_ids = [row.run_id for row in TaskRun.select(TaskRun.run_id).where(
            TaskRun.status.in_(['requested', 'submitted', 'running']))]
        for run_id in run_ids:
            finish_run(run_id, 'unknown', 'process recovery could not confirm remote outcome')
        return len(run_ids)


def resolve_run(run_id, outcome, reason, actor):
    if outcome not in {'not_executed', 'ended'} or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('核实结果或原因无效。')
    reason = reason.strip()
    with _session() as database, database.atomic():
        run = TaskRun.get_or_none((TaskRun.run_id == run_id) & (TaskRun.status == 'unknown'))
        if run is None:
            raise ValueError('只有未知运行可以人工核实。')
        now = datetime.now(timezone.utc)
        final_status = 'cancelled' if outcome == 'not_executed' else 'ended'
        changed = TaskRun.update(status=final_status, reason=reason, finished_at=now).where(
            (TaskRun.run_id == run_id) & (TaskRun.status == 'unknown')).execute()
        if not changed:
            raise ValueError('运行已被其他请求核实，请刷新。')
        TaskDefinition.update(active_run_id=None, guard_state='clear', updated_at=now).where(
            (TaskDefinition.task_id == run.task_id) & (TaskDefinition.active_run_id == run_id)
            & (TaskDefinition.guard_state == 'unknown')).execute()
        audit(actor, 'resolve_unknown', 'run', run_id, run.group_name, result=outcome, reason=reason)
        return TaskRun.get_by_id(run_id)


def get_run(run_id):
    with _session():
        return TaskRun.get_or_none(TaskRun.run_id == run_id)


def get_requested_run(task_id, actor, request_key):
    with _session():
        return TaskRun.get_or_none((TaskRun.task_id == task_id) & (TaskRun.actor == actor)
                                   & (TaskRun.request_key == request_key))


def transfer_task(task_id, destination, actor, reason):
    with _session() as database, database.atomic():
        task = TaskDefinition.get_by_id(task_id)
        changed = TaskDefinition.update(group_name=destination, updated_at=datetime.now(timezone.utc)).where(
            (TaskDefinition.task_id == task_id) & (TaskDefinition.guard_state == 'clear')
            & TaskDefinition.active_run_id.is_null() & (TaskDefinition.enabled == False)
            & (TaskDefinition.deleted == False)).execute()
        if not changed:
            raise ValueError('仅可转移已暂停且无未决运行的任务。')
        audit(actor, 'transfer_task', 'task', task_id, destination,
              before_state=task.group_name, after_state=destination, reason=reason)
        return TaskDefinition.get_by_id(task_id)


def get_log_chunks(run_id, cursor=0, limit=100):
    with _session():
        run = TaskRun.get_or_none(TaskRun.run_id == run_id)
        if run is None:
            return [], cursor, None
        rows = list(TaskRunLog.select().where((TaskRunLog.run_id == run_id)
                                              & (TaskRunLog.sequence > cursor))
                    .order_by(TaskRunLog.sequence).limit(limit))
        chunks = [row.text for row in rows]
        next_cursor = rows[-1].sequence if rows else cursor
        return chunks, next_cursor, run


def prune_logs(days, batch_size=100):
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError('Retention days must be a positive integer.')
    if not 1 <= batch_size <= 1000:
        raise ValueError('Invalid retention batch size.')
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with _session() as database, database.atomic():
        eligible = [row.run_id for row in TaskRun.select(TaskRun.run_id).where(
            TaskRun.status.in_(['succeeded', 'failed', 'timeout', 'cancelled', 'ended', 'skipped', 'accepted'])
            & (TaskRun.finished_at < cutoff) & (TaskRun.log_sequence > 0)).limit(batch_size)]
        removed = TaskRunLog.delete().where(TaskRunLog.run_id.in_(eligible)).execute() if eligible else 0
        if eligible:
            TaskRun.update(log_sequence=0).where(TaskRun.run_id.in_(eligible)).execute()
            audit('scheduler', 'prune_logs', 'retention', str(days), reason=f'runs={len(eligible)} chunks={removed}')
        return removed


def upsert_grant(group_name, user_name, operations, actor):
    now = datetime.now(timezone.utc)
    with _session() as database, database.atomic():
        grant, created = TaskTeamGrant.get_or_create(
            group_name=group_name, user_name=user_name,
            defaults={'operations': json.dumps(sorted(set(operations))), 'updated_by': actor, 'updated_at': now})
        if not created:
            grant.operations = json.dumps(sorted(set(operations)))
            grant.updated_by = actor
            grant.updated_at = now
            grant.save()
        audit(actor, 'grant_team', 'grant', f'{group_name}:{user_name}', group_name)


def get_grant(group_name, user_name):
    with _session():
        grant = TaskTeamGrant.get_or_none((TaskTeamGrant.group_name == group_name)
                                          & (TaskTeamGrant.user_name == user_name))
        if grant is None:
            return set()
        try:
            values = json.loads(grant.operations)
        except (TypeError, ValueError):
            return set()
        return set(values) if isinstance(values, list) and all(isinstance(item, str) for item in values) else set()


def audit(actor, action, object_type='', object_id='', group_name='', before_state='', after_state='',
          result='ok', reason=''):
    from ..entity.table_task_center import TaskOperationAudit

    database = db()
    if database.is_closed() and not database.in_transaction():
        ensure_schema()
    return TaskOperationAudit.create(
        actor=str(actor or '')[:191], action=str(action)[:64], object_type=str(object_type)[:32],
        object_id=str(object_id)[:512], group_name=str(group_name)[:128],
        before_state=str(before_state)[:1000], after_state=str(after_state)[:1000],
        result=str(result)[:32], reason=str(reason)[:1000], created_at=datetime.now(timezone.utc))