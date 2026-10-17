import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from common.agent import AgentError, MAX_PROVIDER_STATE_BYTES, redact, redact_structure
from ..conn import db
from ..entity.table_agent import (
    AgentAction, AgentConversation, AgentConversationProfile, AgentMessage, AgentProviderState, AgentRemoteSession,
)


@contextmanager
def _session():
    database = db()
    with database.connection_context():
        database.create_tables([
            AgentConversation, AgentConversationProfile, AgentRemoteSession, AgentMessage, AgentAction,
            AgentProviderState,
        ], safe=True)
        yield database


def _profile_default():
    from . import agent_provider_repository

    return agent_provider_repository.default_profile_id()


def _owned(owner, conversation_id):
    conversation = AgentConversation.get_or_none(
        (AgentConversation.conversation_id == conversation_id) & (AgentConversation.owner == owner))
    if conversation is None:
        raise AgentError('会话不存在或无权访问。')
    return conversation


def create_conversation(owner):
    default_profile = _profile_default()
    with _session():
        conversation = AgentConversation.create(conversation_id=uuid4().hex, owner=owner)
        AgentConversationProfile.create(
            conversation_id=conversation.conversation_id,
            profile_id=default_profile,
        )
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


def get_remote_session(owner, conversation_id, provider_id):
    with _session():
        conversation = _owned(owner, conversation_id)
        remote = AgentRemoteSession.get_or_none(
            (AgentRemoteSession.conversation == conversation)
            & (AgentRemoteSession.provider_id == provider_id))
        return remote.remote_conversation_id if remote else None


def set_remote_session(owner, conversation_id, provider_id, remote_conversation_id):
    if (not isinstance(provider_id, str) or not provider_id or len(provider_id) > 64
            or (remote_conversation_id is not None
                and (not isinstance(remote_conversation_id, str) or len(remote_conversation_id) > 512))):
        raise AgentError('远端会话标识无效。')
    if remote_conversation_id is None:
        return
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        remote, created = AgentRemoteSession.get_or_create(
            conversation=conversation,
            provider_id=provider_id,
            defaults={'remote_conversation_id': remote_conversation_id},
        )
        if not created and remote.remote_conversation_id != remote_conversation_id:
            AgentRemoteSession.update(
                remote_conversation_id=remote_conversation_id,
                updated_at=datetime.now(),
            ).where(AgentRemoteSession.id == remote.id).execute()


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


def get_provider_state(owner, conversation_id, provider_id):
    from .agent_provider_repository import _decrypt

    with _session():
        conversation = _owned(owner, conversation_id)
        state = AgentProviderState.get_or_none(
            (AgentProviderState.conversation == conversation) & (AgentProviderState.provider_id == provider_id))
        return json.loads(_decrypt(state.encrypted_state)) if state else None


def prepare_provider_state(owner, conversation_id, turn_id, settings):
    if settings.provider != 'enterprise_models':
        return get_provider_state(owner, conversation_id, settings.remote_session_key)
    from .agent_provider_repository import _decrypt, _encrypt

    version = settings.provider_options['_credential_version']
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        if conversation.pending_turn != turn_id:
            raise AgentError('请求已失效，请刷新会话。')
        row = AgentProviderState.get_or_none(
            (AgentProviderState.conversation == conversation)
            & (AgentProviderState.provider_id == settings.remote_session_key))
        state = json.loads(_decrypt(row.encrypted_state)) if row else None
        if state and state.get('credential_version') == version and state.get('interrupted'):
            raise AgentError('Enterprise Models 上轮状态不确定，请新建会话后重试。')
        marker = _encrypt(json.dumps({'credential_version': version, 'interrupted': True}))
        if row:
            AgentProviderState.update(encrypted_state=marker).where(AgentProviderState.id == row.id).execute()
        else:
            AgentProviderState.create(conversation=conversation, provider_id=settings.remote_session_key,
                                      encrypted_state=marker)
        return state


def finish_turn(owner, conversation_id, turn_id, content, trace=(), model_name=None, failed=False, proposals=(),
                provider_id=None, provider_state=None):
    with _session() as database, database.atomic():
        conversation = _owned(owner, conversation_id)
        if not failed and isinstance(provider_state, dict) and provider_state.get('provider') == 'enterprise_models':
            from common.enterprise_models_credentials import read_credential
            from .agent_provider_repository import credential_rejected

            version = provider_state.get('credential_version')
            if read_credential().version != version or credential_rejected(version):
                raise AgentError('Enterprise Models 登录凭证已变更，请新建会话后重试。')
        updated = (AgentConversation.update(pending_turn=None, updated_at=datetime.now(), model_name=model_name)
                   .where((AgentConversation.conversation_id == conversation_id)
                          & (AgentConversation.owner == owner) & (AgentConversation.pending_turn == turn_id))
                   .execute())
        if not updated:
            raise AgentError('请求已失效，请刷新会话。')
        AgentMessage.create(conversation=conversation, role='error' if failed else 'assistant',
                            content=redact(content)[:8000],
                            trace=json.dumps(redact_structure(list(trace)), ensure_ascii=False))
        if not failed and provider_state is not None:
            from .agent_provider_repository import _encrypt

            if not provider_id or len(provider_id) > 64 or not isinstance(provider_state, dict):
                raise AgentError('Provider 对话状态无效。')
            state_json = json.dumps(provider_state, ensure_ascii=False)
            if len(state_json.encode('utf-8')) > MAX_PROVIDER_STATE_BYTES:
                raise AgentError('Provider 对话状态过大。')
            AgentProviderState.delete().where(
                (AgentProviderState.conversation == conversation)
                & (AgentProviderState.provider_id == provider_id)).execute()
            AgentProviderState.create(conversation=conversation, provider_id=provider_id,
                                      encrypted_state=_encrypt(state_json))
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