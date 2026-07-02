from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from home.books import sync_book_transactions
from home.models import *
from home.party_ledger import sync_party_ledger_entries_for_object
from home.services.cash_counter import (
    can_filter_all_cash_counter as _can_filter_all_cash_counter,
    can_manage_cash_counter_records as _can_manage_cash_counter_records,
    cash_counter_collection_list_base_qs as _cash_counter_collection_list_base_qs,
)
from home.services.collections import (
    BANK_REQUIRED_COLLECTION_MODES,
    CHEQUE_COLLECTION_MODES,
    can_review_collections as _can_review_collections,
    collection_datetime_with_preserved_time,
    collection_payment_validation_error,
    parse_collection_form_date,
)
from home.services.reports import formatINR
from home.services.sequences import next_tenant_sequence_value as _next_tenant_sequence_value
from home.tenancy import assign_tenant, tenant_qs


@transaction.atomic
def add_collection_from_cash_counter_api(request):
    if request.method == 'POST':
        try:
            transferredParty = request.POST.get("TransferredParty")
            party = request.POST.get("party")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            validation_error = collection_payment_validation_error(paymentMode, bank, chequeDate)
            if validation_error:
                return JsonResponse({'message': validation_error}, safe=False)
            c = str(party).split('@')
            cus = tenant_qs(request, Party).select_related().get(pk=int(c[1]))
            obj = CollectionCashCounter()
            assign_tenant(obj, request)
            obj.isTallied = False
            obj.partyID_id = cus.pk
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            if paymentMode in BANK_REQUIRED_COLLECTION_MODES:
                obj.bankID_id = int(bank)
            else:
                obj.bankID = None
            if paymentMode in CHEQUE_COLLECTION_MODES:
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = tenant_qs(request, Party).select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass
            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk
            obj.isApproved = True
            obj.approvedBy_id = user.pk
            obj.approvedOn = timezone.now()
            try:
                # obj.collectionAddress = deduct_location_balance(lat, lng)
                obj.collectionAddress = "From Shop Counter"
            except:
                pass
            obj.save()
            obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.CASH_COUNTER_COLLECTION)
            obj.collectionDateTime = obj.datetime
            obj.save()
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
class CashCounterCollectionListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'bankID.name', 'detail', 'chequeDate',
                     'transferredPartyID',
                     'remark', 'action'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False)
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(collectedBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                             collectionDateTime__date__range=[
                                                                                 sDate.date(),
                                                                                 eDate.date()])
            except:
                return _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                             datetime__date=datetime.today().date())
        else:
            return _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                         datetime__date=datetime.today().date(),
                                                                         collectedBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    transferredPartyID__name__icontains=search) | Q(
                    paidAmount__icontains=search) | Q(
                    modeOfPayment__icontains=search)
                | Q(bankID__name__icontains=search) | Q(chequeDate__icontains=search) | Q(detail__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search) | Q(collectionDateTime__icontains=search) | Q(
                    collectionAddress__icontains=search) | Q(
                    collectedBy__name__icontains=search)
                | Q(approvedBy__name__icontains=search) | Q(isApproved__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action_parts = []
            if _can_review_collections(self.request.user) and not item.isApproved:
                action_parts.append('''<button data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showConfirmationModal('c{}')" class="ui circular facebook icon button purple"><i class="check circle icon"></i></button>'''.format(item.pk))
            if _can_manage_cash_counter_records(self.request.user):
                action_parts.append('''<a href="/edit_cash_counter_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delUser('c{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            action = ''.join(action_parts)
            if not action:
                action = '''<div class="ui tiny label">
                  Denied
                </div>'''
            if item.modeOfPayment == 'Cheque':
                try:
                    chequeDate = item.chequeDate.strftime('%d-%m-%Y')
                except:
                    chequeDate = '-'
            else:
                chequeDate = '-'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID_id}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = '-'
            try:
                bank = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('bank', '{item.bankID_id}')" style="cursor: pointer;">{escape(item.bankID.name)}</span>'''
            except:
                bank = '-'
            try:
                tparty = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.transferredPartyID_id}')" style="cursor: pointer;">{escape(item.transferredPartyID.name)}</span>'''
            except:
                tparty = '-'
            try:
                collectedBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.collectedBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.collectedBy.name)}</span>'''
            except:
                collectedBy = '-'
            json_data.append([
                escape(item.paymentID),
                party,
                formatINR(format(item.paidAmount, '.0f')),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                bank,
                escape(item.detail),
                escape(chequeDate),
                tparty,
                escape(item.remark),
                action,
            ])
        return json_data
@transaction.atomic
def edit_cash_counter_collection_by_admin_api(request):
    if request.method == 'POST':
        try:
            ID = request.POST.get("ID")
            cDate = request.POST.get("cDate")
            party = request.POST.get("party")
            transferredParty = request.POST.get("TransferredParty")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            # c = str(party).split('@')
            chequeDate = request.POST.get("chequeDate")
            validation_error = collection_payment_validation_error(paymentMode, bank, chequeDate)
            if validation_error:
                return JsonResponse({'message': validation_error}, safe=False)
            # cus = Party.objects.select_related().get(pk=int(c[1]))
            cus = tenant_qs(request, Party).select_related().get(pk=int(party))
            obj = tenant_qs(request, CollectionCashCounter).get(pk=int(ID))
            if _can_review_collections(request.user):
                obj.partyID_id = cus.pk
                obj.modeOfPayment = paymentMode
                obj.paidAmount = float(amountPaid)
                obj.detail = detail
                obj.remark = remark
                obj.transferredPartyID = None
                if cDate and parse_collection_form_date(cDate) is None:
                    obj.collectionDateTime = datetime.strptime(cDate, '%d/%m/%Y')
                else:
                    obj.collectionDateTime = collection_datetime_with_preserved_time(
                        cDate,
                        obj.collectionDateTime or obj.datetime,
                    )
                if paymentMode in BANK_REQUIRED_COLLECTION_MODES:
                    obj.bankID_id = int(bank)
                else:
                    obj.bankID = None
                if paymentMode in CHEQUE_COLLECTION_MODES:
                    obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
                else:
                    obj.chequeDate = None
                if paymentMode == 'Party':
                    try:
                        tparty = tenant_qs(request, Party).select_related().get(pk=int(transferredParty))
                        obj.transferredPartyID_id = tparty.pk
                    except:
                        pass
                obj.save()
                sync_book_transactions(obj)
                sync_party_ledger_entries_for_object(obj)
            # if 'Moderator' in request.user.groups.values_list('name', flat=True):
            #     if paymentMode == 'Cheque':
            #         obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            #
            #     obj.save()
            # try:
            #     msg = "Sir, Our Executive has collected the payment of {} Rs.{}/- from you, Kindly confirm the same. If you have any query Please feel free contact on this no. 7005607770. Thanks, BSS".format(
            #         obj.modeOfPayment, obj.paidAmount)
            #     send_whatsapp_message(obj.partyID.phone, msg)
            # except:
            #     pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)

