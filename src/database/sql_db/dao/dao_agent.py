import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from common.agent import AgentError, redact, redact_structure
from ..conn import db
from ..entity.table_agent import AgentAction, AgentConversation, AgentMessage


@contextmanager
def _session():
    database = db()
    with database.connection_context():
        database.create_tables([AgentConversation, AgentMessage, AgentAction], safe=True)
        yield database


def _owned(owner, conversation_id):
    conversation = AgentConversation.get_or_none(
        (AgentConversation.conversation_id == conversation_id) & (AgentConversation.owner == owner))
    if conversation is None:
        raise AgentError('会话不存在或无权访问。')
    return conversation


def create_conversation(owner):
    with _session():
        conversation = AgentConversation.create(conversation_id=uuid4().hex, owner=owner)
        return conversation.conversation_id


def list_conversations(owner):
    with _session():
        return list(AgentConversation.select(
            AgentConversation.conversation_id, AgentConversation.title, AgentConversation.model_name,
        ).where(AgentConversation.owner == owner).order_by(AgentConversation.updated_at.desc()).limit(20).dicts())


def read_messages(owner, conversation_id):
    with _session():
        conversation = _owned(owner, conversation_id)
        rows = list(AgentMessage.select().where(AgentMessage.conversation == conversation)
                    .order_by(AgentMessage.id.desc()).limit(40).dicts())
        return [
            {'role': row['role'], 'content': row['content'], 'trace': json.loads(row['trace']),
             'created_at': row['created_at'].isoformat()}
            for row in reversed(rows)
        ]


def begin_turn(owner, conversation_id, prompt):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise AgentError('请输入 1 到 4000 字的消息。')
    prompt = redact(prompt.strip())
    now = datetime.now()
    turn_id = uuid4().hex
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        claimed = (AgentConversation.update(pending_turn=turn_id, updated_at=now)
                   .where((AgentConversation.conversation_id == conversation_id)
                          & (AgentConversation.owner == owner)
                          & (AgentConversation.pending_turn.is_null()
                             | (AgentConversation.updated_at < now - timedelta(minutes=5))))
                   .execute())
        if not claimed:
            raise AgentError('当前会话正在处理，请等待完成。')
        rows = list(AgentMessage.select(AgentMessage.role, AgentMessage.content)
                    .where((AgentMessage.conversation == conversation)
                           & (AgentMessage.role.in_(['user', 'assistant'])))
                    .order_by(AgentMessage.id.desc()).limit(12).dicts())
        history = list(reversed(rows))
        AgentMessage.create(conversation=conversation, role='user', content=prompt)
        AgentConversation.update(title=prompt[:60]).where(
            (AgentConversation.conversation_id == conversation_id) & (AgentConversation.title == '新会话')).execute()
    return turn_id, history


def finish_turn(owner, conversation_id, turn_id, content, trace=(), model_name=None, failed=False, proposals=()):
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        updated = (AgentConversation.update(pending_turn=None, updated_at=datetime.now(), model_name=model_name)
                   .where((AgentConversation.conversation_id == conversation_id)
                          & (AgentConversation.owner == owner) & (AgentConversation.pending_turn == turn_id))
                   .execute())
        if not updated:
            raise AgentError('请求已失效，请刷新会话。')
        AgentMessage.create(conversation=conversation, role='error' if failed else 'assistant',
                            content=redact(content)[:8000],
                            trace=json.dumps(redact_structure(list(trace)), ensure_ascii=False))
        if not failed:
            for proposal in proposals:
                action_id = uuid4().hex
                AgentAction.create(action_id=action_id, conversation=conversation, job_id=proposal['job_id'],
                                   task_digest=proposal['task_digest'], summary=redact(proposal['summary'])[:1000],
                                   run_id=f'agent_run_{action_id}', expires_at=datetime.now() + timedelta(minutes=10))


def create_action(owner, conversation_id, job_id, task_digest, summary):
    action_id = uuid4().hex
    with _session():
        conversation = _owned(owner, conversation_id)
        AgentAction.create(action_id=action_id, conversation=conversation, job_id=job_id,
                           task_digest=task_digest, summary=redact(summary)[:1000],
                           run_id=f'agent_run_{action_id}', expires_at=datetime.now() + timedelta(minutes=10))
    return action_id


def read_actions(owner, conversation_id):
    with _session():
        conversation = _owned(owner, conversation_id)
        AgentAction.update(status='expired').where(
            (AgentAction.conversation == conversation) & (AgentAction.status == 'pending')
            & (AgentAction.expires_at <= datetime.now())).execute()
        return list(AgentAction.select().where(AgentAction.conversation == conversation)
                    .order_by(AgentAction.created_at.desc()).limit(20).dicts())


def decide_action(owner, conversation_id, action_id, approve):
    if not isinstance(approve, bool):
        raise AgentError('审批决定无效。')
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        claimed = AgentAction.update(status='submitting' if approve else 'rejected', decided_at=datetime.now()).where(
            (AgentAction.action_id == action_id) & (AgentAction.conversation == conversation)
            & (AgentAction.status == 'pending') & (AgentAction.expires_at > datetime.now())).execute()
        if not claimed:
            raise AgentError('审批已处理、已过期或不存在，请刷新会话。')
        return AgentAction.get_by_id(action_id).__data__.copy()


def finish_action(owner, conversation_id, action_id, status, result):
    if status not in {'submitted', 'unknown', 'blocked'}:
        raise AgentError('执行状态无效。')
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        updated = AgentAction.update(status=status, result=redact(result)[:1000]).where(
            (AgentAction.action_id == action_id) & (AgentAction.conversation == conversation)
            & (AgentAction.status == 'submitting')).execute()
        if not updated:
            raise AgentError('审批执行状态已失效。')