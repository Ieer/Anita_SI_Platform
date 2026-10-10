import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def inventory(database, jobs):
    from database.sql_db.entity.table_task_center import MODELS, TaskDefinition, TaskRun, TaskSchemaVersion
    from database.sql_db.entity.table_apscheduler import SysApschedulerRunning

    with database.connection_context():
        tables = set(database.get_tables())
        mappings = ({row.backend_ref: row for row in TaskDefinition.select().where(
            (TaskDefinition.engine == 'platform') & (TaskDefinition.deleted == False))}
            if TaskDefinition._meta.table_name in tables else {})
        active = SysApschedulerRunning.select().count() if SysApschedulerRunning._meta.table_name in tables else 0
        if TaskRun._meta.table_name in tables:
            active += TaskRun.select().where(TaskRun.status.in_(['requested', 'submitted', 'running', 'unknown'])).count()
        version = TaskSchemaVersion.get_or_none(TaskSchemaVersion.component == 'task_center') if TaskSchemaVersion._meta.table_name in tables else None
        if version and version.version not in {1, 2}:
            raise ValueError('Unsupported task-center schema version; stop and review migration.')
        candidates = [{'job_id': job['id'], 'trigger': job.get('trigger'),
                       'was_enabled': job.get('status') is True}
                      for job in jobs if not str(job.get('id', '')).startswith('sys_')
                      and not (mappings.get(job['id']) and mappings[job['id']].claimed)]
        return {'schema_version': version.version if version else None, 'target_schema_version': 2,
                'missing_tables': [model._meta.table_name for model in MODELS if model._meta.table_name not in tables],
                'unresolved_runs': active, 'platform_tasks_to_pause': candidates,
                'windows_changes': 0, 'automatic_restore': False}


def apply_migration(database, jobs, backup_dir, actor, confirmed=False, legacy_timezone=None):
    from peewee import SqliteDatabase
    from common import task_center
    from common.utilities import util_apscheduler
    from database.sql_db.dao import dao_task_center

    if not confirmed:
        raise ValueError('Maintenance confirmation is required.')
    if not task_center._is_admin(actor):
        raise ValueError('An enabled global administrator is required.')
    report = inventory(database, jobs)
    if report['unresolved_runs']:
        raise ValueError('Drain or manually verify all unresolved runs before applying.')
    if not isinstance(database, SqliteDatabase):
        raise ValueError('MySQL apply is gated pending an independently verified backup and contract test.')
    if report['schema_version'] == 1 and not legacy_timezone:
        raise ValueError('Version-1 timestamp migration requires an explicitly confirmed legacy timezone.')
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    identity = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
    backup = directory / f'task-center-{identity}.sqlite3'
    with database.connection_context(), closing(sqlite3.connect(str(backup))) as destination:
        database.connection().backup(destination)
    manifest = directory / f'task-center-{identity}.json'
    report.update(backup=str(backup), actor=actor, paused=[], status='backup_complete', legacy_timezone=legacy_timezone)
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if report['schema_version'] == 1:
        legacy_zone = ZoneInfo(legacy_timezone)
        from database.sql_db.entity.table_task_center import MODELS, TaskSchemaVersion, UTCDateTimeField
        with database.connection_context(), database.atomic():
            for model in MODELS:
                fields = [field for field in model._meta.sorted_fields if isinstance(field, UTCDateTimeField)]
                if not fields or not database.table_exists(model._meta.table_name):
                    continue
                primary_key = model._meta.primary_key
                query, parameters = model.select(primary_key, *fields).sql()
                cursor = database.execute_sql(query, parameters)
                records = cursor.fetchall()
                for record in records:
                    converted = {}
                    for field, value in zip(fields, record[1:]):
                        if value is not None:
                            parsed = datetime.fromisoformat(value)
                            converted[field.name] = (parsed.replace(tzinfo=legacy_zone) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
                    if converted:
                        model.update(**converted).where(primary_key == record[0]).execute()
            TaskSchemaVersion.update(version=2).where(TaskSchemaVersion.component == 'task_center').execute()
    dao_task_center.ensure_schema()
    try:
        for candidate in report['platform_tasks_to_pause']:
            reference = candidate['job_id']
            util_apscheduler.start_stop_job(reference, False, candidate['trigger'])
            report['paused'].append(reference)
            if dao_task_center.find_task('platform', reference) is None:
                job = next(row for row in jobs if row['id'] == reference)
                dao_task_center.create_task(uuid4().hex, 'platform', reference, '',
                    digest=task_center._digest(task_center._payload(job)), enabled=False,
                    claimed=False, actor=actor)
            report['status'] = 'pausing'
            manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        report['status'] = 'paused_pending_claim'
        dao_task_center.audit(actor, 'migration', 'schema', 'task_center', result='paused_pending_claim')
    except Exception:
        report['status'] = 'interrupted_no_automatic_restore'
        raise
    finally:
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description='Read-only inventory by default; never starts a scheduler.')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-maintenance', action='store_true')
    parser.add_argument('--actor')
    parser.add_argument('--backup-dir')
    parser.add_argument('--legacy-timezone')
    args = parser.parse_args()
    from database.sql_db.conn import db
    from common.task_center import _platform_job_rows
    from peewee import SqliteDatabase

    database = db()
    if isinstance(database, SqliteDatabase) and not Path(database.database).is_file():
        parser.error('The existing SQLite database is required; dry-run will not create one.')
    try:
        jobs = _platform_job_rows()
        if args.apply:
            if not args.backup_dir or not args.actor:
                parser.error('--apply requires --actor and --backup-dir.')
            report = apply_migration(database, jobs, args.backup_dir, args.actor, args.confirm_maintenance, args.legacy_timezone)
        else:
            report = inventory(database, jobs)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as error:
        parser.exit(1, f'{type(error).__name__}: migration stopped; no automatic restore.\n')


if __name__ == '__main__':
    main()