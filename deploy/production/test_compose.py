"""Validate both environment templates without starting Docker containers."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from deploy.production import operations

ROOT = Path(__file__).resolve().parents[2]


class ComposeConfigurationTests(unittest.TestCase):
    def config(self, environment):
        template = (ROOT / f'.env.{environment}.example').read_text()
        # Deliberately fake credentials; never load the developer's private .env.
        template += '\nSECRET_KEY=test-only\nPOSTGRES_PASSWORD=test-only\n'
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env') as env:
            env.write(template)
            env.flush()
            result = subprocess.run(
                ['docker', 'compose', '--env-file', env.name, '-f',
                 str(ROOT / 'docker-compose.yml'), '--profile', 'tools', 'config', '--format', 'json'],
                env={k: v for k, v in os.environ.items()
                     if k in ('PATH', 'HOME', 'DOCKER_CONFIG')},
                capture_output=True, text=True, check=True,
            )
        return json.loads(result.stdout)

    def test_local_uses_existing_app_directory_and_http(self):
        config = self.config('local')
        self.assertEqual(config['name'], 'zivabooks')
        web = config['services']['web']
        self.assertEqual(web['environment']['SESSION_COOKIE_SECURE'], 'false')
        self.assertEqual(web['environment']['DEBUG'], 'true')
        self.assertEqual(web['volumes'][0]['source'], str(ROOT / 'local-data/zivabooks/media'))
        self.assertEqual(config['services']['db']['volumes'][0]['source'],
                         str(ROOT / 'local-data/zivabooks/postgres'))

    def test_production_defaults_are_secure_and_storage_is_explicit(self):
        config = self.config('production')
        web = config['services']['web']
        self.assertEqual(web['environment']['DEBUG'], 'false')
        self.assertEqual(web['environment']['SESSION_COOKIE_SECURE'], 'true')
        self.assertEqual(web['environment']['CSRF_TRUSTED_ORIGINS'], 'https://zivainfotech.tech')
        self.assertEqual(web['volumes'][0]['source'], '/srv/zivabooks/data/media')
        self.assertFalse(web['volumes'][0]['bind'].get('create_host_path', False))
        self.assertEqual(web['ports'][0]['host_ip'], '127.0.0.1')
        for service in ('db', 'redis'):
            self.assertFalse(config['services'][service].get('ports'))
        self.assertNotIn('migrate', web['command'])
        self.assertEqual(config['services']['migrate']['profiles'], ['tools'])
        self.assertEqual(web['image'], config['services']['worker']['image'])

    def test_backup_helper_uses_shared_compose(self):
        config = self.config('production')
        with patch.object(operations, 'run', return_value=Mock(stdout=json.dumps(config))):
            ops = operations.Operations('/tmp/example.env')
        self.assertEqual(ops.compose[-1], str(ROOT / 'docker-compose.yml'))
