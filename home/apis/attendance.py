import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from utils.logger import logger
from home.models import *
from home.services.attendance import (
    attendance_list_base_qs,
    deduct_location_balance,
    image_url,
    tenant_attendance_location_enabled,
)
from home.tenancy import assign_tenant, get_staff_user_for_request, tenant_qs


def get_attendance_dashboard_report_api(request):
    today = datetime.today().date()
    monthly_start = today.replace(day=1)
    today_record = tenant_qs(request, Attendance).filter(
        isDeleted=False,
        staffID__user_ID_id=request.user.pk,
        datetime__date=today,
    ).order_by('-datetime').first()
    monthly_qs = tenant_qs(request, Attendance).filter(
        isDeleted=False,
        staffID__user_ID_id=request.user.pk,
        datetime__date__range=[monthly_start, today],
    )
    if today_record:
        if getattr(today_record, 'isLogOut', False):
            today_status = 'Logged Out'
        elif getattr(today_record, 'isLogIn', False):
            today_status = 'Logged In'
        else:
            today_status = 'Pending'
        login_time = today_record.loginDateTime.strftime('%I:%M %p') if today_record.loginDateTime else '--'
        logout_time = today_record.logoutDateTime.strftime('%I:%M %p') if today_record.logoutDateTime else '--'
    else:
        today_status = 'Not Marked'
        login_time = '--'
        logout_time = '--'
    data = {
        'attendance_today_status': today_status,
        'attendance_login_time': login_time,
        'attendance_logout_time': logout_time,
        'attendance_monthly_count': monthly_qs.count(),
    }
    return JsonResponse({'data': data}, safe=False)


@transaction.atomic
def add_attendance_api(request):
    if request.method == 'POST':
        try:
            remark = request.POST.get("remark")
            stat = request.POST.get("stat")
            lat = (request.POST.get("lat") or "").strip()
            lng = (request.POST.get("lng") or "").strip()
            photo = request.FILES.get("photo")
            if not lat or not lng or lat == '0.0' or lng == '0.0':
                return JsonResponse({
                    'message': 'error',
                    'error': 'Live location is required to submit attendance.'
                }, safe=False)
            obj = tenant_qs(request, Attendance).get(datetime__date=datetime.today().date(),
                                         staffID__user_ID__id=request.user.pk)
            if stat == 'Login':
                obj.isLogIn = True
                obj.login_remark = remark
                obj.login_latitude = lat
                obj.login_longitude = lng
                obj.loginDateTime = timezone.now()
                if photo:
                    obj.loginPhoto = photo
                if tenant_attendance_location_enabled(request):
                    try:
                        obj.login_location = deduct_location_balance(lat, lng)
                    except:
                        pass
                else:
                    obj.login_location = None
            if stat == 'Logout':
                obj.isLogOut = True
                obj.logout_remark = remark
                obj.logout_latitude = lat
                obj.logout_longitude = lng
                obj.logoutDateTime = timezone.now()
                if photo:
                    obj.logoutPhoto = photo
                if tenant_attendance_location_enabled(request):
                    try:
                        obj.logout_location = deduct_location_balance(lat, lng)
                    except:
                        pass
                else:
                    obj.logout_location = None
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def _staff_location_can_view_all(request):
    return (
        request.user.has_perm('home.can_view_staff_locations') or
        request.user.has_perm('home.can_view_all_attendance') or
        request.user.has_perm('home.can_view_attendance_reports') or
        request.user.has_perm('home.can_manage_staff') or
        request.user.has_perm('home.can_view_party_credentials')
    )


def _staff_location_staff_for_request(request):
    staff = get_staff_user_for_request(request)
    if staff is None:
        raise PermissionError('Staff profile is required for location tracking.')
    return staff


