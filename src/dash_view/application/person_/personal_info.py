from common.utilities.util_menu_access import MenuAccess
import feffery_antd_components as fac
import feffery_utils_components as fuc
from common.utilities.util_logger import Log
from dash_view.framework.user_avatar import render_user_avatar
from dash import html
from dash import dcc
from database.sql_db.repositories import user_repository
from config.enums import Sex
import dash_callback.application.person_.personal_info_c  # noqa
from i18n import t__person, t__default, t__access


# 二级菜单的标题、图标和显示顺序
title = '个人资料'
icon = None
logger = Log.get_logger(__name__)
order = 1
access_metas = ('个人信息-页面',)


def _editable_field(label, field_id, value):
    return html.Div(
        [
            html.Label(t__person(label), htmlFor=field_id),
            html.Div(
                [
                    fac.AntdInput(
                        id=field_id,
                        value=value,
                        defaultValue=value,
                        variant='borderless',
                        readOnly=True,
                        **{'aria-label': t__person(label)},
                    ),
                    fac.AntdButton(
                        fac.AntdIcon(icon='antd-edit'),
                        id=f'{field_id}-edit',
                        type='text',
                        title=t__default('编辑'),
                        **{'aria-label': t__default('编辑')},
                    ),
                    fac.AntdButton(
                        t__default('保存'),
                        id=f'{field_id}-save',
                        type='primary',
                        style={'display': 'none'},
                    ),
                    fac.AntdButton(
                        t__default('取消'),
                        id=f'{field_id}-cancel',
                        style={'display': 'none'},
                    ),
                ],
                className='personal-center-field-controls',
            ),
        ],
        className='personal-center-field',
    )


def render_content(menu_access: MenuAccess, **kwargs):
    user_info = user_repository.get_user_info([menu_access.user_name], exclude_disabled=True)[0]
    full_name = user_info.user_full_name or user_info.user_name
    status_text = t__default('启用' if user_info.user_status else '停用')
    return html.Main(
        [
            html.Section(
                [
                    dcc.Upload(
                        fac.AntdButton(
                            render_user_avatar(user_info, size=76, component_id='personal-info-avatar'),
                            id='personal-info-avatar-upload-button',
                            type='text',
                            shape='circle',
                            title=t__person('更换头像'),
                            **{'aria-label': t__person('更换头像')},
                        ),
                        id='personal-info-avatar-upload-choose',
                        accept='.jpeg,.jpg,.png',
                        max_size=10 * 1024 * 1024,
                    ),
                    html.Div(
                        [
                            html.H1(full_name, id='personal-info-display-name'),
                            html.Div(
                                [
                                    html.Span(user_info.user_name, className='personal-center-username'),
                                    html.Span(
                                        status_text,
                                        className='personal-center-status is-active' if user_info.user_status else 'personal-center-status',
                                    ),
                                ],
                                className='personal-center-meta',
                            ),
                        ],
                        className='personal-center-identity-copy',
                    ),
                ],
                className='personal-center-identity',
            ),
            fac.AntdTabs(
                id='personal-center-tabs',
                size='default',
                items=[
                    {
                        'key': 'profile',
                        'label': t__person('基本资料'),
                        'children': html.Section(
                            [
                                _editable_field('全名', 'personal-info-user-full-name', user_info.user_full_name),
                                html.Div(
                                    [
                                        html.Label(t__person('性别：'), htmlFor='personal-info-user-sex'),
                                        fac.AntdSelect(
                                            id='personal-info-user-sex',
                                            options=[{'label': t__access(item.value), 'value': item.value} for item in Sex],
                                            value=user_info.user_sex,
                                            defaultValue=user_info.user_sex,
                                            variant='borderless',
                                            allowClear=False,
                                        ),
                                    ],
                                    className='personal-center-field',
                                ),
                                _editable_field('邮箱', 'personal-info-user-email', user_info.user_email),
                                _editable_field('电话', 'personal-info-phone-number', user_info.phone_number),
                                _editable_field('描述', 'personal-info-user-remark', user_info.user_remark),
                            ],
                            className='personal-center-fields',
                        ),
                    },
                    {
                        'key': 'security',
                        'label': t__person('账户安全'),
                        'children': html.Section(
                            [
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Strong(t__person('登录密码')),
                                                html.Span(t__person('使用当前密码更新账户密码')),
                                            ],
                                            className='personal-center-security-copy',
                                        ),
                                        fac.AntdButton(
                                            t__person('修改密码'),
                                            id='personal-info-password-edit',
                                            type='default',
                                        ),
                                    ],
                                    className='personal-center-security-row',
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Strong(t__person('动态码验证')),
                                                html.Span(t__person('绑定动态码以增加登录验证')),
                                            ],
                                            className='personal-center-security-copy',
                                        ),
                                        fac.AntdPopconfirm(
                                            fac.AntdButton(t__person('绑定动态码'), type='default'),
                                            title=t__person('如已绑定，再次绑定会导致之前的认证码失效，请确认'),
                                            id='personal-info-show-otp-modal',
                                        ),
                                    ],
                                    className='personal-center-security-row',
                                ),
                            ],
                            className='personal-center-security',
                        ),
                    },
                ],
            ),
            fac.AntdModal(
                children=[
                    fac.AntdForm(
                        [
                            fac.AntdFormItem(fac.AntdInput(id='personal-info-change-password-old', mode='password'), label=t__person('旧密码'), required=True),
                            fac.AntdFormItem(fac.AntdInput(id='personal-info-change-password-new', mode='password'), label=t__person('新密码'), required=True),
                            fac.AntdFormItem(fac.AntdInput(id='personal-info-change-password-new-again', mode='password'), label=t__person('确认密码'), required=True),
                        ],
                        className={'.ant-form-item': {'marginBottom': '12px', 'marginRight': '8px'}},
                    )
                ],
                destroyOnClose=False,
                renderFooter=True,
                okText=t__default('确定'),
                cancelText=t__default('取消'),
                title=t__person('修改密码'),
                id='personal-info-change-password-modal',
            ),
            fac.AntdModal(
                children=[
                    fac.AntdSpace(
                        [
                            fac.AntdFormItem(fac.AntdInput(id='personal-info-verify-password-for-rqcode', mode='password'), label=t__person('旧密码'), required=True),
                            fac.AntdButton(t__person('生成授权二维码'), type='primary', id='personal-info-otp-show-rqcode'),
                            html.Div(id='personal-info-otp-rqcode-container'),
                        ],
                        direction='vertical',
                    )
                ],
                visible=False,
                renderFooter=False,
                maskClosable=False,
                destroyOnClose=False,
                title=t__person('绑定动态码（生成后，之前的动态码失效）'),
                id='personal-info-otp-modal',
            ),
        ],
        className='personal-center',
    )
