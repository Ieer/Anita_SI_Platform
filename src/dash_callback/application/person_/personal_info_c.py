from dash.dependencies import Input, Output, State
from server import app
from common.utilities.util_file_trans import AvatarFile
from dash_components import MessageManager
from dash import set_props
from database.sql_db.dao import dao_user
import dash
from common.utilities.util_menu_access import get_menu_access
from uuid import uuid4
from dash.exceptions import PreventUpdate
from i18n import t__person


# 头像上传模块
@app.callback(
    Input('personal-info-avatar-upload-choose', 'contents'),
    [
        State('personal-info-avatar', 'src'),
        State('global-head-avatar', 'src'),
    ],
    prevent_initial_call=True,
)
def callback_func(contents, src, _):
    menu_access = get_menu_access()
    if contents is not None:
        note, base_str = contents.split(',', 1)
        img_suffix = note.split(';')[0].split('/')[-1]

        AvatarFile.save_avatar_file(base64_str=base_str, img_type=img_suffix, user_name=menu_access.user_name)
    # 强制刷新url，实际对象没变
    url = src.split('?')[0] + f'?{str(uuid4())}'
    # 个人页
    set_props('personal-info-avatar', {'src': url})
    # head
    set_props('global-head-avatar', {'src': url})


def _register_profile_field(field_id, update_user, success_text, failure_text, sync_display_name=False):
    edit_id = f'{field_id}-edit'
    save_id = f'{field_id}-save'
    cancel_id = f'{field_id}-cancel'
    @app.callback(
        [
            Output(field_id, 'readOnly'),
            Output(field_id, 'variant'),
            Output(edit_id, 'style'),
            Output(save_id, 'style'),
            Output(cancel_id, 'style'),
            Output(field_id, 'value'),
            Output(field_id, 'defaultValue'),
        ],
        [
            Input(edit_id, 'nClicks'),
            Input(save_id, 'nClicks'),
            Input(cancel_id, 'nClicks'),
            Input(field_id, 'nSubmit'),
        ],
        [State(field_id, 'value'), State(field_id, 'defaultValue')],
        prevent_initial_call=True,
    )

    def save_profile_field(edit_clicks, save_clicks, cancel_clicks, submitted, value, default_value):
        del edit_clicks, save_clicks, cancel_clicks, submitted
        no_update = dash.no_update
        if dash.ctx.triggered_id == edit_id:
            return False, 'outlined', {'display': 'none'}, {'display': 'inline-flex'}, {'display': 'inline-flex'}, no_update, no_update
        if dash.ctx.triggered_id == cancel_id:
            return True, 'borderless', {'display': 'inline-flex'}, {'display': 'none'}, {'display': 'none'}, default_value, default_value

        value = value or ''
        default_value = default_value or ''
        if value == default_value:
            updated = True
        else:
            updated = update_user(
                user_name=get_menu_access(only_get_user_name=True),
                value=value,
            )
        if not updated:
            MessageManager.warning(content=t__person(failure_text))
            return False, 'outlined', {'display': 'none'}, {'display': 'inline-flex'}, {'display': 'inline-flex'}, no_update, no_update

        if sync_display_name:
            display_name = value or get_menu_access(only_get_user_name=True)
            set_props('personal-info-display-name', {'children': display_name})
            set_props('personal-info-avatar', {'alt': display_name})
            set_props('global-head-avatar', {'alt': display_name})
        if value != default_value:
            MessageManager.success(content=t__person(success_text))
        return True, 'borderless', {'display': 'inline-flex'}, {'display': 'none'}, {'display': 'none'}, value, value

    return save_profile_field


