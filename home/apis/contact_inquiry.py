import json
import logging
from threading import Thread
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods
from home.models import LicenseInquiry

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _extract_email_address(raw_email):
    if not raw_email:
        return 'admin@zivabooks.com'
    if '<' in str(raw_email) and '>' in str(raw_email):
        return str(raw_email).split('<')[-1].split('>')[0].strip()
    return str(raw_email).strip()


def _resolve_admin_emails():
    default_from = getattr(settings, 'DEFAULT_FROM_EMAIL', 'admin@zivabooks.com')
    clean_email = _extract_email_address(default_from)
    return [clean_email]


def send_license_inquiry_admin_email(inquiry, request=None):
    """
    Sends an email notification to system admins when a new License Inquiry is submitted.
    Both from_email and to_email are derived from DEFAULT_FROM_EMAIL.
    Dispatched asynchronously in a daemon thread so API responses stay instantaneous.
    """
    if not getattr(settings, 'LICENSE_INQUIRY_NOTIFY_ADMIN', True):
        return

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'zivaBooks <admin@zivabooks.com>')
    admin_recipients = _resolve_admin_emails()
    if not admin_recipients:
        admin_recipients = [_extract_email_address(from_email)]

    admin_url = None
    if request:
        try:
            admin_url = request.build_absolute_uri(f"/admin/home/licenseinquiry/{inquiry.id}/change/")
        except Exception:
            admin_url = f"/admin/home/licenseinquiry/{inquiry.id}/change/"
    else:
        admin_url = f"/admin/home/licenseinquiry/{inquiry.id}/change/"

    context = {
        'inquiry': inquiry,
        'admin_url': admin_url,
    }

    try:
        subject = render_to_string('home/emails/license_inquiry_admin_subject.txt', context).strip()
    except Exception:
        subject = f"[zivaBooks Admin] New License Request: {inquiry.full_name} ({inquiry.license_plan.title()} Plan)"

    try:
        body = render_to_string('home/emails/license_inquiry_admin_email.txt', context)
    except Exception:
        body = f"New License Request from {inquiry.full_name} ({inquiry.email}, Phone: {inquiry.phone}). Plan: {inquiry.license_plan}."

    try:
        html_body = render_to_string('home/emails/license_inquiry_admin_email.html', context)
    except Exception:
        html_body = None

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'zivaBooks <admin@zivabooks.com>')

    def worker():
        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=body,
                from_email=from_email,
                to=admin_recipients,
            )
            if html_body:
                msg.attach_alternative(html_body, 'text/html')
            sent_count = msg.send(fail_silently=False)
            if sent_count:
                logger.info('[License Inquiry Email] Admin notification sent to %s for inquiry #%s', admin_recipients, inquiry.id)
            else:
                logger.warning('[License Inquiry Email] Send returned 0 for inquiry #%s', inquiry.id)
        except Exception as err:
            logger.warning('[License Inquiry Email] Exception sending email for inquiry #%s: %s', inquiry.id, str(err))

    # Synchronous dispatch in test environment or when testing
    if getattr(settings, 'EMAIL_BACKEND', '').endswith('locmem.EmailBackend'):
        worker()
    else:
        Thread(target=worker, daemon=True).start()


@require_http_methods(["POST"])
def contact_inquiry_api(request):
    """API endpoint to receive and store prospective license purchaser inquiry form details."""
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body.decode('utf-8'))
        else:
            data = request.POST

        full_name = (data.get('full_name') or '').strip()
        email = (data.get('email') or '').strip()
        phone = (data.get('phone') or '').strip()
        business_name = (data.get('business_name') or '').strip() or None
        license_plan = (data.get('license_plan') or 'growth').strip().lower()
        message = (data.get('message') or '').strip() or None
        
        try:
            user_seats = int(data.get('user_seats', 1))
        except (ValueError, TypeError):
            user_seats = 1

        if not full_name:
            return JsonResponse({'status': 'error', 'message': 'Full name is required.'}, status=400)
        if not email or '@' not in email:
            return JsonResponse({'status': 'error', 'message': 'A valid email address is required.'}, status=400)
        if not phone or len(phone) < 7:
            return JsonResponse({'status': 'error', 'message': 'A valid phone number is required.'}, status=400)

        inquiry = LicenseInquiry.objects.create(
            full_name=full_name,
            business_name=business_name,
            email=email,
            phone=phone,
            license_plan=license_plan,
            user_seats=max(1, user_seats),
            message=message,
            status='new',
            ip_address=_get_client_ip(request)
        )

        # Trigger Admin Email Notification
        send_license_inquiry_admin_email(inquiry, request=request)

        return JsonResponse({
            'status': 'success',
            'inquiry_id': inquiry.id,
            'message': 'Thank you! Your license request has been submitted successfully. Our team will contact you shortly.'
        })
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': f'Failed to process inquiry: {str(exc)}'}, status=500)


