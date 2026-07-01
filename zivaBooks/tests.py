from django.test import TestCase, override_settings


class HealthCheckTests(TestCase):
    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'health-check-tests',
            }
        }
    )
    def test_health_check_reports_database_and_cache_status(self):
        response = self.client.get('/health/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['checks']['database']['ok'])
        self.assertTrue(payload['checks']['cache']['ok'])
