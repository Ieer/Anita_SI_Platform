"""Safe command-line shortcuts for the existing platform scripts."""

import argparse
import os
import secrets
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {
    'web': 'Start Web only (no scheduler)',
    'paired-paused': 'Start Web and a paused scheduler',
    'demo': 'Start the isolated demo',
    'check-pages': 'Validate registered Dash pages',
    'test': 'Run the test suite (real integrations remain opt-in)',
    'check-deps': 'Check installed dependency compatibility',
    'init-db': 'Initialize database tables and initial account (writes data)',
}


def project_python():
    relative = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    executable = ROOT / '.venv' / relative
    return str(executable) if executable.is_file() else sys.executable


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return port


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=tuple(ACTIONS), help='omit to open the menu')
    parser.add_argument('--port', type=port_number, help='Web port (8090), or demo port (8097)')
    parser.add_argument('--mode', choices=('debug', 'prod'), default='prod', help='Web mode (default: prod)')
    parser.add_argument('--dry-run', action='store_true', help='print the command without executing it')
    parser.add_argument('--yes', action='store_true', help='explicitly authorize database initialization')
    args = parser.parse_args(argv)
    if args.port is not None and args.action not in (None, 'web', 'paired-paused', 'demo'):
        parser.error('--port is only valid for a service action')
    if args.dry_run and args.action is None:
        parser.error('--dry-run requires an action')
    return args


def build_command(action, args):
    command = [project_python()]
    if action in ('web', 'paired-paused'):
        command += [str(ROOT / 'scripts' / 'start_ap.py'), '--scheduler-mode',
                    'web_only' if action == 'web' else 'paired_paused',
                    '--mode', args.mode, '--host', '127.0.0.1', '--port',
                    str(args.port if args.port is not None else 8090)]
    elif action == 'demo':
        command += [str(ROOT / 'scripts' / 'demo_task_center.py'), '--port',
                    str(args.port if args.port is not None else 8097)]
    elif action == 'check-pages':
        command += [str(ROOT / 'scripts' / 'check_pages.py')]
    elif action == 'test':
        command += ['-m', 'pytest', str(ROOT / 'tests'), '-q']
    elif action == 'check-deps':
        command += ['-m', 'pip', 'check']
    elif action == 'init-db':
        command += [str(ROOT / 'scripts' / 'start_ap.py'), '--only-init']
    else:
        raise ValueError(f'Unknown action: {action}')
    return command


def execute(action, args, interactive=False):
    command = build_command(action, args)
    display = subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)
    print(f'[command] {display}', flush=True)
    if args.dry_run:
        return 0
    if action == 'init-db' and not args.yes:
        if not interactive or input('Database write: type INIT to continue: ').strip() != 'INIT':
            print('Cancelled. Use init-db --yes to explicitly authorize database writes.', file=sys.stderr)
            return 2
    environment = os.environ.copy()
    if action == 'paired-paused' and not environment.get('SMART_AGENT_SCHEDULER_RPC_SECRET'):
        environment['SMART_AGENT_SCHEDULER_RPC_SECRET'] = secrets.token_urlsafe(48)
        print('[scheduler] Temporary RPC secret; existing jobs remain paused.', flush=True)
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


def menu(args):
    if not sys.stdin.isatty():
        print('No interactive terminal. Specify an action; see --help.', file=sys.stderr)
        return 2
    actions = tuple(ACTIONS)
    while True:
        print('\nSI-Platform Dash Helper')
        for number, action in enumerate(actions, start=1):
            print(f'{number}. {ACTIONS[action]}')
        print('0. Exit')
        choice = input('Select: ').strip()
        if choice == '0':
            return 0
        if choice not in {str(number) for number in range(1, len(actions) + 1)}:
            print('Invalid selection.')
            continue
        result = execute(actions[int(choice) - 1], args, interactive=True)
        print(f'[exit code] {result}')


def main(argv=None):
    args = parse_args(argv)
    try:
        return execute(args.action, args) if args.action else menu(args)
    except (KeyboardInterrupt, EOFError):
        print('\nInterrupted.', file=sys.stderr)
        return 130
    except OSError as error:
        print(f'Command failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())