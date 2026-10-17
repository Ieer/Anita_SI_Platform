import hashlib
import json
import math
import threading
import time
from uuid import UUID

import httpx

from common.agent import AgentError, MAX_PROVIDER_STATE_BYTES, redact


MAX_STATE_BYTES = MAX_PROVIDER_STATE_BYTES
_tokens: dict[str, tuple[str, float]] = {}
_token_lock = threading.Lock()


def validate_options(options):
    if not isinstance(options, dict):
        raise AgentError('Columbus 配置必须是对象。')
    for key, label in (
        ('agent_id', 'Agent ID'), ('tenant_id', 'Tenant ID'), ('client_id', 'Client ID'),
    ):
        value = options.get(key)
        try:
            if not isinstance(value, str):
                raise ValueError
            UUID(value)
        except ValueError as error:
            raise AgentError(f'Columbus {label} 必须是有效 UUID。') from error
    for key, label, limit in (
        ('client_secret', 'Client Secret', 8192), ('scope', 'Scope', 512),
    ):
        value = options.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise AgentError(f'请填写有效的 Columbus {label}。')
    if len(json.dumps(options, ensure_ascii=False).encode('utf-8')) > MAX_STATE_BYTES:
        raise AgentError('Columbus 扩展配置过大。')


def _json(response, stage):
    if response.status_code in {401, 403}:
        raise AgentError(f'Columbus {stage}认证或授权失败，请检查凭证、订阅和 Scope。')
    if response.status_code == 429:
        raise AgentError('Columbus 请求受到限流，请稍后确认状态再重试。')
    if not response.is_success:
        raise AgentError(f'Columbus {stage}失败（HTTP {response.status_code}），请联系管理员。')
    content = bytearray()
    for chunk in response.iter_bytes():
        content.extend(chunk)
        if len(content) > 1048576:
            raise AgentError('Columbus 响应过大，无法安全处理。')
    try:
        body = json.loads(content)
    except ValueError as error:
        raise AgentError('Columbus 返回的不是有效 JSON；当前 Provider 尚不支持上游 SSE。') from error
    if not isinstance(body, dict):
        raise AgentError('Columbus 响应格式无效。')
    return body


