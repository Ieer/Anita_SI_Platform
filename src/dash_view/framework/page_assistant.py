import os
from urllib.parse import urlsplit

import feffery_utils_components as fuc
from dash import html

from i18n import t__dashboard


LOCAL_PAGE_AGENT_URL = '/assets/vendor/page-agent/1.12.4/page-agent.demo.js'


def page_agent_url():
    return os.environ.get('SMART_AGENT_PAGE_AGENT_URL', '').strip() or LOCAL_PAGE_AGENT_URL


def validate_page_agent_url(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if not parsed.scheme and not parsed.netloc:
        parts = parsed.path.split('/')
        return (
            parsed.path.startswith('/assets/vendor/page-agent/')
            and '..' not in parts
            and parsed.path.endswith('.js')
            and not parsed.query
            and not parsed.fragment
        )
    return (
        parsed.scheme == 'https'
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def render_content():
    return html.Div([
        fuc.FefferyExternalJs(id='page-agent-script'),
        html.Div([
            html.Div([
                html.Strong(t__dashboard('页面助手')),
                html.Span(t__dashboard('仅辅助定位、填写和筛选，不执行任务'),
                          className='page-agent-policy'),
            ], className='page-agent-copy'),
            html.Button(
                t__dashboard('加载页面助手'),
                id='page-agent-launcher',
                type='button',
                className='page-agent-launcher',
                **{'aria-label': t__dashboard('加载页面助手')},
            ),
        ], className='page-agent-toolbar'),
        html.Div(
            t__dashboard('当前页面：等待页面上下文'),
            id='page-agent-scope',
            className='page-agent-scope',
            role='status',
        ),
        html.Div(
            t__dashboard('页面助手未加载。点击按钮后才会连接浏览器辅助脚本。'),
            id='page-agent-status',
            className='page-agent-status',
            role='status',
            **{'aria-live': 'polite'},
        ),
    ], id='page-assistant', className='page-assistant')