def _staff_location_decimal(value, label, min_value, max_value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{label} is invalid.')
    if parsed < Decimal(str(min_value)) or parsed > Decimal(str(max_value)):
        raise ValueError(f'{label} is out of range.')
    return parsed


def _staff_location_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _staff_location_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _staff_location_recorded_at(value):
    parsed = parse_datetime(str(value)) if value else None
    if parsed is None:
        return timezone.now()
    if settings.USE_TZ and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if not settings.USE_TZ and timezone.is_aware(parsed):
        parsed = timezone.make_naive(parsed, timezone.get_current_timezone())
    return parsed


def _staff_location_payload(request):
    if request.content_type and 'application/json' in request.content_type:
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return {}
    return request.POST


def _save_staff_location_sample(request, staff, sample):
    lat = _staff_location_decimal(sample.get('latitude') or sample.get('lat'), 'Latitude', -90, 90)
    lng = _staff_location_decimal(sample.get('longitude') or sample.get('lng'), 'Longitude', -180, 180)
    client_sample_id = (sample.get('client_sample_id') or sample.get('clientSampleId') or '').strip()[:80] or None
    if client_sample_id:
        existing = tenant_qs(request, StaffLocationLog).filter(
            staff=staff,
            client_sample_id=client_sample_id,
            isDeleted=False,
        ).first()
        if existing:
            return existing, False
    obj = StaffLocationLog(
        staff=staff,
        latitude=lat,
        longitude=lng,
        accuracy=_staff_location_float(sample.get('accuracy')),
        speed=_staff_location_float(sample.get('speed')),
        heading=_staff_location_float(sample.get('heading')),
        battery_percent=_staff_location_float(sample.get('battery_percent') or sample.get('batteryPercent')),
        is_mock_location=_staff_location_bool(sample.get('is_mock_location') or sample.get('isMockLocation')),
        source=(sample.get('source') or 'pwa')[:30],
        client_sample_id=client_sample_id,
        recorded_at=_staff_location_recorded_at(sample.get('recorded_at') or sample.get('recordedAt')),
    )
    assign_tenant(obj, request)
    obj.save()
    return obj, True


@transaction.atomic
def add_staff_location_ping_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'POST is required.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    try:
        staff = _staff_location_staff_for_request(request)
        obj, created = _save_staff_location_sample(request, staff, _staff_location_payload(request))
        return JsonResponse({
            'message': 'success',
            'created': created,
            'id': obj.pk,
            'recorded_at': obj.recorded_at.isoformat(),
        })
    except PermissionError as exc:
        return JsonResponse({'message': 'forbidden', 'error': str(exc)}, status=403)
    except ValueError as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


@transaction.atomic
def bulk_staff_location_sync_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'POST is required.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    try:
        staff = _staff_location_staff_for_request(request)
        payload = _staff_location_payload(request)
        samples = payload.get('locations') or payload.get('samples') or []
        if not isinstance(samples, list):
            return JsonResponse({'message': 'error', 'error': 'locations must be a list.'}, status=400)
        created = 0
        skipped = 0
        errors = []
        for index, sample in enumerate(samples[:500]):
            try:
                _, was_created = _save_staff_location_sample(request, staff, sample or {})
                created += 1 if was_created else 0
                skipped += 0 if was_created else 1
            except ValueError as exc:
                errors.append({'index': index, 'error': str(exc)})
        return JsonResponse({'message': 'success', 'created': created, 'skipped': skipped, 'errors': errors})
    except PermissionError as exc:
        return JsonResponse({'message': 'forbidden', 'error': str(exc)}, status=403)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def staff_location_daily_route_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    staff = get_staff_user_for_request(request)
    can_view_all = _staff_location_can_view_all(request)
    staff_id = request.GET.get('staff_id') or request.GET.get('staffID')
    if staff_id and can_view_all:
        selected_staff = tenant_qs(request, StaffUser).filter(pk=staff_id, isDeleted=False).first()
    else:
        selected_staff = staff
    if selected_staff is None:
        return JsonResponse({'message': 'forbidden', 'error': 'Staff profile is required.'}, status=403)
    route_date = parse_date(request.GET.get('date') or '') or datetime.today().date()
    start_at = datetime.combine(route_date, datetime.min.time())
    if settings.USE_TZ:
        start_at = timezone.make_aware(start_at, timezone.get_current_timezone())
    end_at = start_at + timedelta(days=1)
    logs = tenant_qs(request, StaffLocationLog).filter(
        staff=selected_staff,
        isDeleted=False,
        recorded_at__gte=start_at,
        recorded_at__lt=end_at,
    ).order_by('recorded_at')
    points = [
        {
            'id': log.pk,
            'latitude': float(log.latitude),
            'longitude': float(log.longitude),
            'accuracy': log.accuracy,
            'speed': log.speed,
            'heading': log.heading,
            'battery_percent': log.battery_percent,
            'is_mock_location': log.is_mock_location,
            'recorded_at': (
                timezone.localtime(log.recorded_at).isoformat()
                if settings.USE_TZ else log.recorded_at.isoformat()
            ),
        }
        for log in logs[:2000]
    ]
    return JsonResponse({
        'message': 'success',
        'staff': {'id': selected_staff.pk, 'name': selected_staff.name or '-'},
        'date': route_date.isoformat(),
        'count': len(points),
        'points': points,
    })


def staff_location_staff_options_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _staff_location_can_view_all(request):
        staff = get_staff_user_for_request(request)
        rows = [{'id': staff.pk, 'name': staff.name or '-'}] if staff else []
    else:
        rows = [
            {'id': item.pk, 'name': item.name or '-'}
            for item in tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name')
        ]
    return JsonResponse({'message': 'success', 'staff': rows})


class StaffAttendanceListJson(BaseDatatableView):
    order_columns = ['datetime', 'loginDateTime', 'loginPhoto', 'login_location', 'login_remark', 'logoutDateTime', 'logoutPhoto', 'logout_location',
                     'logout_remark',
                     ]
    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            return attendance_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                datetime__range=(sDate.date(), eDate.date() + timedelta(days=1)),
                staffID__user_ID__id=self.request.user.pk
            )
        except:
            return attendance_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                datetime__date=datetime.today().date(),
                staffID__user_ID__id=self.request.user.pk
            )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(login_remark__icontains=search) | Q(login_location__icontains=search) | Q(
                    logout_remark__icontains=search) | Q(
                    logout_location__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isLogIn == True:
                login_time = item.loginDateTime.strftime('%I:%M %p')
                login_remark = item.login_remark
                login_location = item.login_location
                login_photo = '<img src="{}" alt="Login photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(image_url(item.loginPhoto, 'thumbnail'))) if getattr(item, 'loginPhoto', None) else '-'
            else:
                login_time = '-'
                login_remark = '-'
                login_location = '-'
                login_photo = '-'
            if item.isLogOut == True:
                logout_time = item.logoutDateTime.strftime('%I:%M %p')
                logout_remark = item.logout_remark
                logout_location = item.logout_location
                logout_photo = '<img src="{}" alt="Logout photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(image_url(item.logoutPhoto, 'thumbnail'))) if getattr(item, 'logoutPhoto', None) else '-'
            else:
                logout_time = '-'
                logout_remark = '-'
                logout_location = '-'
                logout_photo = '-'
            json_data.append([
                escape(item.datetime.strftime('%d-%m-%Y')),
                login_time,
                login_photo,
                login_location,
                login_remark,
                logout_time,
                logout_photo,
                logout_location,
                logout_remark
            ])
        return json_data


class AdminAttendanceListJson(BaseDatatableView):
    order_columns = ['datetime', 'staffID.name', 'loginDateTime', 'loginPhoto', 'logoutDateTime', 'logoutPhoto', 'login_location', 'login_remark',
                     'logout_location', 'logout_remark',
                     ]
    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            if staffID == 'All':
                return attendance_list_base_qs(self.request).filter(
                    isDeleted__exact=False,
                    datetime__range=(sDate.date(), eDate.date() + timedelta(days=1))
                )
            else:
                return attendance_list_base_qs(self.request).filter(
                    isDeleted__exact=False,
                    datetime__range=(sDate.date(), eDate.date() + timedelta(days=1)),
                    staffID_id=int(staffID)
                )
        except:
            return attendance_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                datetime__date=datetime.today().date()
            )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(login_remark__icontains=search) | Q(login_location__icontains=search) | Q(
                    logout_remark__icontains=search) | Q(
                    logout_location__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isLogIn == True:
                login_time = item.loginDateTime.strftime('%I:%M %p')
                login_remark = item.login_remark
                login_location = item.login_location
                login_photo = '<img src="{}" alt="Login photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(image_url(item.loginPhoto, 'thumbnail'))) if getattr(item, 'loginPhoto', None) else '-'
            else:
                login_time = '-'
                login_remark = '-'
                login_location = '-'
                login_photo = '-'
            if item.isLogOut == True:
                logout_time = item.logoutDateTime.strftime('%I:%M %p')
                logout_remark = item.logout_remark
                logout_location = item.logout_location
                logout_photo = '<img src="{}" alt="Logout photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(image_url(item.logoutPhoto, 'thumbnail'))) if getattr(item, 'logoutPhoto', None) else '-'
            else:
                logout_time = '-'
                logout_remark = '-'
                logout_location = '-'
                logout_photo = '-'
            json_data.append([
                escape(item.datetime.strftime('%d-%m-%Y')),
                escape(item.staffID.name),
                login_time,
                login_photo,
                logout_time,
                logout_photo,
                login_location,
                login_remark,
                logout_location,
                logout_remark,
                str(item.staffID.pk)
            ])
        return json_data
