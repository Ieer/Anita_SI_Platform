import base64
import json
from datetime import datetime
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from common.agent import AgentError, ModelSettings
from config.anita_go_conf import CommonConf
from i18n import t__dashboard
from ..conn import db
from ..entity.table_agent import (
    AgentConversation, AgentConversationProfile, AgentProviderOptions, AgentProviderProfile,
    AgentProviderState, AgentRemoteSession,
)


ENVIRONMENT_PROFILE_ID = 'environment'
MAX_PROFILES = 12


def _session():
    database = db()
    database.create_tables([
        AgentConversation, AgentConversationProfile, AgentProviderOptions, AgentProviderProfile,
        AgentProviderState, AgentRemoteSession,
    ], safe=True)
    return database


def _cipher():
    try:
        key = bytes.fromhex(CommonConf.ENCRYPT_KEY)
    except (TypeError, ValueError) as error:
        raise AgentError('服务端加密密钥无效，无法安全保存 Agent 凭证。') from error
    if len(key) != 32:
        raise AgentError('服务端加密密钥必须是 32 字节，无法安全保存 Agent 凭证。')
    return AESGCM(key)


def _encrypt(secret):
    import os

    nonce = os.urandom(12)
    ciphertext = _cipher().encrypt(nonce, secret.encode('utf-8'), b'si-agent-provider-profile:v1')
    return 'v1:' + base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')


def _decrypt(value):
    try:
        payload = base64.urlsafe_b64decode(value.removeprefix('v1:').encode('ascii'))
        return _cipher().decrypt(payload[:12], payload[12:], b'si-agent-provider-profile:v1').decode('utf-8')
    except AgentError:
        raise
    except Exception as error:
        raise AgentError('已保存的 Agent 凭证无法解密，请重新录入凭证。') from error


def _profile_dict(row, include_secret=False):
    data = {
        'profile_id': row.profile_id,
        'name': row.name,
        'provider_id': row.provider_id,
        'base_url': row.base_url,
        'model': row.model,
        'has_api_key': bool(row.encrypted_api_key),
        'is_default': row.is_default,
        'source': 'database',
    }
    if include_secret:
        data['api_key'] = _decrypt(row.encrypted_api_key)
    return data


def _options(profile_id, include_secret=False):
    row = AgentProviderOptions.get_or_none(AgentProviderOptions.profile_id == profile_id)
    options = json.loads(_decrypt(row.encrypted_options)) if row else {}
    if not include_secret:
        options.pop('client_secret', None)
    return options


def list_profiles():
    database = _session()
    with database.connection_context():
        return [_profile_dict(row) for row in AgentProviderProfile.select().order_by(
            AgentProviderProfile.is_default.desc(), AgentProviderProfile.name)]


def get_profile(profile_id, include_secret=False):
    if profile_id == ENVIRONMENT_PROFILE_ID:
        from common.agent import ModelSettings

        settings = ModelSettings.from_environment()
        return {
            'profile_id': ENVIRONMENT_PROFILE_ID,
            'name': '服务端环境配置',
            'provider_id': settings.provider,
            'base_url': settings.base_url,
            'model': settings.model,
            'has_api_key': True,
            'is_default': False,
            'source': 'environment',
            **({'api_key': settings.api_key} if include_secret else {}),
            'provider_options': {
                key: value for key, value in settings.provider_options.items()
                if include_secret or key != 'client_secret'
            },
        }
    database = _session()
    with database.connection_context():
        row = AgentProviderProfile.get_or_none(AgentProviderProfile.profile_id == profile_id)
        if row is None:
            raise AgentError('所选 Agent 配置不存在或已删除。')
        return {**_profile_dict(row, include_secret),
                'provider_options': _options(profile_id, include_secret)}


def default_profile_id():
    database = _session()
    with database.connection_context():
        return _default_profile_id()


def _default_profile_id():
    default = AgentProviderProfile.get_or_none(AgentProviderProfile.is_default == True)
    if default:
        return default.profile_id
    first = AgentProviderProfile.select().order_by(AgentProviderProfile.created_at).first()
    if first:
        return first.profile_id
    from common.agent import model_status

    return ENVIRONMENT_PROFILE_ID if model_status()['ready'] else None


def resolve_profile_settings(profile_id):
    profile = get_profile(profile_id, include_secret=True)
    return ModelSettings.from_profile(profile)


