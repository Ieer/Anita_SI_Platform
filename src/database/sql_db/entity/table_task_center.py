from datetime import datetime, timezone

from peewee import BooleanField, CharField, DateTimeField, IntegerField, Model, TextField, SqliteDatabase

from ..conn import db


class BaseModel(Model):
    class Meta:
        database = db()


class UTCDateTimeField(DateTimeField):
    def db_value(self, value):
        if value is None:
            return None
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        converted = parsed.astimezone(timezone.utc)
        return converted.isoformat() if isinstance(self.model._meta.database, SqliteDatabase) else converted.replace(tzinfo=None)

    def python_value(self, value):
        if value is None:
            return None
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None and not isinstance(self.model._meta.database, SqliteDatabase):
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


class TaskDefinition(BaseModel):
    task_id = CharField(primary_key=True, max_length=32)
    engine = CharField(max_length=16)
    backend_ref = CharField(max_length=512)
    group_name = CharField(max_length=128)
    claimed = BooleanField(default=False)
    enabled = BooleanField(default=False)
    definition_version = IntegerField(default=1)
    digest = CharField(max_length=64, default='')
    deleted = BooleanField(default=False)
    active_run_id = CharField(max_length=32, null=True)
    guard_state = CharField(max_length=16, default='clear')
    claimed_at = UTCDateTimeField(null=True)
    legacy_cutoff = DateTimeField(null=True)
    created_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))
    updated_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = 'sys_task_definition'
        indexes = ((('engine', 'backend_ref'), True), (('group_name', 'deleted'), False))


class TaskRun(BaseModel):
    run_id = CharField(primary_key=True, max_length=32)
    task_id = CharField(max_length=32, index=True)
    job_id = CharField(max_length=512)
    group_name = CharField(max_length=128)
    name = CharField(max_length=191, default='')
    source = CharField(max_length=16)
    actor = CharField(max_length=191, default='')
    status = CharField(max_length=16)
    request_key = CharField(max_length=191, null=True)
    digest = CharField(max_length=64, default='')
    requested_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))
    started_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)
    duration = IntegerField(null=True)
    reason = TextField(default='')
    legacy_start = DateTimeField(null=True)
    log_sequence = IntegerField(default=0)

    class Meta:
        table_name = 'sys_task_run'
        indexes = ((('task_id', 'actor', 'request_key'), True), (('job_id', 'legacy_start'), True),
               (('group_name', 'requested_at'), False))


class TaskRunLog(BaseModel):
    run_id = CharField(max_length=32)
    sequence = IntegerField()
    text = TextField()
    created_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = 'sys_task_run_log'
        indexes = ((('run_id', 'sequence'), True),)


class TaskTeamGrant(BaseModel):
    group_name = CharField(max_length=128)
    user_name = CharField(max_length=32)
    operations = TextField(default='[]')
    updated_by = CharField(max_length=32)
    updated_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = 'sys_task_team_grant'
        indexes = ((('group_name', 'user_name'), True),)


class TaskSchemaVersion(BaseModel):
    component = CharField(primary_key=True, max_length=64)
    version = IntegerField()
    updated_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = 'sys_task_schema_version'


class TaskOperationAudit(BaseModel):
    actor = CharField(max_length=191, default='')
    action = CharField(max_length=64)
    object_type = CharField(max_length=32, default='')
    object_id = CharField(max_length=512, default='')
    group_name = CharField(max_length=128, default='')
    before_state = TextField(default='')
    after_state = TextField(default='')
    result = CharField(max_length=32, default='ok')
    reason = TextField(default='')
    created_at = UTCDateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = 'sys_task_operation_audit'
        indexes = ((('group_name', 'created_at'), False),)


MODELS = (TaskDefinition, TaskRun, TaskRunLog, TaskTeamGrant, TaskSchemaVersion, TaskOperationAudit)