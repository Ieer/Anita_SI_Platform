from flask import request, redirect, send_from_directory, abort, jsonify, Response
from common.exception import OAuth2Error
from common.utilities.util_oauth2 import current_token, require_oauth
from config.anita_go_conf import ShowConf, FlaskConf, CommonConf, PathProj
from common.utilities.util_logger import Log
from common.exception import global_exception_handler
from common.utilities.util_dash import CustomDash
from common.constant import HttpStatusConstant
from datetime import datetime, timedelta
import time
import threading
from i18n import t__other


logger = Log.get_logger(__name__)

# dash实例
app = CustomDash(
    __name__,
    suppress_callback_exceptions=True,
    compress=True,
    update_title=None, # type: ignore
    serve_locally=CommonConf.DASH_SERVE_LOCALLY,
    assets_path_ignore=[r'monaco-editor', r'three'],
    extra_hot_reload_paths=[],
    hooks={
        'request_pre': """
(payload) => {
    // 尝试获取键名为access_token的cookie，用于生成请求头令牌
    let access_token = document.cookie.match(/Authorization=([^;]+)/)
    // 为来自dash的请求添加请求头
    if (access_token){
        store.getState().config.fetch.headers['Authorization'] = access_token[1].replace(/"/g, '')
    }
}
"""
    },
    on_error=global_exception_handler,
)
app.server.config['COMPRESS_ALGORITHM'] = FlaskConf.COMPRESS_ALGORITHM
app.server.config['COMPRESS_BR_LEVEL'] = FlaskConf.COMPRESS_BR_LEVEL
app.server.secret_key = FlaskConf.COOKIE_SESSION_SECRET_KEY
app.title = ShowConf.WEB_TITLE

# flask实例
server = app.server
_task_sse_lock = threading.Lock()
_task_sse_connections = {}


@server.before_request
def reject_cross_site_callback():
    from urllib.parse import urlsplit

    if request.path == '/_dash-update-component' and request.method == 'POST':
        origin = request.headers.get('Origin')
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403)
        if origin and urlsplit(origin).netloc != request.host:
            abort(403)


@server.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


# 头像获取接口
@server.route('/avatar/<user_name>')
def download_file(user_name):
    file_name = f'{user_name}.jpg'
    if '..' in user_name:
        logger.warning(f'有人尝试通过头像文件接口攻击，URL:{request.url}，IP:{request.remote_addr}')
        abort(HttpStatusConstant.FORBIDDEN)
    else:
        return send_from_directory(PathProj.AVATAR_DIR_PATH, file_name)


