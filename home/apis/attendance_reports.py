from calendar import month_name
from datetime import datetime, timedelta

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import CSS, HTML

from home.models import Attendance, StaffUser
from home.services.attendance import (
    attendance_list_base_qs,
    attendance_month_bounds,
    attendance_month_reference,
    attendance_monthly_rows,
)
from home.services.reports import (
    pdf_base_context as _pdf_base_context,
    xlsx_report_response as _xlsx_report_response,
)
from utils.logger import logger
from home.tenancy import get_current_tenant, tenant_qs


def export_attendance_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = attendance_list_base_qs(request).filter(isDeleted=False, datetime__range=(start_date, end_date + timedelta(days=1)))
    if request.user.has_perm('home.can_view_all_attendance') or request.user.has_perm('home.can_view_attendance_reports') or request.user.has_perm('home.can_view_party_credentials'):
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(staffID_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    else:
        qs = qs.filter(staffID__user_ID__id=request.user.pk)
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
        staff_name = staff.name if staff else 'My Attendance'
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(login_remark__icontains=search) | Q(login_location__icontains=search) |
            Q(logout_remark__icontains=search) | Q(logout_location__icontains=search)
        )
    rows = [[
        item.datetime.strftime('%d/%m/%Y') if item.datetime else '-',
        item.staffID.name if item.staffID_id else '-',
        item.loginDateTime.strftime('%I:%M %p') if item.isLogIn and item.loginDateTime else '-',
        item.logoutDateTime.strftime('%I:%M %p') if item.isLogOut and item.logoutDateTime else '-',
        item.login_location or '-',
        item.login_remark or '-',
        item.logout_location or '-',
        item.logout_remark or '-',
    ] for item in qs.order_by('-datetime')]
    return _xlsx_report_response('attendance-list', [{
        'title': 'Attendance',
        'report_title': 'Attendance List Export',
        'report_subtitle': 'Detailed filtered attendance rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Attendance', 'headers': ['Date', 'Staff', 'Login Time', 'Logout Time', 'Login Location', 'Login Remark', 'Logout Location', 'Logout Remark'], 'rows': rows}],
    }])


