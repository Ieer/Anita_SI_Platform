from datetime import datetime

from peewee import CharField, DateTimeField, ForeignKeyField, Model, TextField

from ..conn import db


class AgentConversation(Model):
    conversation_id = CharField(primary_key=True, max_length=32)
    owner = CharField(max_length=191, index=True)
    title = CharField(max_length=80, default='新会话')
    pending_turn = CharField(max_length=32, null=True)
    model_name = CharField(max_length=191, null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        database = db()
        table_name = 'sys_agent_conversation'


class AgentMessage(Model):
    conversation = ForeignKeyField(AgentConversation, backref='messages', on_delete='CASCADE')
    role = CharField(max_length=16)
    content = TextField()
    trace = TextField(default='[]')
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        database = db()
        table_name = 'sys_agent_message'


class AgentAction(Model):
    action_id = CharField(primary_key=True, max_length=32)
    conversation = ForeignKeyField(AgentConversation, backref='actions', on_delete='CASCADE')
    job_id = CharField(max_length=191)
    task_digest = CharField(max_length=64)
    summary = TextField()
    status = CharField(max_length=16, default='pending')
    run_id = CharField(max_length=191)
    result = TextField(default='')
    expires_at = DateTimeField()
    created_at = DateTimeField(default=datetime.now)
    decided_at = DateTimeField(null=True)

    class Meta:
        database = db()
        table_name = 'sys_agent_action'