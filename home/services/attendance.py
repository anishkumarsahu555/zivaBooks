import requests
from calendar import monthrange
from collections import defaultdict
from datetime import datetime

from home.models import Attendance
from home.tenancy import get_current_tenant, tenant_qs

DEFAULT_AVATAR_URL = "/static/images/default-avatar.svg"
OPENSTREETMAP_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OPENSTREETMAP_USER_AGENT = "zivaBooks/attendance-location"


def image_url(image_field, variation=None):
    if not image_field:
        return DEFAULT_AVATAR_URL
    if variation:
        candidate = getattr(image_field, variation, None)
        if candidate and getattr(candidate, 'url', None):
            return candidate.url
    return getattr(image_field, 'url', '') or DEFAULT_AVATAR_URL


def attendance_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Attendance).select_related('staffID').only(
        'id', 'datetime', 'isLogIn', 'isLogOut', 'loginDateTime', 'logoutDateTime',
        'login_remark', 'logout_remark', 'login_location', 'logout_location', 'loginPhoto', 'logoutPhoto', 'staffID__name'
    )


def tenant_attendance_location_enabled(request_or_user):
    tenant = get_current_tenant(request_or_user)
    return bool(getattr(tenant, 'enableAttendanceLocationMaps', False))


def reverse_geocode_openstreetmap(lat, lng):
    lat = str(lat).strip()
    lng = str(lng).strip()
    if not lat or not lng:
        return ""
    try:
        response = requests.get(
            OPENSTREETMAP_REVERSE_URL,
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lng,
                "zoom": 18,
                "addressdetails": 1,
            },
            headers={
                "User-Agent": OPENSTREETMAP_USER_AGENT,
            },
            timeout=4,
        )
        if response.status_code != 200:
            return f"{lat}, {lng}"
        payload = response.json()
        return payload.get("display_name") or f"{lat}, {lng}"
    except Exception:
        return f"{lat}, {lng}"


def deduct_location_balance(lat, lng):
    return reverse_geocode_openstreetmap(lat, lng)


def attendance_month_reference(request):
    raw_date = request.GET.get('cDate') or request.GET.get('monthDate')
    if raw_date:
        try:
            return datetime.strptime(raw_date, '%d/%m/%Y').date()
        except Exception:
            pass
    return datetime.today().date()


def attendance_month_bounds(reference_date):
    month_start = reference_date.replace(day=1)
    month_end = reference_date.replace(day=monthrange(reference_date.year, reference_date.month)[1])
    return month_start, month_end


def attendance_monthly_rows(request, staff_queryset, month_start, month_end):
    today = datetime.today().date()
    day_numbers = list(range(1, month_end.day + 1))
    attendance_qs = tenant_qs(request, Attendance).filter(
        isDeleted=False,
        staffID__in=staff_queryset,
        datetime__date__range=[month_start, month_end],
    ).select_related('staffID').order_by('staffID__name', 'datetime')
    attendance_map = defaultdict(set)
    for record in attendance_qs:
        day = None
        if getattr(record, 'loginDateTime', None):
            day = record.loginDateTime.date()
        elif getattr(record, 'datetime', None):
            day = record.datetime.date()
        if day:
            attendance_map[record.staffID_id].add(day)

    rows = []
    for staff in staff_queryset:
        present_days = attendance_map.get(staff.id, set())
        statuses = []
        present_count = 0
        absent_count = 0
        holiday_count = 0
        for day_number in day_numbers:
            current_day = month_start.replace(day=day_number)
            if current_day in present_days:
                status = 'P'
                present_count += 1
            elif current_day > today:
                status = '-'
            elif current_day.weekday() == 6:
                status = 'H'
                holiday_count += 1
            else:
                status = 'A'
                absent_count += 1
            statuses.append(status)
        rows.append({
            'staff': staff,
            'statuses': statuses,
            'present_count': present_count,
            'absent_count': absent_count,
            'holiday_count': holiday_count,
        })
    return day_numbers, rows
