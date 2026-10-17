import json
from dataclasses import dataclass
import importlib
import os
from typing import Callable, Iterator, Protocol

from common.agent import AgentError, ModelSettings, SYSTEM_PROMPT, redact, redact_structure


@dataclass(frozen=True)
class AgentTurnRequest:
    prompt: str
    history: list[dict]
    remote_conversation_id: str | None = None
    provider_state: dict | None = None


@dataclass(frozen=True)
class AgentStreamEvent:
    type: str
    text: str = ''
    remote_conversation_id: str | None = None


@dataclass(frozen=True)
class AgentTurnResult:
    answer: str
    trace: list[dict]
    model: str
    proposals: list[dict]
    remote_conversation_id: str | None = None
    provider_state: dict | None = None


ToolExecutor = Callable[[str, dict], dict]


class AgentProvider(Protocol):
    provider_id: str

    def stream_turn(
        self,
        request: AgentTurnRequest,
        tools: list[dict],
        execute_tool: ToolExecutor,
        streaming: bool = True,
    ) -> Iterator[AgentStreamEvent]:
        ...


class OpenAICompatibleProvider:
    provider_id = 'openai_compatible'

    def __init__(self, settings: ModelSettings, client=None):
        self.settings = settings
        self.client = client

    def stream_turn(self, request, tools, execute_tool, streaming=True):
        if self.client is not None:
            return (yield from self._run(request, tools, execute_tool, self.client, streaming))

        from openai import OpenAI

        with OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url,
                    timeout=20, max_retries=0) as client:
            return (yield from self._run(request, tools, execute_tool, client, streaming))

    def _run(self, request, tools, execute_tool, client, streaming):
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        for item in request.history[-12:]:
            if (item.get('role') in {'user', 'assistant'}
                    and isinstance(item.get('content'), str) and item['content'].strip()):
                messages.append({'role': item['role'], 'content': redact(item['content'])[:8000]})
        messages.append({'role': 'user', 'content': redact(request.prompt)[:4000]})
        trace = []
        proposals = []

        for round_number in range(3):
            options = {'model': self.settings.model, 'messages': messages, 'max_tokens': 1200}
            if tools:
                options['tools'] = tools
            try:
                response = client.chat.completions.create(**options, stream=streaming)
                reader = self._read_response(response, streaming)
                if streaming:
                    reader = iter(reader)
                    content_parts = []
                    while True:
                        try:
                            event = next(reader)
                        except StopIteration as completed:
                            message = completed.value
                            break
                        content_parts.append(event.text)
                        yield event
                else:
                    message, content_parts = reader
            except AgentError:
                raise
            except Exception as error:
                raise AgentError('模型请求失败或超时，请检查服务端模型配置后重试。') from error

            calls = message.tool_calls or []
            if not calls:
                answer = ''.join(content_parts)
                if not answer.strip():
                    raise AgentError('模型没有返回可显示的回答。')
                return AgentTurnResult(answer[:8000], trace, self.settings.model, proposals,
                                       request.remote_conversation_id)

            if round_number == 2 or len(calls) > 2:
                raise AgentError('已达到工具调用上限，请缩小查询范围后重试。')
            if any(not isinstance(call.id, str) or not call.id or len(call.id) > 128 for call in calls):
                raise AgentError('模型工具调用 ID 无效。')
            if len({call.id for call in calls}) != len(calls):
                raise AgentError('模型工具调用 ID 重复。')
            messages.append({
                'role': 'assistant',
                'content': redact(message.content) if message.content else None,
                'tool_calls': [
                    {'id': call.id, 'type': 'function',
                     'function': {'name': call.function.name, 'arguments': call.function.arguments}}
                    for call in calls
                ],
            })
            if content_parts:
                yield AgentStreamEvent('status', '正在处理授权工具请求…')
            for call in calls:
                if not isinstance(call.function.arguments, str) or len(call.function.arguments) > 1000:
                    raise AgentError('模型工具参数过长或格式错误。')
                try:
                    arguments = json.loads(call.function.arguments)
                except (TypeError, ValueError) as error:
                    raise AgentError('模型工具参数不是有效 JSON。') from error
                if not isinstance(arguments, dict):
                    raise AgentError('工具参数必须是对象。')
                if call.function.name == 'propose_run_task' and proposals:
                    raise AgentError('每轮只能提议运行一个任务。')
                result = execute_tool(call.function.name, arguments)
                if call.function.name == 'propose_run_task':
                    proposals.append(result['proposal'])
                trace.append({
                    'tool': call.function.name,
                    'status': result['status'],
                    'arguments': redact(json.dumps(arguments, ensure_ascii=False)),
                })
                messages.append({
                    'role': 'tool', 'tool_call_id': call.id,
                    'content': json.dumps(redact_structure(result), ensure_ascii=False, default=str),
                })
                yield AgentStreamEvent('status', '工具处理完成，正在整理答复…')

        raise AgentError('已达到模型调用上限。')

    @staticmethod
    def _read_response(response, streaming):
        if not streaming:
            message = response.choices[0].message
            content = message.content if isinstance(message.content, str) else ''
            return message, [content] if content else []

        def read_stream():
            content_parts = []
            tool_calls = {}
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if isinstance(delta.content, str) and delta.content:
                    content_parts.append(delta.content)
                    yield AgentStreamEvent('delta', delta.content)
                for item in delta.tool_calls or []:
                    current = tool_calls.setdefault(item.index, {
                        'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''},
                    })
                    if item.id:
                        current['id'] = item.id
                    if item.function:
                        if item.function.name:
                            current['function']['name'] += item.function.name
                        if item.function.arguments:
                            current['function']['arguments'] += item.function.arguments

            from types import SimpleNamespace

            message = SimpleNamespace(
                content=''.join(content_parts),
                tool_calls=[
                    SimpleNamespace(
                        id=item['id'],
                        function=SimpleNamespace(**item['function']),
                    )
                    for _, item in sorted(tool_calls.items())
                ],
            )
            return message

        return read_stream()


_PROVIDER_FACTORIES = {OpenAICompatibleProvider.provider_id: OpenAICompatibleProvider}


def registered_provider_ids():
    from common.columbus_provider import ColumbusProvider

    _PROVIDER_FACTORIES.setdefault(ColumbusProvider.provider_id, ColumbusProvider)
    module_name = os.environ.get('SMART_AGENT_PROVIDER_MODULE', '').strip()
    if module_name:
        try:
            importlib.import_module(module_name)
        except Exception as error:
            raise AgentError('外部 Agent Provider 加载失败，请检查服务端模块配置。') from error
    return tuple(sorted(_PROVIDER_FACTORIES))


def register_provider(provider_id, factory):
    if not isinstance(provider_id, str) or not provider_id or provider_id in _PROVIDER_FACTORIES:
        raise ValueError('Provider ID must be non-empty and unique.')
    _PROVIDER_FACTORIES[provider_id] = factory


def create_provider(settings, client=None):
    factory = _PROVIDER_FACTORIES.get(settings.provider)
    if factory is None:
        registered_provider_ids()
        factory = _PROVIDER_FACTORIES.get(settings.provider)
    if factory is None:
        raise AgentError(f'未注册的 Agent Provider：{settings.provider}')
    return factory(settings, client=client)