def prepare_settings(profile_id, provider_id, base_url, model, api_key, provider_options=None):
    key = (api_key or '').strip()
    options = dict(provider_options or {})
    if profile_id:
        previous = get_profile(profile_id, include_secret=True)
        if previous['provider_id'] == provider_id:
            key = key or previous['api_key']
            if provider_id == 'columbus_agent' and not options.get('client_secret'):
                options['client_secret'] = previous.get('provider_options', {}).get('client_secret', '')
    settings = ModelSettings(api_key=key, model='' if provider_id == 'columbus_agent' else model.strip(),
                             base_url=base_url.strip(),
                             provider=provider_id, profile_id=profile_id, provider_options=options)
    settings.validate()
    return settings


def save_profile(profile_id, name, provider_id, base_url, model, api_key, make_default=False,
                 provider_options=None):
    from common.agent_providers import registered_provider_ids

    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        raise AgentError('请填写 1 到 64 个字符的配置名称。')
    if provider_id not in registered_provider_ids():
        raise AgentError('所选 Provider 尚未在服务端注册。')
    if not isinstance(base_url, str) or len(base_url.strip()) > 512:
        raise AgentError('服务地址无效，请检查地址长度。')
    key = (api_key or '').strip()
    if len(key) > 8192:
        raise AgentError('API 凭证过长。')
    if not isinstance(model, str) or len(model.strip()) > 191:
        raise AgentError('模型名称过长。')

    existing = None
    if profile_id:
        database = _session()
        with database.connection_context():
            existing = AgentProviderProfile.get_or_none(AgentProviderProfile.profile_id == profile_id)
        if existing is None:
            raise AgentError('要编辑的 Agent 配置不存在或已删除。')
    settings = prepare_settings(profile_id, provider_id, base_url, model, api_key, provider_options)
    key = settings.api_key

    database = _session()
    with database.connection_context(), database.atomic():
        if not existing and AgentProviderProfile.select().count() >= MAX_PROFILES:
            raise AgentError(f'最多保存 {MAX_PROFILES} 个 Agent 配置。')
        now = datetime.now()
        if make_default:
            AgentProviderProfile.update(is_default=False).execute()
        if existing:
            settings_changed = (
                existing.provider_id != provider_id or existing.base_url != base_url.strip()
                or existing.model != settings.model
                or _decrypt(existing.encrypted_api_key) != key
                or _options(profile_id, True) != settings.provider_options
            )
            if settings_changed:
                conversations = AgentConversationProfile.select(
                    AgentConversationProfile.conversation_id).where(
                    AgentConversationProfile.profile_id == profile_id)
                if AgentConversation.select().where(
                        AgentConversation.conversation_id.in_(conversations),
                        AgentConversation.pending_turn.is_null(False)).exists():
                    raise AgentError('该配置仍有对话正在处理，请等待完成后再修改连接参数。')
                AgentRemoteSession.delete().where(
                    AgentRemoteSession.conversation.in_(conversations)).execute()
                AgentProviderState.delete().where(
                    AgentProviderState.conversation.in_(conversations)).execute()
            AgentProviderProfile.update(
                name=name.strip(),
                provider_id=provider_id,
                base_url=base_url.strip(),
                model=settings.model,
                encrypted_api_key=_encrypt(key),
                is_default=bool(make_default or existing.is_default),
                updated_at=now,
            ).where(AgentProviderProfile.profile_id == profile_id).execute()
            AgentProviderOptions.replace(
                profile_id=profile_id,
                encrypted_options=_encrypt(json.dumps(settings.provider_options, ensure_ascii=False)),
            ).execute()
            return profile_id
        profile_id = uuid4().hex
        AgentProviderProfile.create(
            profile_id=profile_id,
            name=name.strip(),
            provider_id=provider_id,
            base_url=base_url.strip(),
            model=settings.model,
            encrypted_api_key=_encrypt(key),
            is_default=bool(make_default or AgentProviderProfile.select().count() == 0),
            created_at=now,
            updated_at=now,
        )
        AgentProviderOptions.create(
            profile_id=profile_id,
            encrypted_options=_encrypt(json.dumps(settings.provider_options, ensure_ascii=False)),
        )
        return profile_id