@require_http_methods(["GET"])
def list_license_plans_api(request):
    """API endpoint returning active dynamic license plans and their configurable features."""
    try:
        from django.core.cache import cache
        plan_list = cache.get('active_license_plans_cache')
        if plan_list is not None:
            return JsonResponse({'status': 'success', 'plans': plan_list})

        from home.models import LicensePlan
        plans = LicensePlan.get_active_plans()
        plan_list = []
        for plan in plans:
            plan_list.append({
                'id': plan.id,
                'plan_code': plan.plan_code,
                'name': plan.name,
                'tagline': plan.tagline or '',
                'price_per_year': float(plan.price_per_year),
                'original_price_per_year': float(plan.original_price_per_year or 0),
                'user_seats': plan.user_seats,
                'is_featured': plan.is_featured,
                'display_order': plan.display_order,
                'features': {
                    'sales_billing_enabled': plan.sales_billing_enabled,
                    'proforma_estimates_enabled': plan.proforma_estimates_enabled,
                    'custom_invoice_series_enabled': plan.custom_invoice_series_enabled,
                    'sales_returns_credit_notes_enabled': plan.sales_returns_credit_notes_enabled,
                    'thermal_printing_enabled': plan.thermal_printing_enabled,
                    'pdf_excel_exports_enabled': plan.pdf_excel_exports_enabled,
                    'gst_einvoicing_enabled': plan.gst_einvoicing_enabled,
                    'eway_bill_enabled': plan.eway_bill_enabled,
                    'inventory_enabled': plan.inventory_enabled,
                    'fefo_batch_tracking_enabled': plan.fefo_batch_tracking_enabled,
                    'multi_warehouse_enabled': plan.multi_warehouse_enabled,
                    'stock_adjustment_vouchers_enabled': plan.stock_adjustment_vouchers_enabled,
                    'zero_stock_alerts_enabled': plan.zero_stock_alerts_enabled,
                    'multi_counter_pos_enabled': plan.multi_counter_pos_enabled,
                    'dual_otp_enabled': plan.dual_otp_enabled,
                    'cashbook_bankbook_enabled': plan.cashbook_bankbook_enabled,
                    'party_ledgers_enabled': plan.party_ledgers_enabled,
                    'double_entry_journals_enabled': plan.double_entry_journals_enabled,
                    'gps_tracking_enabled': plan.gps_tracking_enabled,
                    'route_history_map_enabled': plan.route_history_map_enabled,
                    'geofenced_attendance_enabled': plan.geofenced_attendance_enabled,
                    'ziva_ai_enabled': plan.ziva_ai_enabled,
                    'ziva_ai_max_queries_per_month': plan.ziva_ai_max_queries_per_month,
                    'multi_branch_isolation_enabled': plan.multi_branch_isolation_enabled,
                    'custom_shadow_mode_enabled': plan.custom_shadow_mode_enabled,
                    'role_based_permissions_enabled': plan.role_based_permissions_enabled,
                },
                'features_list': plan.features_list or []
            })
        cache.set('active_license_plans_cache', plan_list, timeout=300)
        return JsonResponse({'status': 'success', 'plans': plan_list})
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': f'Failed to fetch plans: {str(exc)}'}, status=500)

