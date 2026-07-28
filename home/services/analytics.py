from datetime import datetime, timedelta

from django.utils import timezone

from utils.logger import logger
from home.services.reports import formatINR


def money(value):
    return f'Rs. {formatINR(format(float(value or 0.0), ".0f"))}'


def report_date(value, default):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        return default


def today_date():
    try:
        now = timezone.now()
        if timezone.is_aware(now):
            return timezone.localtime(now).date()
        return now.date()
    except Exception:
        return datetime.today().date()


def report_range(request):
    today = today_date()
    start_date = report_date(request.GET.get('startDate'), today)
    end_date = report_date(request.GET.get('endDate'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def rows_with_money(rows):
    return [
        {
            'label': row.get('label') or 'Unassigned',
            'amount': money(row.get('amount') or 0.0),
            'count': row.get('count') or 0,
        }
        for row in rows
    ]


def date_points(start_date, end_date):
    points = []
    current = start_date
    while current <= end_date:
        points.append(current)
        current += timedelta(days=1)
    return points


def pct_change(current, previous):
    current = float(current or 0.0)
    previous = float(previous or 0.0)
    if previous == 0:
        if current == 0:
            return 0.0
        return 100.0
    return ((current - previous) / previous) * 100.0


def group_trend_series(points, series_map, group_by='day'):
    group_by = (group_by or 'day').lower()
    grouped = []
    bucket_index = {}
    for point in points:
        if group_by == 'month':
            key = point.replace(day=1)
            label = point.strftime('%b %Y')
        elif group_by == 'week':
            week_start = point - timedelta(days=point.weekday())
            key = week_start
            week_end = min(week_start + timedelta(days=6), points[-1])
            label = f"{week_start.strftime('%d %b')} - {week_end.strftime('%d %b')}"
        else:
            key = point
            label = point.strftime('%d %b')
        if key not in bucket_index:
            bucket_index[key] = len(grouped)
            grouped.append({
                'label': label,
                'sales': 0.0,
                'collections': 0.0,
                'cash_counter': 0.0,
            })
        bucket = grouped[bucket_index[key]]
        bucket['sales'] += float(series_map['sales'].get(point, 0.0) or 0.0)
        bucket['collections'] += float(series_map['collections'].get(point, 0.0) or 0.0)
        bucket['cash_counter'] += float(series_map['cash_counter'].get(point, 0.0) or 0.0)
    return {
        'labels': [item['label'] for item in grouped],
        'sales': [round(item['sales'], 2) for item in grouped],
        'collections': [round(item['collections'], 2) for item in grouped],
        'cashCounter': [round(item['cash_counter'], 2) for item in grouped],
        'groupBy': group_by,
    }
