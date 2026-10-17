import json
import os
from urllib.parse import urlsplit

import feffery_antd_components as fac
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
    labels = {key: t__dashboard(key) for key in (
        '启用', '加载中…', '已启用', '重试', '等待页面上下文', '当前页面',
        '当前页面暂无辅助能力', '工具已就绪，模型连接将在使用时建立。',
        '加载失败或超时，请重试。', '正在加载页面辅助…',
        '系统监控', '任务概览', '任务日志', '读取指标', '打开任务中心',
        '查询任务', '打开任务日志', '筛选任务', '读取脱敏日志', '打开任务概览',
    )}
    return html.Div([
        fuc.FefferyExternalJs(id='page-agent-script'),
        html.Details([
            html.Summary([
                fac.AntdIcon(icon='antd-control'),
                html.Strong(t__dashboard('页面辅助')),
                fac.AntdIcon(icon='antd-down', className='page-agent-chevron'),
            ], title=t__dashboard('页面辅助详情')),
            html.Div([
                html.Span(t__dashboard('等待页面上下文'), id='page-agent-scope',
                          className='page-agent-scope'),
                html.Span(t__dashboard('独立工具'), className='page-agent-tool-label'),
                html.P(t__dashboard('仅辅助定位、填写和筛选，不执行任务'),
                       className='page-agent-policy'),
                html.Div(id='page-agent-capabilities', className='page-agent-capabilities'),
                html.Button(
                    html.Span(t__dashboard('启用')),
                    id='page-agent-launcher',
                    type='button',
                    className='page-agent-launcher',
                    **{'aria-label': t__dashboard('加载页面助手')},
                ),
                html.Div(
                    t__dashboard('未启用'),
                    id='page-agent-status',
                    className='page-agent-status',
                    role='status',
                    **{'aria-live': 'polite'},
                ),
            ], className='page-agent-details-content'),
        ], className='page-agent-details'),
    ], id='page-assistant', className='page-assistant',
        **{'data-page-agent-i18n': json.dumps(labels, ensure_ascii=False)})
