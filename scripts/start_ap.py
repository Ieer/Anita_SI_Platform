"""
一鍵啟動：
1. 初始化資料庫（建立表、admin 使用者，具備冪等性）
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / 'src'
sys.path.insert(0, str(SRC_DIR))

from common.utilities.util_process_lock import ProcessFileLock
from common.utilities.util_startup import STARTUP_MODES, StartupConfigError, load_startup_config


def init_db():
    print('[DB] 显式初始化数据库...')
    from database.sql_db.conn import create_rds_table, init_rds_data

    create_rds_table()
    init_rds_data()
    print('[DB] 完成')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='启动 SI-Platform Web 与独立任务调度服务')
    parser.add_argument('--mode', choices=('debug', 'prod'), default='debug', help='Web 服务模式')
    parser.add_argument('--host', default='127.0.0.1', help='Web 监听地址')
    parser.add_argument('--port', type=int, default=8090, help='Web 监听端口')
    parser.add_argument('--threads', type=int, default=8, help='Waitress 工作线程数')
    parser.add_argument('--scheduler-mode', choices=STARTUP_MODES, help='覆盖已保存的调度启动策略')
    parser.add_argument('--ready-timeout', type=float, default=30, help='子服务就绪超时秒数')
    parser.add_argument('--shutdown-timeout', type=float, default=30, help='首次等待服务退出秒数')
    parser.add_argument('--reload', action='store_true', help='仅 web_only/debug 下启用开发自动重载')
    parser.add_argument('--init-db', action='store_true', help='显式初始化数据库后启动')
    parser.add_argument('--only-init', action='store_true', help='只初始化数据库，不启动服务')
    return parser.parse_args(argv)


def resolve_scheduler_mode(cli_mode, config_path=None):
    if cli_mode is not None:
        return cli_mode
    return load_startup_config(config_path)['scheduler_mode']


def _connect_scheduler_status(timeout):
    from common.utilities.util_scheduler_rpc import connect_scheduler_rpc
    from config.anita_go_conf import ApSchedulerConf

    connection = connect_scheduler_rpc(ApSchedulerConf.HOST, ApSchedulerConf.PORT, timeout=timeout)
    try:
        return json.loads(connection.root.get_status())
    finally:
        connection.close()


def _scheduler_port_is_open():
    from config.anita_go_conf import ApSchedulerConf

    try:
        with socket.create_connection((ApSchedulerConf.HOST, ApSchedulerConf.PORT), timeout=0.5):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as error:
        if getattr(error, 'winerror', None) == 10061 or error.errno == 111:
            return False
        raise RuntimeError('无法确认调度器 RPC 端口状态，已拒绝启动') from error


def _check_scheduler_conflict():
    if not _scheduler_port_is_open():
        return
    try:
        status = _connect_scheduler_status(timeout=2)
    except Exception as error:
        raise RuntimeError('调度器端口已被占用，但 RPC 身份无法验证；不会接管该进程') from error
    if status.get('service') == 'si-platform-scheduler' and status.get('ready'):
        raise RuntimeError(
            f"检测到已有调度器 PID {status.get('process_id')}；请使用 --scheduler-mode web_only 连接现有服务"
        )
    raise RuntimeError('调度器 RPC 端口已有未识别服务；不会接管该进程')


def _wait_for_scheduler(process, expected_state, timeout):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'调度器子进程提前退出，exit code={process.returncode}')
        try:
            status = _connect_scheduler_status(timeout=min(2, max(0.2, deadline - time.monotonic())))
            if (status.get('service') != 'si-platform-scheduler'
                    or not status.get('ready')
                    or status.get('process_id') != process.pid
                    or status.get('state') != expected_state):
                raise RuntimeError('调度器就绪状态与本次启动策略不匹配')
            return status
        except Exception as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f'等待调度器 RPC 就绪超时：{last_error}')


def _wait_for_web(process, host, port, timeout):
    connect_host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    url = f'http://{connect_host}:{port}/'
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Web 子进程提前退出，exit code={process.returncode}')
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as error:
            last_error = error
        time.sleep(0.25)
    raise RuntimeError(f'等待 Web 服务就绪超时：{last_error}')


def _spawn(command):
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    return subprocess.Popen(command, cwd=SRC_DIR, env=os.environ.copy(), creationflags=creationflags)


def _stop_web(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f'[退出] Web 子进程仍在结束，PID={process.pid}', file=sys.stderr, flush=True)
        process.wait()


def _stop_scheduler(process, shutdown_timeout):
    if process is None or process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
        except (AttributeError, OSError):
            process.send_signal(signal.SIGINT)
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=shutdown_timeout)
        return
    except subprocess.TimeoutExpired:
        print(
            f'[退出] 调度器仍在等待在途任务结束，PID={process.pid}；继续持有启动锁，不会强制终止',
            file=sys.stderr,
            flush=True,
        )
    process.wait()


def _supervise(web_process, scheduler_process):
    while True:
        for name, process in (('Web', web_process), ('调度器', scheduler_process)):
            if process is not None and process.poll() is not None:
                raise RuntimeError(f'{name}进程已退出，exit code={process.returncode}')
        time.sleep(0.5)


def run_services(args, scheduler_mode):
    if args.ready_timeout <= 0 or args.shutdown_timeout <= 0:
        raise ValueError('就绪和退出等待时间必须大于 0')
    if args.reload and (scheduler_mode != 'web_only' or args.mode != 'debug'):
        raise ValueError('--reload 仅可与 --scheduler-mode web_only --mode debug 一起使用')

    web_process = None
    scheduler_process = None
    try:
        if scheduler_mode != 'web_only':
            from common.utilities.util_scheduler_rpc import SchedulerRPCAuthenticator

            SchedulerRPCAuthenticator()
            _check_scheduler_conflict()
            paused = scheduler_mode == 'paired_paused'
            print(
                f"[启动策略] {scheduler_mode}；{'调度任务保持暂停' if paused else '已授权恢复计划和监听任务'}",
                flush=True,
            )
            scheduler_command = [sys.executable, str(SRC_DIR / 'app_apscheduler.py')]
            if paused:
                scheduler_command.append('--start-paused')
            scheduler_process = _spawn(scheduler_command)
            _wait_for_scheduler(
                scheduler_process,
                expected_state='paused' if paused else 'running',
                timeout=args.ready_timeout,
            )

        web_command = [
            sys.executable, str(SRC_DIR / 'app.py'),
            '--mode', args.mode, '--host', args.host, '--port', str(args.port), '--threads', str(args.threads),
        ]
        if args.reload:
            web_command.append('--reload')
        web_process = _spawn(web_command)
        _wait_for_web(web_process, args.host, args.port, args.ready_timeout)
        print(f'[Web] 已就绪：http://{args.host}:{args.port}', flush=True)
        _supervise(web_process, scheduler_process)
    finally:
        _stop_web(web_process)
        if scheduler_process is not None:
            _stop_scheduler(scheduler_process, args.shutdown_timeout)


def main(argv=None):
    args = parse_args(argv)
    os.chdir(SRC_DIR)
    if args.only_init:
        init_db()
        return
    if args.init_db:
        init_db()

    try:
        scheduler_mode = resolve_scheduler_mode(args.scheduler_mode)
    except StartupConfigError as error:
        raise SystemExit(f'启动配置无效：{error}') from error
    if scheduler_mode != 'web_only':
        from common.utilities.util_scheduler_rpc import SchedulerRPCAuthenticator

        SchedulerRPCAuthenticator()

    startup_lock_path = ROOT / 'user_data' / 'cache' / 'platform-startup.lock'
    with ProcessFileLock(startup_lock_path):
        try:
            run_services(args, scheduler_mode)
        except KeyboardInterrupt:
            print('\n[退出] 收到中断请求，正在按顺序停止自有服务进程...', flush=True)


if __name__ == '__main__':
    main()
"""
一鍵啟動：
1. 初始化資料庫（建立表、admin 使用者，具備冪等性）
2. 啟動應用（debug 或 prod 模式）

