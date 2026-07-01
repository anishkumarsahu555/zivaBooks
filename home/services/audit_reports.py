import json

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from home.models import AuditLog, Tenant
from home.services.collections import has_any_permission
from home.tenancy import get_current_tenant


def request_user_has_audit_access(user):
    return has_any_permission(user, 'home.can_view_audit_logs', 'home.can_view_party_credentials')


def tenant_audit_log_queryset(request):
    tenant = get_current_tenant(request)
    if tenant is None:
        return AuditLog.objects.none()
    tenant_content_type = ContentType.objects.get_for_model(Tenant)
    return AuditLog.objects.select_related('content_type', 'actor').filter(
        Q(snapshot__tenant=tenant.pk) |
        Q(content_type=tenant_content_type, object_id=tenant.pk)
    )


def apply_audit_log_filters(request, qs):
    search = (request.GET.get('search[value]') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    action = (request.GET.get('action') or '').strip()
    actor_id = (request.GET.get('actor') or '').strip()
    content_type_id = (request.GET.get('content_type') or '').strip()
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())

    if action:
        qs = qs.filter(action=action)
    if actor_id.isdigit():
        qs = qs.filter(actor_id=int(actor_id))
    if content_type_id.isdigit():
        qs = qs.filter(content_type_id=int(content_type_id))
    if date_from:
        qs = qs.filter(datetime__date__gte=date_from)
    if date_to:
        qs = qs.filter(datetime__date__lte=date_to)
    if search:
        qs = qs.filter(
            Q(object_repr__icontains=search) |
            Q(action__icontains=search) |
            Q(actor__username__icontains=search) |
            Q(content_type__model__icontains=search) |
            Q(changes__icontains=search) |
            Q(snapshot__icontains=search)
        )
    if keyword:
        qs = qs.filter(
            Q(object_repr__icontains=keyword) |
            Q(action__icontains=keyword) |
            Q(actor__username__icontains=keyword) |
            Q(content_type__model__icontains=keyword) |
            Q(changes__icontains=keyword) |
            Q(snapshot__icontains=keyword)
        )
    return qs


def audit_log_detail_payload(log):
    dt_display = format_audit_datetime(log.datetime)
    snapshot = log.snapshot or {}
    changes = log.changes or {}
    return {
        'id': log.pk,
        'datetime': dt_display,
        'action': log.get_action_display(),
        'actor': getattr(log.actor, 'username', 'System'),
        'model': log.content_type.model.replace('_', ' ').title(),
        'object_id': log.object_id,
        'object_repr': log.object_repr,
        'changes': changes,
        'changes_pretty': json.dumps(changes, indent=2, ensure_ascii=True),
        'snapshot': snapshot,
        'snapshot_pretty': json.dumps(snapshot, indent=2, ensure_ascii=True),
    }


def format_audit_datetime(value):
    if timezone.is_naive(value):
        return value.strftime('%d-%m-%Y %I:%M %p')
    return timezone.localtime(value).strftime('%d-%m-%Y %I:%M %p')
