from datetime import datetime

from django.db.models import ExpressionWrapper, F, FloatField, Q, Sum, Window
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView
from weasyprint import CSS, HTML

from home.models import *
from home.services.collections import has_any_permission as _has_any_permission
from home.services.party_ledger_reports import (
    customer_view_particulars,
    customer_visible_party_ledger_qs_for_request,
    parse_ddmmyyyy_api,
    party_ledger_export_party,
    party_ledger_statement_rows,
    strip_trailing_particular_chunks,
)
from home.services.reports import (
    formatINR,
    pdf_base_context as _pdf_base_context,
    xlsx_report_response as _xlsx_report_response,
)
from home.tenancy import get_current_tenant, tenant_qs


class PartyLedgerListJson(BaseDatatableView):
    order_columns = ['txDateTime', 'particulars', 'txDateTime', 'txDateTime']

    def _customer_visible_party_ledger_qs(self, qs):
        return customer_visible_party_ledger_qs_for_request(self.request, qs)

    def _resolve_party(self):
        party_id = self.request.GET.get('partyId')
        if not party_id or not str(party_id).isdigit():
            return None
        if self.request.user.has_perm('home.can_manage_parties') or self.request.user.has_perm('home.can_view_party_credentials'):
            return tenant_qs(self.request, Party).filter(isDeleted=False, pk=int(party_id)).only('id', 'openingBalance', 'name').first()
        # Party portal user can only view own ledger.
        return tenant_qs(self.request, Party).filter(isDeleted=False, userID_id=self.request.user.pk).only('id', 'openingBalance', 'name').first()

    def get_initial_queryset(self):
        party = self._resolve_party()
        if not party:
            return tenant_qs(self.request, PartyLedgerEntry).none()
        is_customer_view = TenantMembership.objects.filter(
            user=self.request.user,
            role=TenantMembership.Role.PARTY,
        ).exists()
        today = datetime.today().date()
        start_date = parse_ddmmyyyy_api(self.request.GET.get('startDate'), today)
        end_date = parse_ddmmyyyy_api(self.request.GET.get('endDate'), today)
        if end_date and start_date and end_date < start_date:
            start_date, end_date = end_date, start_date
        base_qs = tenant_qs(self.request, PartyLedgerEntry).filter(isDeleted=False, party=party)
        if is_customer_view:
            base_qs = self._customer_visible_party_ledger_qs(base_qs)
        prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
            debit_total=Sum('debit'),
            credit_total=Sum('credit'),
        )
        opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
        self._opening_balance = opening
        qs = base_qs.filter(txDateTime__date__range=[start_date, end_date])
        signed = ExpressionWrapper(F('debit') - F('credit'), output_field=FloatField())
        # Compute running totals in chronological order, but display latest-first.
        return qs.annotate(
            signed_amount=signed,
            running_total=Window(
                expression=Sum('signed_amount'),
                order_by=[F('txDateTime').asc(), F('id').asc()],
            ),
        ).order_by('-txDateTime', '-id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(particulars__icontains=search) |
                Q(reference__icontains=search) |
                Q(source__icontains=search)
            )
        return qs

    def ordering(self, qs):
        return qs

    def prepare_results(self, qs):
        json_data = []
        opening = float(getattr(self, '_opening_balance', 0.0) or 0.0)
        is_customer_view = TenantMembership.objects.filter(
            user=self.request.user,
            role=TenantMembership.Role.PARTY,
        ).exists()
        sales_id_map = {}
        collection_id_map = {}
        counter_collection_id_map = {}
        if is_customer_view:
            sales_source_ids = [item.source_object_id for item in qs if item.source == PartyLedgerEntry.Source.SALES and item.source_object_id]
            collection_source_ids = [item.source_object_id for item in qs if item.source == PartyLedgerEntry.Source.COLLECTION and item.source_object_id]
            counter_collection_source_ids = [item.source_object_id for item in qs if item.source == PartyLedgerEntry.Source.COUNTER_COLLECTION and item.source_object_id]
            if sales_source_ids:
                sales_id_map = {
                    row['id']: (row.get('salesID') or row.get('invoiceNumber') or '')
                    for row in tenant_qs(self.request, Sales).filter(id__in=sales_source_ids).values('id', 'salesID', 'invoiceNumber')
                }
            if collection_source_ids:
                collection_id_map = {
                    row['id']: (row.get('paymentID') or '')
                    for row in tenant_qs(self.request, Collection).filter(id__in=collection_source_ids).values('id', 'paymentID')
                }
            if counter_collection_source_ids:
                counter_collection_id_map = {
                    row['id']: (row.get('paymentID') or '')
                    for row in tenant_qs(self.request, CollectionCashCounter).filter(id__in=counter_collection_source_ids).values('id', 'paymentID')
                }
        for item in qs:
            running = float(item.running_total or 0.0)
            balance = opening + running
            debit = float(item.debit or 0.0)
            credit = float(item.credit or 0.0)
            signed = debit - credit
            # Many legacy/admin flows save collection dates as date-only (00:00 time).
            # For display, prefer the record creation time in that case.
            time_dt = item.txDateTime
            try:
                if (
                    item.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION)
                    and item.txDateTime
                    and item.txDateTime.time().strftime('%H:%M:%S') == '00:00:00'
                    and getattr(item, 'datetime', None)
                ):
                    time_dt = item.datetime
            except Exception:
                time_dt = item.txDateTime
            date_html = '<div class="pl-date">{}</div><div class="pl-time">{}</div>'.format(
                escape(item.txDateTime.strftime('%d-%m-%Y')),
                escape(time_dt.strftime('%I:%M %p')),
            )
            particulars = (item.particulars or '').strip()
            if is_customer_view:
                particulars = customer_view_particulars(particulars)
                particulars = strip_trailing_particular_chunks(particulars)
            ref = (item.reference or '').strip()
            ref_html = ''
            if is_customer_view:
                ref_label = ''
                ref_value = ''
                if item.source == PartyLedgerEntry.Source.SALES:
                    if getattr(item, 'source_object_id', None):
                        ref_label = 'Sale ID'
                        ref_value = sales_id_map.get(item.source_object_id) or ref
                elif item.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION):
                    if item.source == PartyLedgerEntry.Source.COLLECTION:
                        ref_value = collection_id_map.get(item.source_object_id) or ref
                    else:
                        ref_value = counter_collection_id_map.get(item.source_object_id) or ref
                    if ref_value:
                        ref_label = 'Payment ID'
                if ref_label and ref_value:
                    ref_html = '<div class="pl-ref"><span class="pl-ref-chip">{}</span><span class="pl-ref-val">{}</span></div>'.format(
                        escape(ref_label),
                        escape(ref_value),
                    )
            if (item.source_content_type_id and item.source_object_id) or (item.source == PartyLedgerEntry.Source.TRANSFER and item.transferId):
                particulars_html = f'''<span class="lookup-detail-link" onclick="showPartyLedgerSourceLookup('{item.pk}')" style="cursor: pointer;"><i class="file alternate outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(particulars)}</span>'''
            else:
                particulars_html = escape(particulars)
            part_html = '<div class="pl-part">{}</div>{}'.format(particulars_html, ref_html)
            if abs(signed) < 1e-9:
                amt_html = '-'
            elif signed > 0:
                amt_html = '<span class="pl-amt pl-amt-in"><span class="pl-chip pl-chip-dr">DR</span> ₹ {}</span>'.format(
                    formatINR(format(abs(signed), '.2f'))
                )
            else:
                amt_html = '<span class="pl-amt pl-amt-out"><span class="pl-chip pl-chip-cr">CR</span> ₹ {}</span>'.format(
                    formatINR(format(abs(signed), '.2f'))
                )
            json_data.append([
                date_html,
                part_html,
                amt_html,
                formatINR(format(balance, '.2f')),
            ])
        return json_data


