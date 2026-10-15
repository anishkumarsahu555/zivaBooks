"""Exercise local CLI workflows against a fake Docker executable, never real data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.test import TestCase


class StartScriptTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repo = Path(__file__).resolve().parents[2]
        shutil.copy(repo / 'start.sh', self.root)
        (self.root / '.env.local').write_text('keep-this-file\n')
        (self.root / '.env.local.example').write_text('example\n')
        self.calls = self.root / 'calls.jsonl'
        self.config = {'name': 'local-test', 'services': {}}
        for service, directory in [('db', 'postgres'), ('redis', 'redis'), ('web', 'media')]:
            path = self.root / 'local-data' / 'test-app' / directory
            path.mkdir(parents=True)
            self.config['services'][service] = {
                'volumes': [{'type': 'bind', 'source': str(path)}],
                'environment': {'DEBUG': 'true'},
            }
        self.config_path = self.root / 'config.json'
        binary = self.root / 'bin'
        binary.mkdir()
        docker = binary / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if 'config' in args:
    print(Path(os.environ['TEST_CONFIG']).read_text())
if 'run' in args and os.environ.get('FAIL_MIGRATE'):
    sys.exit(7)
''')
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                        TEST_CALLS=str(self.calls), TEST_CONFIG=str(self.config_path))

    def run_script(self, *args):
        self.config_path.write_text(json.dumps(self.config))
        result = subprocess.run(['bash', str(self.root / 'start.sh'), *args],
                                cwd='/tmp', env=self.env, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        # Retain only actual Compose operations, stripping its global options.
        operations = [call[call.index('-f') + 2:] for call in calls if '-f' in call]
        return result, operations

    def test_default_start_never_migrates_or_builds(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['up', '-d', '--wait', '--no-build', 'web', 'worker'], calls)
        self.assertFalse(any('run' in call or 'build' in call for call in calls))

    def test_migration_failure_leaves_writers_stopped(self):
        self.env['FAIL_MIGRATE'] = '1'
        result, calls = self.run_script('--build', '--migrate')
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(calls.index(['build', 'web']), calls.index(['stop', '--timeout', '-1', 'web', 'worker']))
        self.assertIn(['run', '--rm', '--no-deps', 'migrate'], calls)
        self.assertFalse(any(call[:1] == ['up'] and 'web' in call for call in calls))
        self.assertIn('remain stopped', result.stderr)

    def test_no_worker_stops_an_existing_worker(self):
        result, calls = self.run_script('--no-worker')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['stop', '--timeout', '-1', 'worker'], calls)
        self.assertIn(['up', '-d', '--wait', '--no-build', 'web'], calls)

    def test_production_and_external_storage_are_rejected(self):
        for change in ('debug', 'path'):
            with self.subTest(change=change):
                if change == 'debug':
                    self.config['services']['web']['environment']['DEBUG'] = 'false'
                else:
                    self.config['services']['web']['environment']['DEBUG'] = 'true'
                    self.config['services']['db']['volumes'][0]['source'] = '/srv/production/data'
                result, calls = self.run_script('--down')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any('down' in call for call in calls))

    def test_management_arguments_preserve_spaces_and_shell_literals(self):
        expression = 'print("hello world; $(do-not-run)")'
        result, calls = self.run_script('--manage', 'shell', '-c', expression)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['exec', 'web', 'python', 'manage.py', 'shell', '-c', expression], calls)

    def test_init_preserves_env_and_existing_files(self):
        media = self.root / 'local-data/test-app/media/photo.jpg'
        media.write_bytes(b'original')
        result, calls = self.run_script('--init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / '.env.local').read_text(), 'keep-this-file\n')
        self.assertEqual(media.read_bytes(), b'original')
        self.assertEqual(calls, [['config', '--format', 'json']])

    def test_invalid_options_fail_before_docker(self):
        for args in [('--down', '--build'), ('--unknown',), ('--env-file',), ('--status', '--stop')]:
            with self.subTest(args=args):
                result, calls = self.run_script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
