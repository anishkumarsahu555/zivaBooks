"""Validate Docker Compose configuration without starting containers."""
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
    def get_compose_config(self, env_content=None):
        cmd = ['docker', 'compose', '-f', str(ROOT / 'docker-compose.yml'), '--profile', 'tools', 'config', '--format', 'json']
        env_dict = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'DOCKER_CONFIG')}
        if env_content:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.env') as env:
                env.write(env_content)
                env.flush()
                result = subprocess.run(
                    ['docker', 'compose', '--env-file', env.name, '-f', str(ROOT / 'docker-compose.yml'),
                     '--profile', 'tools', 'config', '--format', 'json'],
                    env=env_dict,
                    capture_output=True, text=True, check=True,
                )
        else:
            result = subprocess.run(cmd, env=env_dict, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_default_compose_loads_all_services_and_volumes(self):
        config = self.get_compose_config()
        self.assertEqual(config['name'], 'zivabooks')
        services = config['services']
        for expected in ('web', 'worker', 'beat', 'postgres', 'redis', 'migrate'):
            self.assertIn(expected, services)

        web = services['web']
        self.assertIn('postgres', web['depends_on'])
        self.assertIn('redis', web['depends_on'])
        self.assertEqual(web['ports'][0]['target'], 8000)
        self.assertTrue(any(v['target'] == '/app/media' for v in web['volumes']))

        self.assertIn('postgres_data', config['volumes'])
        self.assertIn('redis_data', config['volumes'])
        self.assertIn('media_data', config['volumes'])

    def test_custom_env_overrides(self):
        custom_env = (
            "PORT=8080\n"
            "DEBUG=true\n"
            "POSTGRES_DB=custom_db\n"
            "POSTGRES_USER=custom_user\n"
            "POSTGRES_PASSWORD=custom_pass\n"
            "SECRET_KEY=custom-secret-key\n"
        )
        config = self.get_compose_config(custom_env)
        web = config['services']['web']
        self.assertEqual(web['environment']['DEBUG'], 'true')
        self.assertEqual(web['environment']['POSTGRES_DB'], 'custom_db')
        self.assertEqual(web['environment']['POSTGRES_USER'], 'custom_user')
        self.assertEqual(web['environment']['POSTGRES_PASSWORD'], 'custom_pass')
        self.assertEqual(web['environment']['SECRET_KEY'], 'custom-secret-key')
        self.assertEqual(web['ports'][0]['published'], '8080')

    def test_backup_helper_uses_shared_compose(self):
        dummy_config = {
            "x-backup": {"root": "/backups/zivabooks"},
            "services": {
                "web": {"volumes": [{"type": "volume", "source": "media_data", "target": "/app/media"}]}
            }
        }
        with patch.object(operations, 'run', return_value=Mock(stdout=json.dumps(dummy_config))):
            ops = operations.Operations('/tmp/example.env')
        self.assertEqual(ops.compose[-1], str(ROOT / 'docker-compose.yml'))
