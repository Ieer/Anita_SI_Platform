import json
import os
from urllib.parse import urlsplit

import httpx

from common.agent import AgentError, SYSTEM_PROMPT, redact
from common.agent_providers import AgentStreamEvent, AgentTurnResult


DEFAULT_MODELS = ('gpt-5-mini', 'claude-sonnet-4-6', 'gemini-3.1-pro-preview')


def configured_endpoint():
    endpoint = os.environ.get('SMART_AGENT_ENTERPRISE_MODELS_ENDPOINT', '').strip()
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise AgentError('Enterprise Models 服务地址未配置或无效。') from None
    if (any(character.isspace() for character in endpoint) or '\\' in endpoint
            or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or port == 0
            or parsed.hostname == 'example.com' or parsed.hostname.endswith('.example.com')):
        raise AgentError('Enterprise Models 服务地址未配置或无效。')
    return endpoint


def validate_options(model, options):
    models = options.get('models', list(DEFAULT_MODELS))
    if (not isinstance(models, list) or not 1 <= len(models) <= 50
            or any(not isinstance(item, str) or not item.strip() or item != item.strip()
                   or len(item) > 191 or any(character.isspace() for character in item) for item in models)
            or len(set(models)) != len(models) or model not in models):
        raise AgentError('Enterprise Models 模型必须属于有效且不重复的候选列表。')


class EnterpriseModelsProvider:
    provider_id = 'enterprise_models'

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    def stream_turn(self, request, tools, execute_tool, streaming=True):
        self.settings.validate()
        if not self.settings.api_key:
            raise AgentError('Enterprise Models 登录文件不可用或已过期，请重新完成企业 AI 登录。')
        if self.client is not None:
            return (yield from self._run(request, self.client))
        with httpx.Client(timeout=httpx.Timeout(90, connect=20), follow_redirects=False) as client:
            return (yield from self._run(request, client))

    def _run(self, request, client):
        version = self.settings.provider_options.get('_credential_version')
        state = request.provider_state or {}
        if not isinstance(state, dict):
            raise AgentError('Enterprise Models 对话状态无效。')
        if state.get('credential_version') != version:
            state = {}
        if state.get('interrupted'):
            raise AgentError('Enterprise Models 上轮状态不确定，请新建会话后重试。')
        payload = {
            'prompt': SYSTEM_PROMPT + '\n\n' + redact(request.prompt),
            'type': 'chatting', 'contextType': 'DEFAULT',
            'model': self.settings.model, 'useResponseApi': True,
        }
        for key in ('chatId', 'parentId'):
            if key in state:
                if not isinstance(state[key], str) or not 1 <= len(state[key]) <= 512:
                    raise AgentError('Enterprise Models 对话状态无效。')
                payload[key] = state[key]
        parts = []
        complete = False
        data_lines = []
        event_size = 0
        total_size = 0
        answer_size = 0
        pending = ''
        event_type = ''
        try:
            with client.stream('POST', self.settings.base_url, json=payload, follow_redirects=False, headers={
                'Authorization': 'Bearer ' + self.settings.api_key,
                'Accept': 'text/event-stream',
            }) as response:
                if response.status_code == 401:
                    if version:
                        from database.sql_db.repositories.agent_provider_repository import reject_credential

                        reject_credential(version)
                    raise AgentError('Enterprise Models 登录已失效，请重新完成企业 AI 登录。')
                if response.status_code == 403:
                    raise AgentError('Enterprise Models 上游拒绝授权，请检查账号权限。')
                if response.status_code != 200:
                    raise AgentError('Enterprise Models 服务请求失败，请稍后检查连接。')
                if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                    raise AgentError('Enterprise Models 未返回有效事件流。')
                buffer = ''
                for chunk in response.iter_text():
                    total_size += len(chunk)
                    if total_size > 1048576:
                        raise AgentError('Enterprise Models 响应过大。')
                    buffer += chunk
                    if len(buffer) > 262144:
                        raise AgentError('Enterprise Models 事件过大。')
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.removesuffix('\r')
                        if line.startswith('event:'):
                            event_type = line[6:].strip()
                        elif line.startswith('data:'):
                            value = line[5:].removeprefix(' ')
                            event_size += len(value)
                            if event_size > 262144:
                                raise AgentError('Enterprise Models 事件过大。')
                            data_lines.append(value)
                        elif line == '' and data_lines:
                            data = '\n'.join(data_lines)
                            data_lines = []
                            event_size = 0
                            if event_type == 'error':
                                raise ValueError
                            event_type = ''
                            if data in ('[DONE]', '[Done]'):
                                complete = True
                                break
                            item = json.loads(data)
                            if (not isinstance(item, dict) or item.get('error') or item.get('errorMsg')
                                    or item.get('success') is False or item.get('type') == 'error'):
                                raise ValueError
                            if item.get('chatId') is not None:
                                state = {**state, 'chatId': self._identifier(item['chatId'])}
                            current = item.get('current', [])
                            if not isinstance(current, list):
                                raise ValueError
                            if current:
                                message = current[0]
                                if not isinstance(message, dict):
                                    raise ValueError
                                if message.get('error'):
                                    raise ValueError
                                if message.get('id') is not None:
                                    state = {**state, 'parentId': self._identifier(message['id'])}
                                text = message.get('content', '')
                                if not isinstance(text, str):
                                    raise ValueError
                                if text:
                                    answer_size += len(text)
                                    if answer_size > 8000:
                                        raise AgentError('Enterprise Models 回答过长。')
                                    pending = (pending + text).replace(self.settings.api_key, '[REDACTED]')
                                    held = next((length for length in range(
                                        min(len(pending), len(self.settings.api_key) - 1), 0, -1)
                                        if pending.endswith(self.settings.api_key[:length])), 0)
                                    safe = pending[:-held] if held else pending
                                    pending = pending[-held:] if held else ''
                                    if safe:
                                        parts.append(safe)
                                        yield AgentStreamEvent('delta', safe)
                    if complete:
                        break
        except AgentError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
            raise AgentError('Enterprise Models 响应无效或连接中断，请检查企业 AI 登录。') from None
        if complete and pending:
            parts.append(pending)
            yield AgentStreamEvent('delta', pending)
        if not complete or not ''.join(parts).strip() or not state.get('chatId') or not state.get('parentId'):
            raise AgentError('Enterprise Models 响应不完整，请新建会话后重试。')
        state = {'chatId': state['chatId'], 'parentId': state['parentId'],
                  'credential_version': version, 'provider': self.provider_id}
        return AgentTurnResult(''.join(parts), [], self.settings.model, [], state['chatId'], state)

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 512:
            raise ValueError
        return value