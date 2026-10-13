"""
zivaBooks Stress Test Suite (Locust)
Simulates realistic concurrent user traffic across critical application endpoints:
1. Landing / Login Page (HTML render + Static assets / WhiteNoise)
2. Health Check (PostgreSQL + Redis connectivity checks)
3. License Plans API (PostgreSQL query + JSON serialization)
4. PWA Web Manifest (Static JSON endpoint)
"""

from locust import HttpUser, task, between


class ZivaBooksUser(HttpUser):
    # Simulate realistic user think time between actions (100ms - 500ms)
    wait_time = between(0.1, 0.5)

    @task(4)
    def test_home_page(self):
        """Simulate loading the main login / landing page."""
        with self.client.get("/", catch_response=True, name="GET / (Landing/Login)") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Unexpected status code: {response.status_code}")

    @task(3)
    def test_health_check(self):
        """Simulate frequent system health pings (exercises DB & Redis)."""
        with self.client.get("/health/", catch_response=True, name="GET /health/ (DB & Cache)") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed with status: {response.status_code}")

    @task(3)
    def test_license_plans_api(self):
        """Simulate dynamic API query to PostgreSQL database."""
        with self.client.get("/api/license-plans/", catch_response=True, name="GET /api/license-plans/ (DB API)") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"License plans API failed with status: {response.status_code}")

    @task(1)
    def test_manifest_json(self):
        """Simulate fetching PWA manifest static asset."""
        with self.client.get("/manifest.json", catch_response=True, name="GET /manifest.json (Static PWA)") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Manifest fetch failed with status: {response.status_code}")
