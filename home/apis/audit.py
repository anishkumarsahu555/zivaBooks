import csv

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from home.models import AuditLog, StaffUser
from home.services.audit_reports import (
    apply_audit_log_filters,
    audit_log_detail_payload,
    format_audit_datetime,
    request_user_has_audit_access,
    tenant_audit_log_queryset,
)
from home.services.collections import has_any_permission as _has_any_permission
from home.services.reports import (
    pdf_base_context as _pdf_base_context,
    xlsx_report_response as _xlsx_report_response,
)
from utils.logger import logger
from home.tenancy import get_current_tenant


class AuditLogListJson(BaseDatatableView):
    order_columns = ['datetime', 'actor__username', 'content_type__model', 'action', 'object_repr', 'object_id']

    def get_initial_queryset(self):
        if not request_user_has_audit_access(self.request.user):
            return AuditLog.objects.none()
        return tenant_audit_log_queryset(self.request).only(
            'id', 'datetime', 'action', 'object_id', 'object_repr',
            'changes', 'snapshot', 'actor__username', 'content_type__model',
        )

    def filter_queryset(self, qs):
        return apply_audit_log_filters(self.request, qs)

    def prepare_results(self, qs):
        data = []
        for item in qs:
            if item.actor_id:
                try:
                    staff_user = StaffUser.objects.filter(user_ID=item.actor_id, isDeleted=False).only('pk').first()
                    if staff_user:
                        actor = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{staff_user.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.actor.username)}</span>'''
                    else:
                        actor = escape(item.actor.username)
                except:
                    actor = escape(item.actor.username)
            else:
                actor = 'System'
            model_name = escape(item.content_type.model.replace('_', ' ').title())
            action_label = escape(item.get_action_display())
            changes_count = len(item.changes or {})
            detail_btn = (
                '<button class="ui tiny teal basic icon button audit-detail-trigger" '
                'type="button" title="View detail" data-tooltip="View detail" data-position="left center" '
                'onclick="openAuditDetail(\'{}\', {})">'
                '<i class="eye icon"></i></button>'
            ).format(reverse('homeApp:get_audit_log_detail_api'), item.pk)
            raw_model = item.content_type.model
            object_repr = escape(item.object_repr)
            if raw_model == 'party':
                object_repr = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.object_id}')" style="cursor: pointer;"><i class="briefcase icon" style="margin-right: 4px; opacity: 0.8;"></i>{object_repr}</span>'''
            elif raw_model == 'staffuser':
                object_repr = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.object_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{object_repr}</span>'''
            elif raw_model == 'bankaccount':
                object_repr = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('bank', '{item.object_id}')" style="cursor: pointer;"><i class="university icon" style="margin-right: 4px; opacity: 0.8;"></i>{object_repr}</span>'''
            elif raw_model in ('collection', 'sales', 'cashcounter', 'collectioncashcounter'):
                object_repr = f'''<span class="lookup-detail-link" onclick="showGenericObjectLookup('{raw_model}', '{item.object_id}')" style="cursor: pointer;"><i class="file alternate outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{object_repr}</span>'''
            data.append([
                escape(format_audit_datetime(item.datetime)),
                actor,
                model_name,
                action_label,
                object_repr,
                escape(str(item.object_id)),
                escape(str(changes_count)),
                detail_btn,
            ])
        return data


def get_audit_log_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not request_user_has_audit_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    audit_id = request.GET.get('id')
    if not str(audit_id).isdigit():
        return JsonResponse({'message': 'invalid'}, status=400, safe=False)
    obj = get_object_or_404(tenant_audit_log_queryset(request), pk=int(audit_id))
    return JsonResponse({'data': audit_log_detail_payload(obj)}, safe=False)


def export_audit_log_csv(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_export_audit_logs', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    qs = apply_audit_log_filters(request, tenant_audit_log_queryset(request)).order_by('-datetime')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit-log-report.csv"'
    writer = csv.writer(response)
    writer.writerow(['Datetime', 'Actor', 'Model', 'Action', 'Object ID', 'Object', 'Changes', 'Snapshot'])
    for item in qs:
        payload = audit_log_detail_payload(item)
        writer.writerow([
            payload['datetime'],
            payload['actor'],
            payload['model'],
            payload['action'],
            payload['object_id'],
            payload['object_repr'],
            payload['changes_pretty'],
            payload['snapshot_pretty'],
        ])
    return response


def export_audit_log_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_export_audit_logs', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    qs = apply_audit_log_filters(request, tenant_audit_log_queryset(request)).order_by('-datetime')
    rows = []
    for item in qs:
        payload = audit_log_detail_payload(item)
        rows.append([
            payload['datetime'],
            payload['actor'],
            payload['model'],
            payload['action'],
            payload['object_id'],
            payload['object_repr'],
            payload['changes_pretty'],
            payload['snapshot_pretty'],
        ])
    return _xlsx_report_response('audit-log-report', [{
        'title': 'Audit Log',
        'report_title': 'Audit Log Report',
        'report_subtitle': 'Filtered audit history',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Generated By', _pdf_base_context(request).get('pdf_generated_by'))],
        'tables': [{'title': 'Audit Entries', 'headers': ['Datetime', 'Actor', 'Model', 'Action', 'Object ID', 'Object', 'Changes', 'Snapshot'], 'rows': rows}],
    }])
