"""Exercise generated deployments using the real Compose parser, without a daemon."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'docker_generator.sh'


class DockerGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'project with spaces'
        self.env = {k: v for k, v in os.environ.items()
                    if k in ('PATH', 'HOME', 'DOCKER_CONFIG', 'TMPDIR')}

    def generate(self, *options, output=None):
        return subprocess.run(
            ['bash', str(SCRIPT), '--project', 'test-shop', '--output',
             str(output or self.output), '--host-port', '8101', *options],
            capture_output=True, text=True, env=self.env,
        )

    def config(self, output):
        result = subprocess.run(
            ['docker', 'compose', '--env-file', str(output / '.env'),
             '-f', str(output / 'compose.yaml'), 'config', '--format', 'json'],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_all_database_and_redis_combinations(self):
        for database in ('postgres', 'mysql', 'none'):
            for redis in (False, True):
                with self.subTest(database=database, redis=redis):
                    output = self.output / f'{database}-{redis}'
                    options = ['--image', 'example/app:1', '--database', database]
                    if redis:
                        options.append('--redis')
                    result = self.generate(*options, output=output)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    config = self.config(output)
                    services = config['services']
                    expected = {'web'} | ({database} if database != 'none' else set())
                    if redis:
                        expected.add('redis')
                    self.assertEqual(set(services), expected)
                    self.assertEqual(config['name'], 'test-shop')
                    self.assertEqual(config['networks']['default']['name'], 'test-shop_default')
                    web = services['web']
                    self.assertEqual(web['ports'][0]['host_ip'], '127.0.0.1')
                    self.assertEqual(web['ports'][0]['published'], '8101')
                    self.assertEqual(web['ports'][0]['target'], 8000)
                    self.assertNotIn('MYSQL_ROOT_PASSWORD', web['environment'])
                    for name, service in services.items():
                        self.assertNotIn('container_name', service)
                        if name != 'web':
                            self.assertNotIn('ports', service)
                            self.assertEqual(web['depends_on'][name]['condition'], 'service_healthy')
                    for volume in config.get('volumes', {}).values():
                        self.assertTrue(volume['name'].startswith('test-shop_'))
                    if database != 'none':
                        password = web['environment']['DB_PASSWORD']
                        self.assertRegex(password, r'^[a-f0-9]{64}$')
                        self.assertIn(password, web['environment']['DATABASE_URL'])
                    if redis:
                        password = services['redis']['environment']['REDIS_PASSWORD']
                        self.assertIn(password, web['environment']['REDIS_URL'])
                    for name in ('.env', 'app.env'):
                        self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)
                    for name in ('.gitignore', '.dockerignore'):
                        self.assertIn('app.env', (output / name).read_text())

    def test_build_existing_dockerfile_and_preserve_ignore_rules(self):
        self.output.mkdir(parents=True)
        (self.output / 'Dockerfile').write_text('FROM scratch\n')
        (self.output / '.dockerignore').write_text('node_modules\n')
        result = self.generate('--database', 'none', '--app-port', '3000')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.config(self.output)
        self.assertEqual(config['services']['web']['build']['context'], str(self.output))
        self.assertEqual(config['services']['web']['ports'][0]['target'], 3000)
        self.assertTrue((self.output / '.dockerignore').read_text().startswith('node_modules\n'))

    def test_existing_files_are_not_modified(self):
        for filename in ('.env', 'compose.yml', 'docker-compose.yaml', 'app.env'):
            with self.subTest(filename=filename):
                output = self.output / filename.replace('.', '_')
                output.mkdir(parents=True)
                existing = output / filename
                existing.write_text('keep me\n')
                result = self.generate('--image', 'example/app:1', output=output)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(existing.read_text(), 'keep me\n')
                self.assertEqual(list(output.iterdir()), [existing])

    def test_rerun_preserves_credentials(self):
        result = self.generate('--image', 'example/app:1')
        self.assertEqual(result.returncode, 0, result.stderr)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.assertNotEqual(self.generate('--image', 'example/app:1').returncode, 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_invalid_input_and_missing_build_fail_before_generation(self):
        for options in ([], ['--image', 'x', '--database', 'sqlite'],
                        ['--image', 'x', '--project', '../bad'],
                        ['--image', 'x', '--host-port', '65536'],
                        ['--image', 'x', '--app-port', '080'],
                        ['--image', 'x\nmalicious'], ['--redis', '--database']):
            with self.subTest(options=options):
                result = self.generate(*options)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_ignore_symlink_is_rejected(self):
        self.output.mkdir(parents=True)
        target = Path(self.tmp.name) / 'untouched'
        target.write_text('keep me')
        (self.output / '.dockerignore').symlink_to(target)
        result = self.generate('--image', 'example/app:1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), 'keep me')
        self.assertFalse((self.output / '.env').exists())

    def test_projects_have_separate_resources_and_credentials(self):
        configs = []
        for name in ('shop-one', 'shop-two'):
            output = self.output / name
            result = self.generate('--image', 'example/app:1', '--project', name,
                                   '--redis', output=output)
            self.assertEqual(result.returncode, 0, result.stderr)
            configs.append(self.config(output))
        self.assertNotEqual(configs[0]['networks'], configs[1]['networks'])
        first_volumes = {v['name'] for v in configs[0]['volumes'].values()}
        second_volumes = {v['name'] for v in configs[1]['volumes'].values()}
        self.assertFalse(first_volumes & second_volumes)
        self.assertNotEqual(configs[0]['services']['web']['environment']['DB_PASSWORD'],
                            configs[1]['services']['web']['environment']['DB_PASSWORD'])
