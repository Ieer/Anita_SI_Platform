from database.sql_db.conn import db
from peewee import DoesNotExist, IntegrityError
from common.utilities.util_logger import Log
from ..entity.table_listen_task import ApschedulerJobsActiveListen, ApschedulerListenCheckpoint
from datetime import datetime, timedelta
from typing import Optional, Iterator, List, Union
from common.utilities.util_menu_access import get_menu_access

logger = Log.get_logger(__name__)


def ensure_listen_checkpoint_table():
    database = db()
    with database.connection_context():
        ApschedulerListenCheckpoint.create_table(safe=True)


def get_listen_checkpoint(channel_name: str) -> Optional[datetime]:
    database = db()
    with database.connection_context():
        checkpoint = ApschedulerListenCheckpoint.get_or_none(
            ApschedulerListenCheckpoint.channel_name == channel_name
        )
        return checkpoint.scanned_until if checkpoint else None


def set_listen_checkpoint(channel_name: str, scanned_until: datetime) -> None:
    database = db()
    with database.connection_context(), database.atomic():
        checkpoint = ApschedulerListenCheckpoint.get_or_none(
            ApschedulerListenCheckpoint.channel_name == channel_name
        )
        if checkpoint:
            checkpoint.scanned_until = scanned_until
            checkpoint.updated_at = datetime.now()
            checkpoint.save()
        else:
            ApschedulerListenCheckpoint.create(
                channel_name=channel_name,
                scanned_until=scanned_until,
                updated_at=datetime.now(),
            )


def get_activa_listen_job(job_id: Optional[str] = None) -> List[ApschedulerJobsActiveListen]:
    if job_id is None:
        activa_listen_jobs = [i for i in ApschedulerJobsActiveListen.select()]
        return activa_listen_jobs
    else:
        activa_listen_jobs = [i for i in ApschedulerJobsActiveListen.select().where(ApschedulerJobsActiveListen.job_id == job_id)]
        return activa_listen_jobs


def remove_activa_listen_job(job_id: str) -> bool:
    database = db()
    try:
        with database.atomic():
            ApschedulerJobsActiveListen.delete().where(ApschedulerJobsActiveListen.job_id == job_id).execute()
    except IntegrityError as e:
        raise e


def enable_job(job_id: str, enable: bool) -> bool:
    database = db()
    try:
        with database.atomic():
            ApschedulerJobsActiveListen.update(
                status=enable,
            ).where(ApschedulerJobsActiveListen.job_id == job_id).execute()
    except IntegrityError as e:
        raise e


def insert_activa_listen_job(
    type,
    job_id,
    status,
    script_text,
    script_type,
    update_by,
    update_datetime,
    create_by,
    create_datetime,
    notify_channels,
    extract_names,
    timeout,
    listen_channels,
    listen_keyword,
    host=None,
    port=None,
    username=None,
    password=None,
) -> bool:
    database = db()
    try:
        with database.atomic():
            ApschedulerJobsActiveListen.create(
                type=type,
                job_id=job_id,
                status=status,
                script_text=script_text,
                script_type=script_type,
                update_by=update_by,
                update_datetime=update_datetime,
                create_by=create_by,
                create_datetime=create_datetime,
                notify_channels=notify_channels,
                extract_names=extract_names,
                timeout=timeout,
                host=host if host else '',
                port=port if port else '',
                username=username if username else '',
                password=password if password else '',
                listen_channels=listen_channels,
                listen_keyword=listen_keyword,
            )
        return True
    except IntegrityError as e:
        raise e


def update_activa_listen_job(job_id: str, **values) -> bool:
    allowed_fields = {
        'type', 'status', 'script_text', 'script_type', 'update_by', 'update_datetime',
        'create_by', 'create_datetime', 'notify_channels', 'extract_names', 'timeout',
        'listen_channels', 'listen_keyword', 'host', 'port', 'username', 'password',
    }
    if not values or set(values) - allowed_fields:
        raise ValueError('Invalid listen task update fields')
    database = db()
    with database.connection_context(), database.atomic():
        updated = (
            ApschedulerJobsActiveListen.update(**values)
            .where(ApschedulerJobsActiveListen.job_id == job_id)
            .execute()
        )
        if updated != 1:
            raise DoesNotExist(f'Listen task not found: {job_id}')
    return True
