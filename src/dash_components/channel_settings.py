import feffery_antd_components as fac
from dash import html

from i18n import t__setting


def render_channel_settings(kind, actions, activation, items):
    listening = kind == 'listen'
    prefix = kind + '-api'
    return html.Main([
        html.Section([
            html.Div([
                html.Span(fac.AntdIcon(icon='antd-plus-circle'), className='channel-section-icon'),
                html.Div([
                    html.H2(t__setting('新增监听通道' if listening else '新增通知通道')),
                    html.P(t__setting('接收外部消息，为自动化流程提供输入。' if listening
                                     else '将任务结果发送到团队常用的消息或邮件平台。')),
                ]),
            ], className='channel-card-heading'),
            html.Div([
                html.Div([
                    html.Label(t__setting('通道名称'), htmlFor=prefix + '-add-name'),
                    fac.AntdInput(
                        id=prefix + '-add-name',
                        placeholder=t__setting('例如：团队告警 / 比赛演示'),
                        **{'aria-label': t__setting('通道名称')},
                    ),
                ], className='channel-name-field'),
                html.Div(actions, className='channel-add-actions'),
            ], className='channel-create-row'),
            html.Div([
                html.Span(fac.AntdIcon(icon='antd-info-circle'), className='channel-note-icon'),
                html.Span(t__setting('按2分钟的周期轮询邮箱，获取最新的邮件') if listening else
                          t__setting('新通道默认未启用，请先保存参数并测试，再勾选激活。'),
                          className='channel-note-text'),
                *([] if listening else [
                    html.A(t__setting('了解 Server酱服务'), href='https://sct.ftqq.com/r/16293',
                           target='_blank', rel='noopener noreferrer'),
                ]),
            ], className='channel-inline-note'),
        ], className='channel-card channel-create-card'),
        html.Div([
            html.Section([
                html.Div([
                    html.Span(fac.AntdIcon(icon='antd-check-circle'), className='channel-section-icon'),
                    html.Div([
                        html.H2(t__setting('激活通道')),
                        html.P(t__setting('勾选即启用，取消勾选即停用。')),
                    ]),
                ], className='channel-card-heading'),
                fac.AntdCheckboxGroup(options=activation[0], value=activation[1],
                                      id=prefix + '-activate'),
                html.Div(t__setting('暂无可激活通道，请先新增并配置。'),
                         className='channel-activation-empty'),
            ], className='channel-card channel-activation-card'),
            html.Section([
                html.Div([
                    html.Span(fac.AntdIcon(icon='antd-api'), className='channel-section-icon'),
                    html.Div([
                        html.H2(t__setting('通道配置')),
                        html.P(t__setting('选择通道，维护连接参数并验证服务。')),
                    ]),
                ], className='channel-card-heading'),
                fac.AntdTabs(
                    items=items, id=prefix + '-edit-tabs', tabPosition='top', tabBarGutter=16,
                    size='small',
                    placeholder=html.Div([
                        html.Span(fac.AntdIcon(icon='antd-mail'), className='channel-empty-icon'),
                        html.Strong(t__setting('尚未添加监听通道' if listening else '尚未添加通知通道')),
                        html.P(t__setting('在上方输入名称并选择通道类型，开始配置。')),
                    ], className='channel-empty-state'),
                    style={'width': '100%', 'minWidth': 0},
                ),
                html.Div([
                    html.Span(fac.AntdIcon(icon='antd-info-circle'), className='channel-note-icon'),
                    html.Span(t__setting('测试将实际连接邮箱，请使用测试账号。' if listening else
                                         '消息测试会实际发送通知，请使用测试接收人。'),
                              className='channel-note-text'),
                ], className='channel-inline-note'),
            ], className='channel-card channel-editor-card'),
        ], className='channel-settings-grid'),
    ], className='channel-settings-page', **{'aria-label': t__setting('监听接口' if listening else '通知接口')})
