import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

util_startup = importlib.import_module('common.utilities.util_startup')
anita_go_config = importlib.import_module('config.anita_go_conf')
DEFAULT_STARTUP_MODE = util_startup.DEFAULT_STARTUP_MODE
StartupConfigError = util_startup.StartupConfigError
load_startup_config = util_startup.load_startup_config
save_scheduler_mode = util_startup.save_scheduler_mode
startup_config_path = util_startup.startup_config_path
start_ap = importlib.import_module('start_ap')
parse_args = start_ap.parse_args
resolve_scheduler_mode = start_ap.resolve_scheduler_mode


class StartupConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'startup.json'

    def tearDown(self):
        self.folder.cleanup()

    def test_config_path_prefers_new_name_and_falls_back_to_legacy_name(self):
        config_dir = Path(self.folder.name)
        legacy_config = config_dir / 'dashgo.ini'
        current_config = config_dir / 'anita_go.ini'

        legacy_config.touch()
        self.assertEqual(anita_go_config.resolve_config_file_path(config_dir), legacy_config)

        current_config.touch()
        self.assertEqual(anita_go_config.resolve_config_file_path(config_dir), current_config)

        current_config.unlink()
        legacy_config.unlink()
        self.assertEqual(anita_go_config.resolve_config_file_path(config_dir), current_config)

    def test_missing_file_uses_default_without_creating_it(self):
        self.assertEqual(load_startup_config(self.path), {
            'schema': 1,
            'revision': 0,
            'scheduler_mode': DEFAULT_STARTUP_MODE,
            'keep_awake': False,
        })
        self.assertFalse(self.path.exists())

    def test_legacy_config_defaults_keep_awake_to_false(self):
        self.path.write_text(json.dumps({
            'schema': 1,
            'revision': 3,
            'scheduler_mode': 'web_only',
        }), encoding='utf-8')
        self.assertEqual(load_startup_config(self.path)['keep_awake'], False)

    def test_default_path_uses_repository_user_data_cache(self):
        self.assertEqual(startup_config_path(), ROOT / 'user_data' / 'cache' / 'startup.json')

    def test_database_initialization_preserves_existing_admin(self):
        peewee = importlib.import_module('peewee')
        conn = importlib.import_module('database.sql_db.conn')
        user_models = importlib.import_module('database.sql_db.entity.table_user')
        models = [user_models.SysUser, user_models.SysRole, user_models.SysUserRole]
        database = peewee.SqliteDatabase(Path(self.folder.name) / 'init.db')
        with database.bind_ctx(models), patch.object(conn, 'db', return_value=database):
            database.create_tables(models)
            conn.init_rds_data()
            user_models.SysUser.update(password_sha256='preserved-hash', user_status=False).where(
                user_models.SysUser.user_name == 'admin').execute()
            user_models.SysRole.update(role_status=False).where(
                user_models.SysRole.role_name == 'admin').execute()
            conn.init_rds_data()
            admin = user_models.SysUser.get_by_id('admin')
            self.assertEqual(admin.password_sha256, 'preserved-hash')
            self.assertFalse(admin.user_status)
            self.assertFalse(user_models.SysRole.get_by_id('admin').role_status)
            self.assertEqual(user_models.SysUser.select().count(), 1)
            self.assertEqual(user_models.SysRole.select().count(), 1)
            self.assertEqual(user_models.SysUserRole.select().count(), 1)
        database.close()

    def test_save_increments_revision_and_rejects_stale_writer(self):
        saved = save_scheduler_mode('paired_paused', 0, self.path)
        self.assertEqual(saved['revision'], 1)
        self.assertFalse(saved['keep_awake'])
        self.assertEqual(load_startup_config(self.path), saved)
        with self.assertRaisesRegex(StartupConfigError, 'changed'):
            save_scheduler_mode('web_only', 0, self.path)

    def test_corrupt_or_extended_config_is_not_silently_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(StartupConfigError):
            load_startup_config(self.path)
        self.path.write_text(json.dumps({
            'schema': 1,
            'revision': 0,
            'scheduler_mode': 'web_only',
            'secret': 'must-not-be-stored',
        }), encoding='utf-8')
        with self.assertRaises(StartupConfigError):
            save_scheduler_mode('web_only', 0, self.path)

    def test_invalid_mode_and_revision_are_rejected(self):
        with self.assertRaises(StartupConfigError):
            save_scheduler_mode('shell_command', 0, self.path)
        with self.assertRaises(StartupConfigError):
            save_scheduler_mode('web_only', True, self.path)
        with self.assertRaises(StartupConfigError):
            save_scheduler_mode('web_only', 0, self.path, keep_awake='yes')

    def test_cli_mode_overrides_saved_policy_and_init_is_opt_in(self):
        saved = save_scheduler_mode('paired_paused', 0, self.path)
        self.assertEqual(resolve_scheduler_mode(None, self.path), saved['scheduler_mode'])
        self.assertEqual(resolve_scheduler_mode('web_only', self.path), 'web_only')
        args = parse_args(['--port', '8098', '--scheduler-mode', 'web_only'])
        self.assertEqual(args.port, 8098)
        self.assertEqual(args.scheduler_mode, 'web_only')
        self.assertFalse(args.init_db)

    def test_web_only_starts_only_web_process(self):
        args = parse_args(['--scheduler-mode', 'web_only'])
        web_process = Mock(pid=1234, poll=Mock(return_value=None))
        with patch.object(start_ap, '_spawn', return_value=web_process) as spawn, \
                patch.object(start_ap, '_wait_for_web'), \
                patch.object(start_ap, '_supervise', side_effect=KeyboardInterrupt), \
                patch.object(start_ap, '_stop_web') as stop_web, \
                patch.object(start_ap, '_stop_scheduler') as stop_scheduler, \
                self.assertRaises(KeyboardInterrupt):
            start_ap.run_services(args, 'web_only')
        spawn.assert_called_once()
        self.assertIn('app.py', spawn.call_args.args[0][1])
        stop_web.assert_called_once_with(web_process)
        stop_scheduler.assert_not_called()

    def test_paired_modes_spawn_scheduler_before_web(self):
        args = parse_args(['--scheduler-mode', 'paired_paused'])
        scheduler_process = Mock(pid=1234, poll=Mock(return_value=None))
        web_process = Mock(pid=5678, poll=Mock(return_value=None))
        with patch('common.utilities.util_scheduler_rpc.SchedulerRPCAuthenticator'), \
                patch.object(start_ap, '_check_scheduler_conflict'), \
                patch.object(start_ap, '_spawn', side_effect=[scheduler_process, web_process]) as spawn, \
                patch.object(start_ap, '_wait_for_scheduler') as wait_scheduler, \
                patch.object(start_ap, '_wait_for_web'), \
                patch.object(start_ap, '_supervise', side_effect=KeyboardInterrupt), \
                patch.object(start_ap, '_stop_web'), \
                patch.object(start_ap, '_stop_scheduler'), \
                self.assertRaises(KeyboardInterrupt):
            start_ap.run_services(args, 'paired_paused')
        self.assertEqual(spawn.call_count, 2)
        self.assertIn('app_apscheduler.py', spawn.call_args_list[0].args[0][1])
        self.assertIn('--start-paused', spawn.call_args_list[0].args[0])
        self.assertIn('app.py', spawn.call_args_list[1].args[0][1])
        wait_scheduler.assert_called_once_with(scheduler_process, expected_state='paused', timeout=30)

    def test_missing_rpc_secret_prevents_child_process_creation(self):
        args = parse_args([])
        with patch('common.utilities.util_scheduler_rpc.SchedulerRPCAuthenticator',
                   side_effect=RuntimeError('secret missing')), \
                patch.object(start_ap, '_spawn') as spawn, \
                self.assertRaisesRegex(RuntimeError, 'secret missing'):
            start_ap.run_services(args, 'paired_running')
        spawn.assert_not_called()

    def test_paused_scheduler_entry_reaches_rpc_without_resuming(self):
        scheduler_module = importlib.import_module('app_apscheduler')
        scheduler = Mock()
        scheduler.get_jobs.return_value = []
        with patch.object(scheduler_module, 'SchedulerRPCAuthenticator'), \
                patch('database.sql_db.repositories.listen_task_repository.ensure_listen_checkpoint_table'), \
                patch.object(scheduler_module, 'SQLAlchemyJobStore'), \
                patch.object(scheduler_module, '_task_center_schema_version', return_value=2), \
                patch.object(scheduler_module, 'recover_stale_runs'), \
                patch('database.sql_db.repositories.task_center_repository.recover_runs'), \
                patch.object(scheduler_module, 'BackgroundScheduler', return_value=scheduler), \
                patch.object(scheduler_module, 'ThreadedServer', side_effect=RuntimeError('rpc probe')), \
                patch.dict(scheduler_module.__dict__, {'scheduler': scheduler}), \
                self.assertRaisesRegex(RuntimeError, 'rpc probe'):
            scheduler_module.run_scheduler_service(start_paused=True)
        scheduler.start.assert_called_once_with(paused=True)
        scheduler.resume.assert_not_called()
        scheduler.shutdown.assert_called_once_with(wait=True)

    def test_unverified_scheduler_port_is_never_taken_over(self):
        with patch.object(start_ap, '_scheduler_port_is_open', return_value=True), \
                patch.object(start_ap, '_connect_scheduler_status', side_effect=PermissionError('auth failed')), \
                self.assertRaisesRegex(RuntimeError, '不会接管'):
            start_ap._check_scheduler_conflict()


class DashHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('dash_helper', ROOT / 'tools' / 'dash_helper.py')
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)

    def test_web_command_overrides_saved_scheduler_policy(self):
        args = self.helper.parse_args(['web', '--port', '8100'])
        command = self.helper.build_command('web', args)
        self.assertEqual(command[1], str(ROOT / 'scripts' / 'start_ap.py'))
        self.assertEqual(command[2:], [
            '--scheduler-mode', 'web_only', '--mode', 'prod', '--host', '127.0.0.1', '--port', '8100',
        ])

    def test_paused_start_uses_private_ephemeral_secret(self):
        with patch.object(self.helper.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
                patch.dict(self.helper.os.environ, {'SMART_AGENT_SCHEDULER_RPC_SECRET': ''}):
            self.assertEqual(self.helper.main(['paired-paused']), 0)
            command = run.call_args.args[0]
            self.assertIn('paired_paused', command)
            self.assertNotIn('paired_running', command)
            secret = run.call_args.kwargs['env']['SMART_AGENT_SCHEDULER_RPC_SECRET']
            self.assertGreaterEqual(len(secret), 32)
            self.assertEqual(self.helper.os.environ['SMART_AGENT_SCHEDULER_RPC_SECRET'], '')

    def test_existing_rpc_secret_is_preserved(self):
        with patch.object(self.helper.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
                patch.dict(self.helper.os.environ, {'SMART_AGENT_SCHEDULER_RPC_SECRET': 'existing-value'}):
            self.helper.main(['paired-paused'])
        self.assertEqual(run.call_args.kwargs['env']['SMART_AGENT_SCHEDULER_RPC_SECRET'], 'existing-value')

    def test_dry_run_never_spawns_any_action(self):
        with patch.object(self.helper.subprocess, 'run') as run:
            for action in self.helper.ACTIONS:
                with self.subTest(action=action):
                    self.assertEqual(self.helper.main([action, '--dry-run']), 0)
        run.assert_not_called()

    def test_database_write_requires_explicit_consent(self):
        with patch.object(self.helper.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(self.helper.main(['init-db']), 2)
            run.assert_not_called()
            self.assertEqual(self.helper.main(['init-db', '--yes']), 0)
        self.assertEqual(run.call_args.args[0][1:], [str(ROOT / 'scripts' / 'start_ap.py'), '--only-init'])

    def test_menu_database_confirmation_can_cancel(self):
        args = self.helper.parse_args([])
        with patch('builtins.input', return_value='no'), patch.object(self.helper.subprocess, 'run') as run:
            self.assertEqual(self.helper.execute('init-db', args, interactive=True), 2)
        run.assert_not_called()

    def test_commands_use_absolute_paths_and_preserve_exit_status(self):
        expected = {
            'demo': [str(ROOT / 'scripts' / 'demo_task_center.py'), '--port', '8097'],
            'check-pages': [str(ROOT / 'scripts' / 'check_pages.py')],
            'test': ['-m', 'pytest', str(ROOT / 'tests'), '-q'],
            'check-deps': ['-m', 'pip', 'check'],
        }
        for action, suffix in expected.items():
            with self.subTest(action=action), \
                    patch.object(self.helper.subprocess, 'run', return_value=Mock(returncode=7)) as run:
                self.assertEqual(self.helper.main([action]), 7)
                self.assertEqual(run.call_args.args[0][1:], suffix)
                self.assertEqual(run.call_args.kwargs['cwd'], ROOT)
                self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_invalid_ports_are_rejected_before_spawn(self):
        with patch.object(self.helper.subprocess, 'run') as run:
            for port in ('0', '65536', 'invalid'):
                with self.subTest(port=port), self.assertRaises(SystemExit):
                    self.helper.main(['web', '--port', port])
        run.assert_not_called()

    def test_noninteractive_menu_does_not_wait_for_input(self):
        with patch.object(self.helper.sys.stdin, 'isatty', return_value=False), \
                patch('builtins.input') as prompt:
            self.assertEqual(self.helper.main([]), 2)
        prompt.assert_not_called()


if __name__ == '__main__':
    unittest.main()