_register_profile_field(
    'personal-info-user-full-name',
    lambda user_name, value: dao_user.update_user_full_name(user_name=user_name, user_full_name=value),
    '用户全名更新成功',
    '用户全名更新失败',
    sync_display_name=True,
)
_register_profile_field(
    'personal-info-user-email',
    lambda user_name, value: dao_user.update_user_email(user_name=user_name, user_email=value),
    '用户邮箱更新成功',
    '用户邮箱更新失败',
)
_register_profile_field(
    'personal-info-phone-number',
    lambda user_name, value: dao_user.update_phone_number(user_name=user_name, phone_number=value),
    '用户电话更新成功',
    '用户电话更新失败',
)
_register_profile_field(
    'personal-info-user-remark',
    lambda user_name, value: dao_user.update_user_remark(user_name=user_name, user_remark=value),
    '用户描述更新成功',
    '用户描述更新失败',
)


@app.callback(
    Input('personal-info-user-sex', 'value'),
    State('personal-info-user-sex', 'defaultValue'),
    prevent_initial_call=True,
)
def update_user_sex(value, default_value):
    if dao_user.update_user_sex(user_name=get_menu_access(only_get_user_name=True), user_sex=value):
        MessageManager.success(content=t__person('用户性别更新成功'))
    else:
        set_props('personal-info-user-sex', {'value': default_value})
        MessageManager.warning(content=t__person('用户性别更新失败'))


# 修改密码开关
app.clientside_callback(
    """(_) => {
        return true
    }""",
    Output('personal-info-change-password-modal', 'visible'),
    Input('personal-info-password-edit', 'nClicks'),
    prevent_initial_call=True,
)


# 修改密码
@app.callback(
    Input('personal-info-change-password-modal', 'okCounts'),
    [
        State('personal-info-change-password-old', 'value'),
        State('personal-info-change-password-new', 'value'),
        State('personal-info-change-password-new-again', 'value'),
    ],
    prevent_initial_call=True,
)
def update_password(okCounts, old_password, new_password, new_password_again):
    if not old_password:
        MessageManager.warning(content=t__person('请填写旧密码'))
        return
    if new_password != new_password_again:
        MessageManager.warning(content=t__person('密码不一致，请重新填写'))
        return
    if dao_user.update_user_password(user_name=get_menu_access(only_get_user_name=True), new_password=new_password, old_password=old_password):
        MessageManager.success(content=t__person('用户密码更新成功'))
    else:
        MessageManager.warning(content=t__person('用户密码验证错误'))


# 显示otp对话框
@app.callback(
    [
        Output('personal-info-otp-modal', 'visible'),
        Output('personal-info-otp-rqcode-container', 'children', allow_duplicate=True),
        Output('personal-info-verify-password-for-rqcode', 'value'),
    ],
    Input('personal-info-show-otp-modal', 'confirmCounts'),
    prevent_initial_call=True,
)
def show_otp_modal(_):
    return True, None, None


# 生成OTP二维码
@app.callback(
    Output('personal-info-otp-rqcode-container', 'children', allow_duplicate=True),
    Input('personal-info-otp-show-rqcode', 'nClicks'),
    State('personal-info-verify-password-for-rqcode', 'value'),
    prevent_initial_call=True,
)
def gen_otp_rqcode(_, password):
    from otpauth import TOTP
    import feffery_antd_components as fac
    import feffery_utils_components as fuc
    from config.dashgo_conf import ShowConf

    user_name = get_menu_access(only_get_user_name=True)

    if not password:
        MessageManager.warning(content=t__person('请填写密码'))
        return dash.no_update
    ret = dao_user.gen_otp_qrcode(user_name=user_name, password=password)
    if not ret:
        MessageManager.warning(content=t__person('密码校验错误'))
        return dash.no_update
    otp_secret: str = ret
    totp = TOTP(otp_secret.encode())
    uri_otp = totp.to_uri(label=f'{ShowConf.APP_NAME}:{user_name}', issuer=ShowConf.APP_NAME)
    return fac.AntdSpace(
        [
            fac.AntdText(t__person('请打开微信，搜索“腾讯身份验证器”，点击“二维码激活”，扫描以下二维码（为防止清理缓存丢失，扫描完成后，请点击设置-备份令牌到云！！）')),
            fuc.FefferyQRCode(value=uri_otp, size=256),
        ],
        direction='vertical',
    )
