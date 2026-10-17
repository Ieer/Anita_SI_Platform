"""Run delivery checks with temporary configuration and an isolated database."""

import argparse
from configparser import ConfigParser
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(folder):
    folder = Path(folder)
    config = ConfigParser()
    config.read(ROOT / 'src' / 'config' / 'anita_go.ini.example', encoding='utf-8')
    config['CommonConf']['ENCRYPT_KEY'] = secrets.token_hex(32)
    config['FlaskConf']['COOKIE_SESSION_SECRET_KEY'] = secrets.token_urlsafe(48)
    config['JwtConf']['JWT_SECRET_KEY'] = secrets.token_urlsafe(48)
    config['SqlDbConf']['RDB_TYPE'] = 'sqlite'
    config['SqlDbConf']['SQLITE_DB_PATH'] = str(folder / 'ci.sqlite3')
    config['LogConf']['HANDLER_LOG_FILE'] = 'False'
    config_path = folder / 'ci.ini'
    with config_path.open('w', encoding='utf-8') as stream:
        config.write(stream)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(('SMART_AGENT_', 'PLATFORM_'))
    }
    environment.update({
        'SMART_AGENT_CONFIG_FILE': str(config_path),
        'SMART_AGENT_INITIAL_ADMIN_PASSWORD': secrets.token_urlsafe(32),
        'SMART_AGENT_RUN_ALLOWLIST': '[]',
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
    })
    return environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-smoke', action='store_true')
    args = parser.parse_args(argv)
    reports = ROOT / 'cache' / 'report' / 'ci'
    reports.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='si-platform-ci-') as folder:
        environment = isolated_environment(folder)
        if args.browser_smoke:
            environment['PLATFORM_AGENT_UI_PROBE'] = '1'
            tests = [str(ROOT / 'tests' / 'test_agent_ui.py'), '-k',
                     'login_progress_and_failure_follow_language or robot_and_persistent_dialog']
        else:
            tests = [str(ROOT / 'tests')]
        checks = [] if args.browser_smoke else [
            ['-m', 'pip', 'check'],
            ['-m', 'ruff', 'check', 'src', 'scripts', 'tools', 'tests'],
            [str(ROOT / 'scripts' / 'check_pages.py')],
        ]
        checks += [
            [str(ROOT / 'scripts' / 'start_ap.py'), '--only-init'],
            ['-m', 'pytest', *tests, '-q', '--junitxml',
             str(reports / ('browser.xml' if args.browser_smoke else 'tests.xml'))],
        ]
        for check in checks:
            print('[check] ' + ' '.join(check), flush=True)
            result = subprocess.run([sys.executable, *check], cwd=ROOT, env=environment, check=False)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())