@server.route('/task_log_sse', methods=['POST'])
def task_log_sse():
    from common.utilities.util_menu_access import get_menu_access
    from common import task_center
    from flask import stream_with_context
    import json

    menu_access = get_menu_access()
    if not menu_access.has_access('任务日志-页面'): # type: ignore
        response = jsonify({'error': 'Task Log SSE Permission Rejected.'})
        response.status_code = HttpStatusConstant.FORBIDDEN
        return response

    run_id = request.headers.get('run-id', '')
    try:
        cursor = int(request.headers.get('log-cursor', '0'))
        if not run_id or len(run_id) > 32 or cursor < 0:
            raise ValueError('Invalid run or cursor')
        first = task_center.read_log(menu_access, run_id, cursor)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid or inaccessible run.'}), 400
    except Exception:
        return jsonify({'error': 'Log service unavailable.'}), 503
    username = menu_access.user_name
    with _task_sse_lock:
        if _task_sse_connections.get(username, 0) >= 2 or sum(_task_sse_connections.values()) >= 32:
            return jsonify({'error': 'Too many log streams.'}), 429
        _task_sse_connections[username] = _task_sse_connections.get(username, 0) + 1
    released = False

    def release():
        nonlocal released
        with _task_sse_lock:
            if released:
                return
            released = True
            count = _task_sse_connections.get(username, 1) - 1
            if count:
                _task_sse_connections[username] = count
            else:
                _task_sse_connections.pop(username, None)

    @stream_with_context
    def _stream():
        try:
            result = first
            for tick in range(30):
                yield 'data: ' + json.dumps(result, ensure_ascii=False) + '\n\n'
                if result['complete']:
                    return
                time.sleep(1)
                try:
                    result = task_center.read_log(get_menu_access(), run_id, result['cursor'])
                except Exception:
                    yield 'event: closed\ndata: {"reason":"access_or_service_changed"}\n\n'
                    return
            yield 'event: reconnect\ndata: {}\n\n'
        finally:
            release()

    response = Response(_stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
    response.call_on_close(release)
    return response



# nginx代理后，拦截直接访问
@server.before_request
def ban_bypass_proxy():
    from config.anita_go_conf import ProxyConf

    if ProxyConf.NGINX_PROXY and request.headers.get('X-Forwarded-For') is None:
        abort(HttpStatusConstant.FORBIDDEN)


# 恶意访问管理页面拦截器
@server.before_request
def ban_admin():
    if request.path.startswith('/admin'):
        from common.utilities.util_browser import get_browser_info

        browser_info = get_browser_info()
        logger.warning(f'有人尝试访问不存在的管理页面，URL:{browser_info.url}，IP:{browser_info.request_addr}')
        abort(HttpStatusConstant.NOT_FOUND)


# 获取用户浏览器信息
@server.before_request
def get_user_agent_info():
    from common.utilities.util_browser import get_browser_info

    browser_info = get_browser_info()
    if browser_info.type == 'ie':
        return "<h1 style='color: red'>IP:{}, {}</h1>".format(browser_info.request_addr, t__other('请不要使用IE内核浏览器'))
    elif browser_info.type == 'chrome' and browser_info.version is not None and browser_info.version < 88:
        return "<h1 style='color: red'>IP:{}, {}</h1>".format(
            browser_info.request_addr,
            t__other('Chrome内核版本号太低，请升级浏览器'),
        )


# 自动管理数据库上下文
@server.before_request
def _db_connect():
    from database.sql_db.conn import db

    db().connect(reuse_if_open=True)


@server.teardown_request
def _db_close(exc):
    from database.sql_db.conn import db

    _db = db()
    if not _db.is_closed():
        _db.close()


@server.route('/oauth/authorize', methods=['GET', 'POST'])
def authorize():
    """第一步grant用户确认阶段
    OAuth2授权端点
    参数：
    - client_id: 客户端ID（必须）
    - redirect_uri: 重定向URI（必须）
    - response_type: 必须为'code'
    - scope: 请求的权限范围（可选，本项目为必选）
    - state: 客户端状态值（可选，本项目为必选）
    """
    from flask import request, render_template_string, session
    from common.utilities.util_jwt import AccessFailType
    from common.utilities.util_authorization import auth_validate
    from common.utilities.util_oauth2 import authorize_html
    from config.anita_go_conf import OAuth2Conf
    from database.sql_db.repositories.oauth2_repository import exist_client, insert_authorization_code
    from yarl import URL
    import secrets

    # 1. 如果没登陆，登录了再来认证
    if isinstance((rt_access := auth_validate()), AccessFailType):
        return redirect(URL.build(path='/login').with_query({'to': request.full_path}).__str__())
    ### 参数检查
    user_name = rt_access['user_name'] # type: ignore
    # 检查client_id
    if request.args.get('client_id') is None:
        raise OAuth2Error('Invalid_client_id')
    client = exist_client(client_id=request.args.get('client_id'))
    if client is None:
        raise OAuth2Error('Invalid_client_id')
    # 检查scope
    if not request.args.get('scope') or not client.check_scope(request.args.get('scope').split()):
        raise OAuth2Error('Invalid_scope')
    # 检查redirect_uri
    if request.args.get('redirect_uri') is None or not client.check_redirect_uri(request.args.get('redirect_uri')):
        raise OAuth2Error('Invalid_redirect_uri')
    # 检查response_type
    if request.args.get('response_type') != 'code':
        raise OAuth2Error('Invalid_response_type')
    # 检查state
    if not request.args.get('state'):
        raise OAuth2Error('Invalid_state')
    # 2. 登录啦，是否同意授权？
    if request.method == 'GET':
        csrf_token = secrets.token_urlsafe(32)
        session['oauth_consent'] = {'token': csrf_token, 'query': request.query_string.decode('utf-8'), 'user': user_name}
        return render_template_string(authorize_html, scope=request.args.get('scope'), client_id=client.client_id, user_name=user_name, csrf_token=csrf_token)

    # 3. 同意授权
    consent = session.pop('oauth_consent', {})
    if (consent.get('query') != request.query_string.decode('utf-8')
            or consent.get('user') != user_name
            or not secrets.compare_digest(consent.get('token', ''), request.form.get('csrf_token', ''))):
        raise OAuth2Error('Invalid consent request', 403)
    if request.form.get('confirm') != 'on':
        return redirect(str(URL(request.args['redirect_uri']).update_query(error='access_denied', state=request.args['state'])))
    # 生成code授权码
    if insert_authorization_code(
        code=(auth_code := secrets.token_urlsafe(OAuth2Conf.OAuth2AuthorizationCodeLength)),
        client_id=request.args.get('client_id'),
        user_name=user_name,
        expires_at=datetime.now() + timedelta(minutes=OAuth2Conf.OAuth2AuthorizationCodeExpiresInMinutes),
        redirect_uri=request.args.get('redirect_uri'),
        scope=request.args.get('scope'),
    ):
        return redirect(URL(request.args.get('redirect_uri')).update_query({'code': auth_code, 'state': request.args.get('state')}).__str__()) # type: ignore
    else:
        raise OAuth2Error('Internal error: Authorization code generation failed')


@server.route('/oauth/token', methods=['POST'])
def issue_token():
    """第三步token发放
    OAuth2令牌端点
    支持授权码模式（authorization_code）
    参数：
    - grant_type: 必须为'authorization_code'
    - code: 授权码（必须）
    - redirect_uri: 必须与授权请求时一致
    - client_id: 客户端ID（必须）
    - client_secret: 客户端密钥（必须）
    """
    from database.sql_db.repositories.oauth2_repository import exist_code, validate_client, exchange_authorization_code
    import secrets
    from config.anita_go_conf import OAuth2Conf
    from common.utilities.util_jwt import jwt_encode

    # 验证客户端凭证
    client_id = request.form.get('client_id')
    client_secret = request.form.get('client_secret')
    client = validate_client(client_id, client_secret)
    if not client:
        raise OAuth2Error('Invalid client credentials', HttpStatusConstant.UNAUTHORIZED)
    # 验证授权类型
    if request.form.get('grant_type') != 'authorization_code':
        raise OAuth2Error('Unsupported grant_type')
    # 获取授权码
    code = request.form.get('code')
    if not code:
        raise OAuth2Error('Missing authorization code')
    auth_code = exist_code(code=code, client_id=client_id)
    if not auth_code:
        raise OAuth2Error('Invalid authorization code', HttpStatusConstant.UNAUTHORIZED)
    # 验证授权码
    if not auth_code.is_valid():
        raise OAuth2Error('Authorization code expired')
    if not auth_code.check_client_id(client_id):
        raise OAuth2Error('Client mismatch')
    if not auth_code.check_redirect_uri(request.form.get('redirect_uri')):
        raise OAuth2Error('Redirect URI mismatch')
    if exchange_authorization_code(
        auth_code=auth_code,
        token=(
            token_ := jwt_encode(
                data={'user_name': auth_code.user_name, 'token_use': 'oauth', 'jti': secrets.token_urlsafe(16)},
                expires_delta=timedelta(minutes=OAuth2Conf.OAuth2TokenExpiresInMinutes),
            )
        ),
        expires_at=datetime.now() + timedelta(minutes=OAuth2Conf.OAuth2TokenExpiresInMinutes),
    ):
        response = jsonify(
            {
                'token_type': 'bearer',
                'access_token': token_,
                'expires_in': OAuth2Conf.OAuth2TokenExpiresInMinutes * 60,
                'scope': auth_code.scope,
            }
        )
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        return response
    else:
        raise OAuth2Error('Internal error: Token generation failed')


@server.route('/api/userinfo')
@require_oauth('userinfo')
def userinfo():
    """受保护端点"""
    from database.sql_db.repositories.user_repository import get_user_info
    from common.utilities.util_menu_access import MenuAccess
    from common.utilities.util_jwt import jwt_decode

    token = current_token()
    user_name = jwt_decode(token.token)['user_name'] # type: ignore
    if user_name != token.user_name:  # type: ignore # 不改数据库不可能发生
        abort(HttpStatusConstant.ERROR)
    user = get_user_info(user_names=[token.user_name])[0] # type: ignore
    access_metas = MenuAccess(token.user_name).all_access_metas # type: ignore
    return jsonify(
        {
            'user_name': user.user_name,
            'user_full_name': user.user_full_name,
            'user_sex': user.user_sex,
            'access_metas': sorted(access_metas),
            'user_email': user.user_email,
            'phone_number': user.phone_number,
            'user_remark': user.user_remark,
        }
    )


# oauth2_grant登录后重定向
@server.before_request
def oauth2_grant_redirect():
    if request.method == 'GET' and request.path == '/' and request.args.get('next') is not None:
        abort(400)


# OAuth2错误处理器
@server.errorhandler(OAuth2Error)
def handle_oauth2_error(e):
    return jsonify({'error': 'invalid_request', 'error_description': e.description}), e.status_code


# 首页重定向
@server.before_request
def main_page_redirct():
    if request.method == 'GET':
        if request.path in ('/', '/dashboard_/workbench'):
            return redirect('/dashboard_/monitor')