使用範例：
python scripts/start_app.py                        # 預設：初始化 + debug 啟動
python scripts/start_app.py --skip-init            # 跳過初始化
python scripts/start_app.py --only-init            # 只做初始化
python scripts/start_app.py --mode prod            # 以 waitress 生產模式啟動
python scripts/start_app.py --mode prod --port 9000
"""

import argparse
import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"

def init_db():
    print("[DB] 初始化資料庫（若已存在則跳過已有結構）...")
    from database.sql_db.conn import create_rds_table, init_rds_data
    create_rds_table()
    init_rds_data()
    print("[DB] 完成")

def run_debug(host: str, port: int):
    print(f"[APP] Debug 模式啟動 http://{host}:{port}")
    # 直接呼叫 app.py，沿用原本開發方式
    subprocess.run([sys.executable, "app.py", "--port", str(port)], cwd=SRC_DIR)

def run_prod(host: str, port: int, threads: int):
    print(f"[APP] Production(waitress) 模式啟動 http://{host}:{port} (threads={threads})")
    # 以 waitress 直接載入 app:server
    sys.path.insert(0, str(SRC_DIR))
    p.add_argument("--threads", type=int, default=8, help="prod 模式 waitress threads")
    import json
    p.add_argument("--skip-init", action="store_true", help="跳過資料庫初始化")
    import signal
    import socket
    p.add_argument("--only-init", action="store_true", help="只執行初始化不啟動")
    return p.parse_args()
    import time
    from urllib.error import URLError
    from urllib.request import urlopen

def main():
    args = parse_args()
    os.chdir(SRC_DIR)
    sys.path.insert(0, str(SRC_DIR))

    from common.utilities.util_process_lock import ProcessFileLock
    from common.utilities.util_startup import (
        STARTUP_MODES,
        StartupConfigError,
        load_startup_config,
    )


    def init_db():
        print("[DB] 显式初始化数据库...")
        from database.sql_db.conn import create_rds_table, init_rds_data

        create_rds_table()
        init_rds_data()
        print("[DB] 完成")


    def parse_args(argv=None):
        parser = argparse.ArgumentParser(description="启动 SI-Platform Web 与独立任务调度服务")
        parser.add_argument("--mode", choices=("debug", "prod"), default="debug", help="Web 服务模式")
        parser.add_argument("--host", default="127.0.0.1", help="Web 监听地址")
        parser.add_argument("--port", type=int, default=8090, help="Web 监听端口")
        parser.add_argument("--threads", type=int, default=8, help="Waitress 工作线程数")
        parser.add_argument("--scheduler-mode", choices=STARTUP_MODES, help="覆盖已保存的调度启动策略")
        parser.add_argument("--ready-timeout", type=float, default=30, help="子服务就绪超时秒数")
        parser.add_argument("--shutdown-timeout", type=float, default=30, help="等待服务退出秒数")
        parser.add_argument("--reload", action="store_true", help="仅 web_only/debug 下启用开发自动重载")
        parser.add_argument("--init-db", action="store_true", help="显式初始化数据库后启动")
        parser.add_argument("--only-init", action="store_true", help="只初始化数据库，不启动服务")
        return parser.parse_args(argv)


    def resolve_scheduler_mode(cli_mode, config_path=None):
        if cli_mode is not None:
            return cli_mode
        return load_startup_config(config_path)["scheduler_mode"]


    def _creation_flags():
        return subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0


    def _connect_scheduler_status(timeout):
        from common.utilities.util_scheduler_rpc import connect_scheduler_rpc
        from config.anita_go_conf import ApSchedulerConf

        connection = connect_scheduler_rpc(ApSchedulerConf.HOST, ApSchedulerConf.PORT, timeout=timeout)
        try:
            return json.loads(connection.root.get_status())
        finally:
            connection.close()


    def _scheduler_port_is_open():
        from config.anita_go_conf import ApSchedulerConf

        try:
            with socket.create_connection((ApSchedulerConf.HOST, ApSchedulerConf.PORT), timeout=0.5):
                return True
        except ConnectionRefusedError:
            return False
        except OSError as error:
            if getattr(error, "winerror", None) == 10061 or error.errno == 111:
                return False
            raise RuntimeError("无法确认调度器 RPC 端口状态，已拒绝启动") from error


    def _check_scheduler_conflict(timeout):
        if not _scheduler_port_is_open():
            return
        try:
            status = _connect_scheduler_status(timeout)
        except Exception as error:
            raise RuntimeError("调度器端口已被占用，但 RPC 身份无法验证；不会接管该进程") from error
        if status.get("service") == "si-platform-scheduler" and status.get("ready"):
            raise RuntimeError(
                f"检测到已有调度器 PID {status.get('process_id')}；请使用 --scheduler-mode web_only 连接现有服务"
            )
        raise RuntimeError("调度器 RPC 端口已有未识别服务；不会接管该进程")


    def _wait_for_scheduler(process, expected_state, timeout):
        from config.anita_go_conf import ApSchedulerConf

        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"调度器子进程提前退出，exit code={process.returncode}")
            try:
                status = _connect_scheduler_status(timeout=min(2, max(0.2, deadline - time.monotonic())))
                if (status.get("service") != "si-platform-scheduler"
                        or not status.get("ready")
                        or status.get("process_id") != process.pid
                        or status.get("state") != expected_state):
                    raise RuntimeError("调度器就绪状态与本次启动策略不匹配")
                return status
            except Exception as error:
                last_error = error
                time.sleep(0.25)
        raise RuntimeError(f"等待调度器 RPC 就绪超时：{last_error}")


    def _wait_for_web(process, host, port, timeout):
        connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        url = f"http://{connect_host}:{port}/"
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Web 子进程提前退出，exit code={process.returncode}")
            try:
                with urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError) as error:
                last_error = error
            time.sleep(0.25)
        raise RuntimeError(f"等待 Web 服务就绪超时：{last_error}")


    def _spawn(command, cwd):
        return subprocess.Popen(command, cwd=cwd, env=os.environ.copy(), creationflags=_creation_flags())


    def _stop_process(process, graceful, timeout):
        if process is None or process.poll() is not None:
            return True
        if graceful and os.name == "nt":
            try:
                os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            except OSError:
                pass
        elif graceful:
            process.send_signal(signal.SIGINT)
        else:
            process.terminate()
        try:
            process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False


    def _supervise(web_process, scheduler_process):
        while True:
            for name, process in (("Web", web_process), ("调度器", scheduler_process)):
                if process is not None and process.poll() is not None:
                    raise RuntimeError(f"{name}进程已退出，exit code={process.returncode}")
            time.sleep(0.5)


    def run_services(args, scheduler_mode):
        if args.ready_timeout <= 0 or args.shutdown_timeout <= 0:
            raise ValueError("就绪和退出等待时间必须大于 0")
        if args.reload and (scheduler_mode != "web_only" or args.mode != "debug"):
            raise ValueError("--reload 仅可与 --scheduler-mode web_only --mode debug 一起使用")

        web_process = None
        scheduler_process = None
        try:
            if scheduler_mode != "web_only":
                from common.utilities.util_scheduler_rpc import SchedulerRPCAuthenticator
                from config.anita_go_conf import ApSchedulerConf

                SchedulerRPCAuthenticator()
                if _scheduler_port_is_open():
                    _check_scheduler_conflict(timeout=2)
                paused = scheduler_mode == "paired_paused"
                print(f"[启动策略] {scheduler_mode}；{'调度任务保持暂停' if paused else '已授权恢复计划和监听任务'}", flush=True)
                scheduler_command = [sys.executable, str(SRC_DIR / "app_apscheduler.py")]
                if paused:
                    scheduler_command.append("--start-paused")
                scheduler_process = _spawn(scheduler_command, SRC_DIR)
                _wait_for_scheduler(
                    scheduler_process,
                    expected_state="paused" if paused else "running",
                    timeout=args.ready_timeout,
                )

            web_command = [
                sys.executable,
                str(SRC_DIR / "app.py"),
                "--mode", args.mode,
                "--host", args.host,
                "--port", str(args.port),
                "--threads", str(args.threads),
            ]
            if args.reload:
                web_command.append("--reload")
            web_process = _spawn(web_command, SRC_DIR)
            _wait_for_web(web_process, args.host, args.port, args.ready_timeout)
            print(f"[Web] 已就绪：http://{args.host}:{args.port}", flush=True)
            _supervise(web_process, scheduler_process)
        finally:
            if web_process is not None and not _stop_process(web_process, graceful=False, timeout=5):
                print(f"[退出] Web 子进程仍在运行，PID={web_process.pid}", file=sys.stderr, flush=True)
            if scheduler_process is not None and not _stop_process(
                    scheduler_process, graceful=True, timeout=args.shutdown_timeout):
                print(
                    f"[退出] 调度器未在等待期限内停止，仍在运行，PID={scheduler_process.pid}；未强制终止在途任务",
                    file=sys.stderr,
                    flush=True,
                )


    def main(argv=None):
        args = parse_args(argv)
        os.chdir(SRC_DIR)
        if args.only_init:
            init_db()
            return
        if args.init_db:
            init_db()

        try:
            scheduler_mode = resolve_scheduler_mode(args.scheduler_mode)
        except StartupConfigError as error:
            raise SystemExit(f"启动配置无效：{error}") from error
        if scheduler_mode != "web_only":
            from common.utilities.util_scheduler_rpc import SchedulerRPCAuthenticator

            SchedulerRPCAuthenticator()

        lock_path = ROOT / "user_data" / "cache" / "platform-startup.lock"
        try:
            with ProcessFileLock(lock_path):
                run_services(args, scheduler_mode)
        except KeyboardInterrupt:
            print("\n[退出] 收到中断请求，正在按顺序停止自有服务进程...", flush=True)