class ColumbusProvider:
    provider_id = 'columbus_agent'

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    def _token_key(self):
        options = self.settings.provider_options
        return hashlib.sha256(json.dumps(
            {key: options[key] for key in ('tenant_id', 'client_id', 'client_secret', 'scope')},
            sort_keys=True,
        ).encode('utf-8')).hexdigest()

    def _access_token(self, client):
        key = self._token_key()
        with _token_lock:
            cached = _tokens.get(key)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            options = self.settings.provider_options
            response = client.post(
                f"https://login.microsoftonline.com/{options['tenant_id']}/oauth2/v2.0/token",
                data={
                    'grant_type': 'client_credentials', 'client_id': options['client_id'],
                    'client_secret': options['client_secret'], 'scope': options['scope'],
                },
                timeout=20,
            )
            data = _json(response, 'Token 获取')
            token = data.get('access_token')
            lifetime = data.get('expires_in')
            if (not isinstance(token, str) or not token or len(token) > 32768
                    or any(char in token for char in '\r\n')
                    or str(data.get('token_type', '')).lower() != 'bearer'
                    or isinstance(lifetime, bool) or not isinstance(lifetime, (int, float))
                    or not math.isfinite(lifetime) or lifetime <= 0):
                raise AgentError('Columbus Token 响应缺少有效凭证或有效期。')
            now = time.monotonic()
            for expired in [item for item, value in _tokens.items() if value[1] <= now]:
                del _tokens[expired]
            if len(_tokens) >= 32:
                del _tokens[next(iter(_tokens))]
            _tokens[key] = (token, now + max(0, lifetime - 60))
            return token

    def stream_turn(self, request, tools, execute_tool, streaming=True):
        from common.agent_providers import AgentStreamEvent

        self.settings.validate()
        yield AgentStreamEvent('status', '正在连接 Columbus Agent；当前使用完整结果模式…')
        try:
            if self.client is not None:
                return (yield from self._run(request, self.client))
            with httpx.Client(follow_redirects=False) as client:
                return (yield from self._run(request, client))
        except httpx.TimeoutException as error:
            raise AgentError('Columbus 请求超时；远端任务可能仍在执行，请确认状态后再重试，勿重复提交。') from error
        except httpx.RequestError as error:
            raise AgentError('Columbus 连接中断；远端执行状态未知，请确认后再重试。') from error

    def _run(self, request, client):
        from common.agent_providers import AgentStreamEvent, AgentTurnResult

        state = request.provider_state
        history = []
        if state is not None:
            if not isinstance(state, dict) or not isinstance(state.get('messages'), list):
                raise AgentError('Columbus 上轮未返回原生 messages，无法可靠续接。请新建对话，并向平台确认 history 协议。')
            history = state['messages']
            if not history or not all(isinstance(item, dict) for item in history):
                raise AgentError('Columbus 已保存的原生历史无效，请新建对话。')
        if len(json.dumps(history, ensure_ascii=False).encode('utf-8')) > MAX_STATE_BYTES:
            raise AgentError('Columbus 对话历史过大，请新建对话。')
        token = self._access_token(client)
        with client.stream(
            'POST',
            self.settings.base_url.rstrip('/') + '/columbus/platform/agent/message',
            headers={
                'Authorization': 'Bearer ' + token,
                'Ocp-Apim-Subscription-Key': self.settings.api_key,
                'Accept': 'application/json',
            },
            json={
                'agentId': self.settings.provider_options['agent_id'],
                'input': [{
                    'role': 'user', 'type': 'message',
                    'content': [{'type': 'input_text', 'text': redact(request.prompt)}],
                }],
                'history': history, 'background': False,
            },
            timeout=90,
        ) as response:
            if response.status_code == 401:
                with _token_lock:
                    _tokens.pop(self._token_key(), None)
            if 'text/event-stream' in response.headers.get('content-type', '').lower():
                raise AgentError('当前 Columbus Provider 尚不支持上游 SSE；请确认完整结果模式，勿重复提交任务。')
            body = _json(response, 'Agent 请求')
        data = body.get('data', body)
        if not isinstance(data, dict):
            raise AgentError('Columbus 响应缺少有效 data。')
        result = data.get('taskResult', data)
        if not isinstance(result, dict):
            raise AgentError('Columbus 任务结果格式无效。')
        detail = result.get('detail')
        if (body.get('errorDesc') or body.get('errorMsg') or body.get('error')
                or body.get('status') not in (None, 0, 200, 'SUCCESS')
                or (isinstance(detail, dict) and (
                    detail.get('errorType') or detail.get('code') not in (None, 0, 200, '200')))):
            raise AgentError('Columbus 返回业务错误，请检查限流、上下文长度或 Agent 插件配置。')
        answer_response = result.get('response', result)
        if not isinstance(answer_response, dict):
            raise AgentError('Columbus 回答格式无效。')
        status = data.get('taskStatus', answer_response.get('status'))
        if (status in {'FAILURE', 'REVOKED'}
                or answer_response.get('status') in {'FAILURE', 'REVOKED'}
                or answer_response.get('error')):
            raise AgentError('Columbus 任务失败或已取消，请联系管理员确认远端状态。')
        if status in {'PENDING', 'RECEIVED', 'RETRY', 'STARTED'} or (
                data.get('taskId') and 'output' not in answer_response):
            raise AgentError('Columbus 仅返回异步任务，查询协议尚未提供；请在远端确认任务状态，勿重复提交。')
        if status != 'SUCCESS':
            raise AgentError('Columbus 未返回明确的成功状态，无法确认回答。')
        if answer_response.get('status') not in (None, 'SUCCESS'):
            raise AgentError('Columbus 任务与回答状态不一致，无法确认回答。')
        output = answer_response.get('output')
        if not isinstance(output, list):
            raise AgentError('Columbus 响应缺少 output。')
        parts = []
        for message in output:
            if not isinstance(message, dict) or message.get('role') != 'assistant' or message.get('type') != 'message':
                continue
            content = message.get('content')
            if not isinstance(content, list):
                raise AgentError('Columbus 消息内容格式无效。')
            parts.extend(
                item['text'] for item in content
                if isinstance(item, dict) and item.get('type') == 'output_text'
                and isinstance(item.get('text'), str)
            )
        answer = '\n'.join(parts)
        for secret in (token, self.settings.api_key, self.settings.provider_options['client_secret']):
            answer = answer.replace(secret, '[REDACTED]')
        if not answer.strip():
            raise AgentError('Columbus 没有返回可显示的回答。')
        messages = result.get('messages', data.get('messages', body.get('messages')))
        if messages is None:
            messages = answer_response.get('messages')
        if messages is not None and (
                not isinstance(messages, list) or not messages
                or not all(isinstance(item, dict) for item in messages)):
            raise AgentError('Columbus 返回的原生 messages 格式无效。')
        next_state = {'messages': messages}
        if len(json.dumps(next_state, ensure_ascii=False).encode('utf-8')) > MAX_STATE_BYTES:
            raise AgentError('Columbus 返回的对话历史过大，请新建对话。')
        if messages is None:
            yield AgentStreamEvent('status', '上游未返回 messages；本次回答可显示，但下一轮无法可靠续接。')
        yield AgentStreamEvent('delta', answer[:8000])
        model = answer_response.get('model')
        return AgentTurnResult(
            answer=answer[:8000], trace=[], proposals=[],
            model=model[:191] if isinstance(model, str) and model else 'Columbus Agent',
            provider_state=next_state,
        )
