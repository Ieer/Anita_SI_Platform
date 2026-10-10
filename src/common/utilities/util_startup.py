import json
import os
import tempfile
from pathlib import Path

from common.utilities.util_process_lock import ProcessFileLock


STARTUP_MODES = ('web_only', 'paired_running', 'paired_paused')
DEFAULT_STARTUP_MODE = 'paired_running'
STARTUP_CONFIG_SCHEMA = 1


class StartupConfigError(ValueError):
    pass


def startup_config_path():
    project_root = Path(__file__).resolve().parents[3]
    return project_root / 'user_data' / 'cache' / 'startup.json'


def _validate_config(config):
    if not isinstance(config, dict) or set(config) != {'schema', 'revision', 'scheduler_mode'}:
        raise StartupConfigError('Startup configuration has an invalid shape')
    if config['schema'] != STARTUP_CONFIG_SCHEMA:
        raise StartupConfigError('Unsupported startup configuration schema')
    if isinstance(config['revision'], bool) or not isinstance(config['revision'], int) or config['revision'] < 0:
        raise StartupConfigError('Startup configuration revision must be a non-negative integer')
    if config['scheduler_mode'] not in STARTUP_MODES:
        raise StartupConfigError('Unsupported scheduler startup mode')
    return config


def load_startup_config(path=None):
    config_path = Path(path) if path is not None else startup_config_path()
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {
            'schema': STARTUP_CONFIG_SCHEMA,
            'revision': 0,
            'scheduler_mode': DEFAULT_STARTUP_MODE,
        }
    except (OSError, json.JSONDecodeError) as error:
        raise StartupConfigError('Unable to read startup configuration') from error
    return _validate_config(config)


def save_scheduler_mode(mode, expected_revision, path=None):
    if mode not in STARTUP_MODES:
        raise StartupConfigError('Unsupported scheduler startup mode')
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise StartupConfigError('Expected revision must be a non-negative integer')

    config_path = Path(path) if path is not None else startup_config_path()
    lock_path = config_path.with_suffix(config_path.suffix + '.lock')
    try:
        lock = ProcessFileLock(lock_path)
        lock.acquire()
    except RuntimeError as error:
        raise StartupConfigError('Another startup configuration update is in progress') from error

    try:
        current = load_startup_config(config_path)
        if current['revision'] != expected_revision:
            raise StartupConfigError('Startup configuration changed; reload before saving')
        updated = {
            'schema': STARTUP_CONFIG_SCHEMA,
            'revision': current['revision'] + 1,
            'scheduler_mode': mode,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f'.{config_path.name}.', suffix='.tmp', dir=config_path.parent,
        )
        try:
            with os.fdopen(file_descriptor, 'w', encoding='utf-8', newline='\n') as temporary_file:
                json.dump(updated, temporary_file, ensure_ascii=False, sort_keys=True)
                temporary_file.write('\n')
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, config_path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return updated
    finally:
        lock.release()