def generate_attendance_pdf_staff_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    col = tenant_qs(request, Attendance).select_related().filter(datetime__range=(
        startDate.date(), endDate.date() + timedelta(days=1)),
        isDeleted__exact=False, staffID__user_ID_id__exact=request.user.pk).order_by(
        'datetime')
    user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    context = {
        'startDate': startDate,
        'endDate': endDate,
        'col': col,
        'staffName': user.name,
    }
    context.update(_pdf_base_context(request, [
        f'From: {startDate.strftime("%d/%m/%Y")}',
        f'To: {endDate.strftime("%d/%m/%Y")}',
        f'Staff: {user.name}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/staffAttendanceReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_attendance_excel_staff_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    col = tenant_qs(request, Attendance).select_related('staffID').filter(
        datetime__range=(startDate.date(), endDate.date() + timedelta(days=1)),
        isDeleted__exact=False,
        staffID__user_ID_id__exact=request.user.pk
    ).order_by('datetime')
    user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    rows = [[
        item.datetime.strftime('%d/%m/%Y') if item.datetime else '-',
        item.staffID.name if item.staffID_id else '-',
        item.loginDateTime.strftime('%I:%M %p') if item.isLogIn and item.loginDateTime else '-',
        item.logoutDateTime.strftime('%I:%M %p') if item.isLogOut and item.logoutDateTime else '-',
        item.login_location or '-',
        item.login_remark or '-',
        item.logout_location or '-',
        item.logout_remark or '-',
    ] for item in col]
    return _xlsx_report_response('attendance-staff', [{
        'title': 'Attendance',
        'report_title': 'Attendance Report',
        'report_subtitle': 'My attendance detail',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', startDate.strftime('%d/%m/%Y')), ('To', endDate.strftime('%d/%m/%Y')), ('Staff', user.name)],
        'tables': [{'title': 'Attendance Detail', 'headers': ['Date', 'Staff', 'Login', 'Logout', 'Login Location', 'Login Remark', 'Logout Location', 'Logout Remark'], 'rows': rows}],
    }])


def generate_attendance_monthly_pdf_staff_report(request):
    reference_date = attendance_month_reference(request)
    month_start, month_end = attendance_month_bounds(reference_date)
    staff = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    day_numbers, rows = attendance_monthly_rows(request, [staff], month_start, month_end)
    context = {
        'month_label': f'{month_name[reference_date.month]} {reference_date.year}',
        'month_start': month_start,
        'month_end': month_end,
        'day_numbers': day_numbers,
        'rows': rows,
        'staffName': staff.name,
        'generated_on': timezone.now(),
        'is_single_staff': True,
    }
    context.update(_pdf_base_context(request, [
        f'Month: {month_name[reference_date.month]} {reference_date.year}',
        f'Staff: {staff.name}',
        'Legend: P/A/H/-',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=attendance-monthly-{reference_date.strftime("%Y-%m")}-{staff.name}.pdf'
    html = render_to_string("home/report/attendanceMonthlyMatrixPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: .55cm; }')])
    return response


def generate_attendance_monthly_excel_staff_report(request):
    reference_date = attendance_month_reference(request)
    month_start, month_end = attendance_month_bounds(reference_date)
    staff = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    day_numbers, rows = attendance_monthly_rows(request, [staff], month_start, month_end)
    excel_rows = [[staff.name] + row['statuses'] + [row['present_count'], row['absent_count'], row['holiday_count']] for row in rows]
    headers = ['Staff'] + [str(day) for day in day_numbers] + ['P', 'A', 'H']
    return _xlsx_report_response('attendance-monthly', [{
        'title': 'Attendance Monthly',
        'report_title': 'Monthly Attendance Register',
        'report_subtitle': f'{month_name[reference_date.month]} {reference_date.year}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Month', f'{month_name[reference_date.month]} {reference_date.year}'), ('Staff', staff.name), ('Legend', 'P/A/H/-')],
        'tables': [{'title': 'Attendance Matrix', 'headers': headers, 'rows': excel_rows}],
    }])


def generate_attendance_pdf_admin_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    staffID = request.GET.get('staffID')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    if staffID == 'All':
        col = tenant_qs(request, Attendance).select_related().filter(datetime__range=(
            startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False).order_by('staffID__name')
        staffName = "All"
    else:
        col = tenant_qs(request, Attendance).select_related().filter(datetime__range=(
            startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False, staffID_id=int(staffID)).order_by('datetime')
        user = tenant_qs(request, StaffUser).get(id=int(staffID))
        staffName = user.name
    context = {
        'startDate': startDate,
        'endDate': endDate,
        'col': col,
        'staffName': staffName,
    }
    context.update(_pdf_base_context(request, [
        f'From: {startDate.strftime("%d/%m/%Y")}',
        f'To: {endDate.strftime("%d/%m/%Y")}',
        f'Staff: {staffName}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/adminAttendanceReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_attendance_excel_admin_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    staffID = request.GET.get('staffID')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    if staffID == 'All':
        col = tenant_qs(request, Attendance).select_related('staffID').filter(
            datetime__range=(startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False
        ).order_by('staffID__name', 'datetime')
        staffName = 'All'
    else:
        col = tenant_qs(request, Attendance).select_related('staffID').filter(
            datetime__range=(startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False,
            staffID_id=int(staffID)
        ).order_by('datetime')
        staffName = tenant_qs(request, StaffUser).get(id=int(staffID)).name
    rows = [[
        item.datetime.strftime('%d/%m/%Y') if item.datetime else '-',
        item.staffID.name if item.staffID_id else '-',
        item.loginDateTime.strftime('%I:%M %p') if item.isLogIn and item.loginDateTime else '-',
        item.logoutDateTime.strftime('%I:%M %p') if item.isLogOut and item.logoutDateTime else '-',
        item.login_location or '-',
        item.login_remark or '-',
        item.logout_location or '-',
        item.logout_remark or '-',
    ] for item in col]
    return _xlsx_report_response('attendance-report', [{
        'title': 'Attendance',
        'report_title': 'Attendance Report',
        'report_subtitle': 'Admin attendance detail',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', startDate.strftime('%d/%m/%Y')), ('To', endDate.strftime('%d/%m/%Y')), ('Staff', staffName)],
        'tables': [{'title': 'Attendance Detail', 'headers': ['Date', 'Staff', 'Login', 'Logout', 'Login Location', 'Login Remark', 'Logout Location', 'Logout Remark'], 'rows': rows}],
    }])


def generate_attendance_monthly_pdf_admin_report(request):
    reference_date = attendance_month_reference(request)
    month_start, month_end = attendance_month_bounds(reference_date)
    staffID = request.GET.get('staffID')
    if not staffID or staffID == 'All':
        staff_queryset = list(tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'))
        staff_name = 'All Staff'
    else:
        staff = tenant_qs(request, StaffUser).get(id=int(staffID))
        staff_queryset = [staff]
        staff_name = staff.name
    day_numbers, rows = attendance_monthly_rows(request, staff_queryset, month_start, month_end)
    context = {
        'month_label': f'{month_name[reference_date.month]} {reference_date.year}',
        'month_start': month_start,
        'month_end': month_end,
        'day_numbers': day_numbers,
        'rows': rows,
        'staffName': staff_name,
        'generated_on': timezone.now(),
        'is_single_staff': len(staff_queryset) == 1,
    }
    context.update(_pdf_base_context(request, [
        f'Month: {month_name[reference_date.month]} {reference_date.year}',
        f'Staff: {staff_name}',
        'Legend: P/A/H/-',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=attendance-monthly-{reference_date.strftime("%Y-%m")}.pdf'
    html = render_to_string("home/report/attendanceMonthlyMatrixPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: .55cm; }')])
    return response


def generate_attendance_monthly_excel_admin_report(request):
    reference_date = attendance_month_reference(request)
    month_start, month_end = attendance_month_bounds(reference_date)
    staffID = request.GET.get('staffID')
    if not staffID or staffID == 'All':
        staff_queryset = list(tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'))
        staff_name = 'All Staff'
    else:
        staff = tenant_qs(request, StaffUser).get(id=int(staffID))
        staff_queryset = [staff]
        staff_name = staff.name
    day_numbers, rows = attendance_monthly_rows(request, staff_queryset, month_start, month_end)
    excel_rows = [[row['staff'].name] + row['statuses'] + [row['present_count'], row['absent_count'], row['holiday_count']] for row in rows]
    headers = ['Staff'] + [str(day) for day in day_numbers] + ['P', 'A', 'H']
    return _xlsx_report_response('attendance-monthly', [{
        'title': 'Attendance Monthly',
        'report_title': 'Monthly Attendance Register',
        'report_subtitle': f'{month_name[reference_date.month]} {reference_date.year}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Month', f'{month_name[reference_date.month]} {reference_date.year}'), ('Staff', staff_name), ('Legend', 'P/A/H/-')],
        'tables': [{'title': 'Attendance Matrix', 'headers': headers, 'rows': excel_rows}],
    }])