def set_default_profile(profile_id):
    profile = get_profile(profile_id)
    if profile['source'] != 'database':
        raise AgentError('服务端环境配置不能设为数据库默认配置。')
    database = _session()
    with database.connection_context(), database.atomic():
        AgentProviderProfile.update(is_default=False).execute()
        updated = AgentProviderProfile.update(is_default=True, updated_at=datetime.now()).where(
            AgentProviderProfile.profile_id == profile_id).execute()
        if not updated:
            raise AgentError('配置不存在或已删除。')


def delete_profile(profile_id):
    if not profile_id or profile_id == ENVIRONMENT_PROFILE_ID:
        raise AgentError('不能删除服务端环境配置。')
    database = _session()
    with database.connection_context(), database.atomic():
        row = AgentProviderProfile.get_or_none(AgentProviderProfile.profile_id == profile_id)
        if row is None:
            raise AgentError('配置不存在或已删除。')
        affected_conversations = [
            selection.conversation_id
            for selection in AgentConversationProfile.select(AgentConversationProfile.conversation_id).where(
                AgentConversationProfile.profile_id == profile_id)
        ]
        if affected_conversations:
            if AgentConversation.select().where(
                    AgentConversation.conversation_id.in_(affected_conversations),
                    AgentConversation.pending_turn.is_null(False)).exists():
                raise AgentError('该配置仍有对话正在处理，请等待完成后再删除。')
            AgentRemoteSession.delete().where(
                AgentRemoteSession.conversation.in_(affected_conversations)).execute()
            AgentProviderState.delete().where(
                AgentProviderState.conversation.in_(affected_conversations)).execute()
        AgentProviderOptions.delete().where(AgentProviderOptions.profile_id == profile_id).execute()
        row.delete_instance()
        fallback = _default_profile_id()
        if fallback and fallback != ENVIRONMENT_PROFILE_ID:
            AgentProviderProfile.update(is_default=True).where(
                AgentProviderProfile.profile_id == fallback).execute()
        if affected_conversations:
            AgentConversationProfile.update(
                profile_id=fallback, updated_at=datetime.now(),
            ).where(AgentConversationProfile.profile_id == profile_id).execute()


def profile_options():
    from common.agent import model_status

    profiles = list_profiles()
    options = [
        {'label': f"{row['name']} · {row['model'] or row['provider_id']}", 'value': row['profile_id']}
        for row in profiles
    ]
    if model_status()['ready']:
        options.append({
            'label': f"{t__dashboard('服务端环境配置')} · {t__dashboard('环境变量')}",
            'value': ENVIRONMENT_PROFILE_ID,
        })
    return options


def get_conversation_profile(owner, conversation_id):
    database = _session()
    with database.connection_context():
        conversation = AgentConversation.get_or_none(
            (AgentConversation.conversation_id == conversation_id)
            & (AgentConversation.owner == owner))
        if conversation is None:
            raise AgentError('会话不存在或无权访问。')
        selection = AgentConversationProfile.get_or_none(
            AgentConversationProfile.conversation_id == conversation_id)
        if selection and selection.profile_id:
            if selection.profile_id == ENVIRONMENT_PROFILE_ID:
                return selection.profile_id
            if AgentProviderProfile.select().where(
                    AgentProviderProfile.profile_id == selection.profile_id).exists():
                return selection.profile_id
        return _default_profile_id()


def set_conversation_profile(owner, conversation_id, profile_id):
    if profile_id is not None:
        get_profile(profile_id)
    database = _session()
    with database.connection_context(), database.atomic():
        conversation = AgentConversation.get_or_none(
            (AgentConversation.conversation_id == conversation_id)
            & (AgentConversation.owner == owner))
        if conversation is None:
            raise AgentError('会话不存在或无权访问。')
        if conversation.pending_turn:
            raise AgentError('当前会话正在处理，请等待完成后再切换配置。')
        current = AgentConversationProfile.get_or_none(
            AgentConversationProfile.conversation_id == conversation_id)
        previous_id = current.profile_id if current else _default_profile_id()
        if previous_id == profile_id:
            return
        AgentConversationProfile.insert(
            conversation_id=conversation_id, profile_id=profile_id, updated_at=datetime.now(),
        ).on_conflict(
            conflict_target=[AgentConversationProfile.conversation_id],
            update={
                AgentConversationProfile.profile_id: profile_id,
                AgentConversationProfile.updated_at: datetime.now(),
            },
        ).execute()
        AgentRemoteSession.delete().where(AgentRemoteSession.conversation == conversation).execute()
        AgentProviderState.delete().where(AgentProviderState.conversation == conversation).execute()
