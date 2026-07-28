from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

from utils.logger import logger


def health_check(request):
    checks = {
        'database': _check_database(),
        'cache': _check_cache(),
    }
    is_healthy = all(item['ok'] for item in checks.values())
    if is_healthy:
        logger.info("[HealthCheck] System health check succeeded.")
    else:
        logger.error(f"[HealthCheck] System health check failed: {checks}")
    return JsonResponse(
        {
            'status': 'ok' if is_healthy else 'error',
            'checks': checks,
        },
        status=200 if is_healthy else 503,
    )


def _check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:
        return {'ok': False, 'error': exc.__class__.__name__}
    return {'ok': True}


def _check_cache():
    key = 'zivabooks:health'
    try:
        cache.set(key, 'ok', timeout=10)
        if cache.get(key) != 'ok':
            return {'ok': False, 'error': 'CacheRoundTripFailed'}
    except Exception as exc:
        return {'ok': False, 'error': exc.__class__.__name__}
    return {'ok': True}