def generate_party_ledger_pdf(request, party_id):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_party', 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    party = party_ledger_export_party(request, party_id)
    if not party:
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    is_customer_view = TenantMembership.objects.filter(
        user=request.user,
        role=TenantMembership.Role.PARTY,
    ).exists()
    today = datetime.today().date()
    start_date = parse_ddmmyyyy_api(request.GET.get('startDate'), today)
    end_date = parse_ddmmyyyy_api(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = party_ledger_statement_rows(
        request,
        party,
        start_date,
        end_date,
        is_customer_view=is_customer_view,
    )
    context = {
        'party': party,
        'start_date': start_date,
        'end_date': end_date,
        'opening': statement['opening'],
        'rows': statement['rows'],
        'total_debit': statement['total_debit'],
        'total_credit': statement['total_credit'],
        'closing': statement['closing'],
        'generated_on': timezone.now(),
    }
    context.update(_pdf_base_context(request, [
        f'Party: {party.name}',
        f'From: {start_date.strftime("%d/%m/%Y") if start_date else "-"}',
        f'To: {end_date.strftime("%d/%m/%Y") if end_date else "-"}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    safe_party_name = (party.name or f'party-{party.pk}').replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename=party-ledger-{safe_party_name}.pdf'
    html = render_to_string("home/party/partyLedgerPDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(
        response,
        stylesheets=[CSS(string='@page { size: A4 portrait; margin: 1cm 0.75cm 1.1cm; }')]
    )
    return response


def generate_party_ledger_excel(request, party_id):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_party', 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    party = party_ledger_export_party(request, party_id)
    if not party:
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    is_customer_view = TenantMembership.objects.filter(user=request.user, role=TenantMembership.Role.PARTY).exists()
    today = datetime.today().date()
    start_date = parse_ddmmyyyy_api(request.GET.get('startDate'), today)
    end_date = parse_ddmmyyyy_api(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = party_ledger_statement_rows(request, party, start_date, end_date, is_customer_view=is_customer_view)
    safe_party_name = (party.name or f'party-{party.pk}').replace('/', '-')
    return _xlsx_report_response(f'party-ledger-{safe_party_name}', [{
        'title': 'Party Ledger',
        'report_title': 'Customer Ledger Statement',
        'report_subtitle': party.name,
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Party', party.name), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y'))],
        'tables': [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [['Opening', statement['opening']], ['Debit', statement['total_debit']], ['Credit', statement['total_credit']], ['Closing', statement['closing']]]},
            {'title': 'Ledger Detail', 'headers': ['Date', 'Particulars', 'Voucher Type', 'Voucher No', 'Debit', 'Credit', 'Balance'], 'rows': [[row['date'].strftime('%d/%m/%Y') if row['date'] else '-', row['particulars'], row['voucher_type'], row['voucher_no'], row['debit'], row['credit'], row['balance']] for row in statement['rows']]},
        ],
    }])


def get_party_ledger_entry_source_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    entry_id = request.GET.get('id')
    if not entry_id or not entry_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    entry = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False, pk=int(entry_id)).first()
    if entry is None:
        return JsonResponse({'message': 'not_found'}, safe=False)

    title = "Transaction Source Document"
    details = []
    detail_url = ""

    if entry.source_content_type and entry.source_object_id:
        obj = entry.source_object
        if obj:
            model_name = entry.source_content_type.model
            if model_name == 'collection':
                title = f"Collection Details - {obj.paymentID or ''}"
                detail_url = f"/collection_list/?search={obj.paymentID or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Payment ID", obj.paymentID or "-"),
                    ("Mode of Payment", obj.modeOfPayment),
                    ("Paid Amount", f"₹ {obj.paidAmount:.2f}"),
                    ("Collected By", obj.collectedBy.name if obj.collectedBy else "-"),
                    ("Collection Date/Time", obj.collectionDateTime.strftime('%d-%m-%Y %I:%M %p') if obj.collectionDateTime else "-"),
                    ("Bank", obj.bankID.name if obj.bankID else "-"),
                    ("Approved By", obj.approvedBy.name if obj.approvedBy else "-"),
                    ("Detail", obj.detail or "-"),
                    ("Remark", obj.remark or "-"),
                ]
            elif model_name == 'sales':
                title = f"Sales Invoice Details - {obj.invoiceNumber or ''}"
                detail_url = f"/sales_list/?search={obj.invoiceNumber or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Invoice Number", obj.invoiceNumber or "-"),
                    ("Payment Mode", obj.modeOfPayment),
                    ("Total Amount", f"₹ {obj.amount:.2f}"),
                    ("Taxable Amount", f"₹ {obj.taxableAmount:.2f}"),
                    ("GST Amount", f"₹ {obj.gstAmount:.2f}"),
                    ("Created By", obj.createdBy.name if obj.createdBy else "-"),
                    ("Bank", obj.bankID.name if obj.bankID else "-"),
                    ("Remark", obj.remark or "-"),
                ]
            elif model_name == 'cashcounter':
                title = f"Counter Sales Details - {obj.invoiceNumber or ''}"
                detail_url = f"/my_cash_counter_list/?search={obj.invoiceNumber or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Invoice Number", obj.invoiceNumber or "-"),
                    ("Payment Mode", obj.mode),
                    ("Amount", f"₹ {obj.amount:.2f}"),
                    ("Created By", obj.createdBy.name if obj.createdBy else "-"),
                    ("Bank", obj.bankID.name if obj.bankID else "-"),
                    ("Remark", obj.remark or "-"),
                ]
            elif model_name == 'cashcountercollection':
                title = f"Counter Collection Details - {obj.paymentID or ''}"
                detail_url = f"/my_cash_counter_list/?search={obj.paymentID or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Payment ID", obj.paymentID or "-"),
                    ("Mode of Payment", obj.modeOfPayment),
                    ("Paid Amount", f"₹ {obj.paidAmount:.2f}"),
                    ("Collected By", obj.collectedBy.name if obj.collectedBy else "-"),
                    ("Remark", obj.remark or "-"),
                ]
    elif entry.source == PartyLedgerEntry.Source.TRANSFER and entry.transferId:
        title = "Inter-Account Transfer Details"
        details = [
            ("Transfer Date", entry.txDateTime.strftime('%d-%m-%Y %I:%M %p')),
            ("Narration / Remarks", entry.particulars or "-"),
        ]

    if not details:
        details = [
            ("Transaction Date", entry.txDateTime.strftime('%d-%m-%Y %I:%M %p')),
            ("Debit", f"₹ {entry.debit:.2f}" if entry.debit else "-"),
            ("Credit", f"₹ {entry.credit:.2f}" if entry.credit else "-"),
            ("Source", entry.get_source_display()),
            ("Narration", entry.particulars or "-"),
        ]

    return JsonResponse({'message': 'success', 'title': title, 'details': details, 'detail_url': detail_url}, safe=False)

