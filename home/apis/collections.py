from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from home.audit import log_audit_event, snapshot_instance
from home.books import sync_book_transactions
from home.models import *
from home.otp import (
    OTP_TTL_SECONDS,
    clear_collection_otp,
    clear_collection_status,
    get_collection_otp_payload,
    set_collection_otp,
    set_collection_status,
)
from home.party_ledger import sync_party_ledger_entries_for_object
from home.services.messages import send_message
from home.services.reports import formatINR, xlsx_report_response as _xlsx_report_response
from home.services.sequences import next_tenant_sequence_value as _next_tenant_sequence_value
from home.services.collections import (
    BANK_REQUIRED_COLLECTION_MODES,
    CHEQUE_COLLECTION_MODES,
    can_auto_approve_collections as _can_auto_approve_collections,
    can_filter_all_collections as _can_filter_all_collections,
    can_review_collections as _can_review_collections,
    collection_datetime_with_preserved_time,
    collection_list_base_qs as _collection_list_base_qs,
    collection_payment_validation_error,
    collection_verified_label as _collection_verified_label,
    has_any_permission as _has_any_permission,
    parse_collection_form_date,
    parse_party_id_value as _parse_party_id_value,
)
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs


def request_collection_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not _has_any_permission(
        request.user,
        'home.can_approve_collection',
        'home.can_view_party_credentials',
        'home.add_collection',
    ):
        return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
    try:
        party = request.POST.get('party') or ''
        party_id = _parse_party_id_value(party)
        if not party_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required.'}, status=400)
        cus = tenant_qs(request, Party).filter(isDeleted=False, pk=party_id).only('id', 'name', 'phone', 'tenant_id').first()
        if not cus:
            return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
        payment_mode = (request.POST.get('paymentMode') or '').strip()
        amount_paid_raw = (request.POST.get('amountPaid') or '').strip()
        collect_date = (request.POST.get('collectDate') or '').strip()
        try:
            amount_paid = float(amount_paid_raw) if amount_paid_raw != '' else None
        except Exception:
            amount_paid = None
        meta = {}
        if payment_mode:
            meta['paymentMode'] = payment_mode
        if amount_paid is not None:
            meta['amountPaid'] = amount_paid
        if collect_date:
            meta['collectDate'] = collect_date
        otp = get_random_string(length=6, allowed_chars='0123456789')
        set_collection_otp(cus.tenant_id, cus.pk, otp, ttl=OTP_TTL_SECONDS, meta=meta)
        return JsonResponse({'message': 'success', 'expires_in': OTP_TTL_SECONDS}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
def verify_collection_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not _has_any_permission(
        request.user,
        'home.can_approve_collection',
        'home.can_view_party_credentials',
        'home.add_collection',
    ):
        return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
    try:
        party = request.POST.get('party') or ''
        code = (request.POST.get('otp') or '').strip()
        if not (code.isdigit() and len(code) == 6):
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        party_id = _parse_party_id_value(party)
        if not party_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required.'}, status=400)
        party_obj = tenant_qs(request, Party).filter(isDeleted=False, pk=party_id).only('id', 'tenant_id').first()
        if not party_obj:
            return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
        payload = get_collection_otp_payload(party_obj.tenant_id, party_id) or {}
        if not payload or str(payload.get('otp')) != code:
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
def clear_collection_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not _has_any_permission(
        request.user,
        'home.can_approve_collection',
        'home.can_view_party_credentials',
        'home.add_collection',
    ):
        return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
    try:
        party = request.POST.get('party') or ''
        party_id = _parse_party_id_value(party)
        if not party_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required.'}, status=400)
        party_obj = tenant_qs(request, Party).filter(isDeleted=False, pk=party_id).only('id', 'tenant_id').first()
        if not party_obj:
            return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
        clear_collection_otp(party_obj.tenant_id, party_id)
        clear_collection_status(party_obj.tenant_id, party_id)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


@transaction.atomic
def add_collection_by_staff_api(request):
    if request.method == 'POST':
        try:
            transferredParty = request.POST.get("TransferredParty")
            party = request.POST.get("party")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            otp_required = str(request.POST.get("otpRequired") or "").lower() in {"1", "true", "yes", "on"}
            otp_code = (request.POST.get("otpCode") or "").strip()
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            collectDate = request.POST.get("collectDate")
            validation_error = collection_payment_validation_error(paymentMode, bank, chequeDate)
            if validation_error:
                return JsonResponse({'message': validation_error}, safe=False)
            party_id = _parse_party_id_value(party)
            if not party_id:
                return JsonResponse({'message': 'error'}, safe=False)
            cus = tenant_qs(request, Party).select_related().get(pk=party_id)
            if otp_required:
                if not (otp_code.isdigit() and len(otp_code) == 6):
                    return JsonResponse({'message': 'otp_required'}, safe=False)
                payload = get_collection_otp_payload(cus.tenant_id, cus.pk) or {}
                if not payload or str(payload.get('otp')) != otp_code:
                    return JsonResponse({'message': 'otp_invalid'}, safe=False)
                meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
                try:
                    meta_mode = (meta.get('paymentMode') or '').strip()
                    if meta_mode and meta_mode != paymentMode:
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_amt = meta.get('amountPaid', None)
                    if meta_amt is not None:
                        if abs(float(meta_amt) - float(amountPaid)) > 0.01:
                            return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_date = (meta.get('collectDate') or '').strip()
                    if meta_date and meta_date != (collectDate or ''):
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
            obj = Collection()
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
            if otp_required:
                obj.otpVerified = True
                obj.otpVerifiedOn = timezone.now()
            try:
                # obj.collectionAddress = deduct_location_balance(lat, lng)
                obj.collectionAddress = "From Field"
            except:
                pass
            obj.save()
            obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.COLLECTION_PAYMENT)
            obj.collectionDateTime = collection_datetime_with_preserved_time(collectDate, obj.datetime)
            obj.save(update_fields=['paymentID', 'collectionDateTime', 'otpVerified', 'otpVerifiedOn', 'lastUpdatedOn'])
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            if otp_required:
                try:
                    clear_collection_otp(cus.tenant_id, cus.pk)
                except Exception:
                    pass
                try:
                    set_collection_status(
                        cus.tenant_id,
                        cus.pk,
                        status='received',
                        meta={
                            'paymentMode': paymentMode,
                            'amountPaid': float(amountPaid),
                            'collectDate': collectDate,
                            'paymentID': obj.paymentID,
                        },
                    )
                except Exception:
                    pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def add_collection_by_admin_api(request):
    if request.method == 'POST':
        try:
            transferredParty = request.POST.get("TransferredParty")
            party = request.POST.get("party")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            otp_required = str(request.POST.get("otpRequired") or "").lower() in {"1", "true", "yes", "on"}
            otp_code = (request.POST.get("otpCode") or "").strip()
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            collectDate = request.POST.get("collectDate")
            validation_error = collection_payment_validation_error(paymentMode, bank, chequeDate)
            if validation_error:
                return JsonResponse({'message': validation_error}, safe=False)
            c = str(party).split('@')
            cus = tenant_qs(request, Party).select_related().get(pk=int(c[1]))
            if otp_required:
                if not (otp_code.isdigit() and len(otp_code) == 6):
                    return JsonResponse({'message': 'otp_required'}, safe=False)
                payload = get_collection_otp_payload(cus.tenant_id, cus.pk) or {}
                if not payload or str(payload.get('otp')) != otp_code:
                    return JsonResponse({'message': 'otp_invalid'}, safe=False)
                meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
                # If OTP was generated with details, enforce that the submitted values match.
                try:
                    meta_mode = (meta.get('paymentMode') or '').strip()
                    if meta_mode and meta_mode != paymentMode:
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_amt = meta.get('amountPaid', None)
                    if meta_amt is not None:
                        if abs(float(meta_amt) - float(amountPaid)) > 0.01:
                            return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_date = (meta.get('collectDate') or '').strip()
                    if meta_date and meta_date != collectDate:
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
            obj = Collection()
            assign_tenant(obj, request)
            obj.partyID_id = cus.pk
            obj.isTallied = False
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
            if otp_required:
                obj.otpVerified = True
                obj.otpVerifiedOn = timezone.now()
            obj.collectionAddress = "From Shop."
            obj.save()
            obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.COLLECTION_PAYMENT)
            obj.collectionDateTime = collection_datetime_with_preserved_time(collectDate, obj.datetime)
            can_auto_approve = _can_auto_approve_collections(request.user)
            if can_auto_approve:
                try:
                    obj.isApproved = True
                    obj.approvedBy_id = user.pk
                    obj.approvedOn = timezone.now()
                except Exception:
                    pass
            obj.save()
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            if otp_required:
                try:
                    clear_collection_otp(cus.tenant_id, cus.pk)
                except Exception:
                    pass
                try:
                    set_collection_status(
                        cus.tenant_id,
                        cus.pk,
                        status='received',
                        meta={
                            'paymentMode': paymentMode,
                            'amountPaid': float(amountPaid),
                            'collectDate': collectDate,
                            'paymentID': obj.paymentID,
                        },
                    )
                except Exception:
                    pass
            if can_auto_approve:
                try:
                    msg = "Sir, Your Payment has been received by {} Rs.{}/- on Dt.{} ag. Ref. No. {}, Kindly confirm the same. If you have any query Please feel free to contact on this no. 7005607770. Thanks, BSS".format(
                        obj.modeOfPayment, obj.paidAmount, str(obj.collectionDateTime.strftime('%d-%m-%Y')),
                        obj.paymentID)
                    try:
                        numbers = str(obj.partyID.phone).split('.')
                        for n in numbers:
                            send_message(n, msg, obj.partyID.name)
                    except:
                        send_message(obj.partyID.phone, msg, obj.partyID.name)
                except:
                    pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def edit_collection_by_admin_api(request):
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
            obj = tenant_qs(request, Collection).get(pk=int(ID))
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
class CollectionByStaffListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'otpVerified', 'modeOfPayment', 'collectionDateTime', 'datetime',
                     'bankID.name', 'detail', 'transferredPartyID',
                     'remark',
                     ]
    def get_initial_queryset(self):
        # user = StaffUser.objects.get(user_ID__id = self.request.pk)
        # if 'Admin' in group_names:
        return _collection_list_base_qs(self.request).filter(
            isDeleted__exact=False,
            collectionDateTime__date=datetime.today().date(),
            collectedBy__user_ID_id=self.request.user.pk
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    transferredPartyID__name__icontains=search) | Q(
                    paidAmount__icontains=search) | Q(
                    modeOfPayment__icontains=search)
                | Q(bankID__name__icontains=search) | Q(detail__icontains=search) | Q(detail__icontains=search) | Q(
                    remark__icontains=search)
                | Q(collectionDateTime__icontains=search) | Q(datetime__icontains=search) | Q(
                    collectionAddress__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            # action = '''<button  data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
            #         <i class="pen icon"></i>
            #       </button>
            #       <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
            #         <i class="trash alternate icon"></i>
            #       </button>'''.format(item.pk, item.pk),
            action = '''<div class="ui tiny label">
  Denied
</div>''',
            try:
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                tparty = item.transferredPartyID.name
            except:
                tparty = '-'
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                _collection_verified_label(item.otpVerified),
                escape(item.modeOfPayment),
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(bank),
                escape(item.detail),
                escape(tparty),
                escape(item.remark),
                action,
            ])
        return json_data
class CollectionByAdminListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'action', 'otpVerified', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'isApproved', 'approvedBy.name', 'bankID.name', 'isTallied', 'detail', 'chequeDate',
                     'transferredPartyID',
                     'collectionAddress', 'remark'
                     ]
    def get_initial_queryset(self):
        approval = (self.request.GET.get("approval") or "").strip().lower()
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                collectionDateTime__date__range=[sDate.date(), eDate.date()]
            )
            if not _can_filter_all_collections(self.request.user):
                queryset = queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
                if approval == 'pending':
                    queryset = queryset.filter(isApproved=False)
                elif approval == 'approved':
                    queryset = queryset.filter(isApproved=True)
                return queryset
            if staffID and staffID != 'All':
                queryset = queryset.filter(collectedBy_id=int(staffID))
            if approval == 'pending':
                queryset = queryset.filter(isApproved=False)
            elif approval == 'approved':
                queryset = queryset.filter(isApproved=True)
            return queryset
        except:
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
            )
            if approval not in {'pending', 'approved'}:
                queryset = queryset.filter(collectionDateTime__date=datetime.today().date())
            if not _can_filter_all_collections(self.request.user):
                queryset = queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            if approval == 'pending':
                queryset = queryset.filter(isApproved=False)
            elif approval == 'approved':
                queryset = queryset.filter(isApproved=True)
            return queryset
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
            if _can_auto_approve_collections(self.request.user):
                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                   <i class="check circle icon"></i>
                  </button>
                  <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk, item.pk),
            elif _can_review_collections(self.request.user):
                action = ''' <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Cheque Date Only" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>'''.format(item.pk)
            else:
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
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                tparty = item.transferredPartyID.name
            except:
                tparty = '-'
            try:
                collectedBy = item.collectedBy.name
            except:
                collectedBy = '-'
            try:
                approvedBy = item.approvedBy.name
            except:
                approvedBy = '-'
            if item.isApproved == True:
                isApproved = '''<div class="ui tiny green label">
                  Yes
                </div>'''
            else:
                isApproved = '''<div class="ui tiny red label">
                  No
                </div>'''
            if item.isTallied == True:
                isTallied = '''<div class="ui tiny green label">
                            Yes
                          </div>'''
            else:
                isTallied = '''<div class="ui tiny red label" data-tooltip="Enable Tally" data-position="left center" data-variation="mini" style="cursor:pointer"  onclick = "confirmTallyModel('{}')">
                            No
                          </div>'''.format(item.pk)
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                action,
                _collection_verified_label(item.otpVerified),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                isApproved,
                approvedBy,
                escape(bank),
                isTallied,
                escape(item.detail),
                escape(chequeDate),
                escape(tparty),
                escape(item.collectionAddress),
                escape(item.remark),
            ])
        return json_data


def export_collection_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = _collection_list_base_qs(request).filter(isDeleted=False)
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = qs.filter(collectionDateTime__date__range=[start_date, end_date])
    if not _can_filter_all_collections(request.user):
        qs = qs.filter(collectedBy__user_ID_id=request.user.pk)
        staff_name = 'MyList'
    else:
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(collectedBy_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) |
            Q(transferredPartyID__name__icontains=search) | Q(paidAmount__icontains=search) |
            Q(modeOfPayment__icontains=search) | Q(bankID__name__icontains=search) |
            Q(chequeDate__icontains=search) | Q(detail__icontains=search) |
            Q(remark__icontains=search) | Q(collectionAddress__icontains=search) |
            Q(collectedBy__name__icontains=search) | Q(approvedBy__name__icontains=search)
        )
    rows = []
    for item in qs.order_by('-collectionDateTime', '-datetime'):
        rows.append([
            item.paymentID or '-',
            item.partyID.name if item.partyID_id else '-',
            float(item.paidAmount or 0.0),
            'Yes' if item.otpVerified else 'No',
            item.modeOfPayment or '-',
            item.collectedBy.name if item.collectedBy_id else '-',
            item.collectionDateTime.strftime('%d/%m/%Y') if item.collectionDateTime else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            'Yes' if item.isApproved else 'No',
            item.approvedBy.name if item.approvedBy_id else '-',
            item.bankID.name if item.bankID_id else '-',
            'Yes' if item.isTallied else 'No',
            item.detail or '-',
            item.chequeDate.strftime('%d/%m/%Y') if item.chequeDate else '-',
            item.transferredPartyID.name if item.transferredPartyID_id else '-',
            item.collectionAddress or '-',
            item.remark or '-',
        ])
    return _xlsx_report_response('collection-list', [{
        'title': 'Collections',
        'report_title': 'Collection List Export',
        'report_subtitle': 'Detailed filtered collection rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Collections', 'headers': ['Payment ID', 'Party', 'Amount', 'Verified', 'Mode', 'Collected By', 'Collection Date', 'Added On', 'Approved', 'Approved By', 'Bank', 'Tallied', 'Detail', 'Cheque Date', 'Transferred Party', 'Location', 'Remark'], 'rows': rows}],
    }])
class ChequeReminderCollectionListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'isApproved', 'approvedBy.name', 'bankID.name', 'detail', 'chequeDate', 'collectionAddress',
                     'remark'
                     ]
    def get_initial_queryset(self):
        due = (self.request.GET.get("due") or "").strip().lower()
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                modeOfPayment__exact='Cheque'
            )
            if due == 'overdue':
                queryset = queryset.filter(chequeDate__lt=datetime.today().date())
            else:
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                queryset = queryset.filter(chequeDate__range=[sDate.date(), eDate.date()])
            if not _can_filter_all_collections(self.request.user):
                return queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            if staffID and staffID != 'All':
                return queryset.filter(collectedBy_id=int(staffID))
            return queryset
        except:
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                modeOfPayment__exact='Cheque'
            )
            if due == 'overdue':
                queryset = queryset.filter(chequeDate__lt=datetime.today().date())
            else:
                queryset = queryset.filter(chequeDate__exact=datetime.today().date())
            if not _can_filter_all_collections(self.request.user):
                return queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
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
            if _can_auto_approve_collections(self.request.user):
                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                   <i class="check circle icon"></i>
                  </button>
                  <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk, item.pk),
            elif _can_review_collections(self.request.user):
                action = ''' <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Cheque Date Only" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                        <i class="pen icon"></i>
                      </a>'''.format(item.pk)
            else:
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
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                collectedBy = item.collectedBy.name
            except:
                collectedBy = '-'
            try:
                approvedBy = item.approvedBy.name
            except:
                approvedBy = '-'
            if item.isApproved == True:
                isApproved = '''<div class="ui tiny green label">
                  Yes
                </div>'''
            else:
                isApproved = '''<div class="ui tiny red label">
                  No
                </div>'''
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                isApproved,
                approvedBy,
                escape(bank),
                escape(item.detail),
                escape(chequeDate),
                escape(item.collectionAddress),
                escape(item.remark),
                action,
            ])
        return json_data
@transaction.atomic
def delete_collection(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, Collection).select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def approve_collection(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_review_collections(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        id = request.POST.get("userID")
        user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
        is_counter_collection = isinstance(id, str) and id.startswith('c')
        if is_counter_collection:
            obj = tenant_qs(request, CollectionCashCounter).select_related().get(pk=int(id[1:]))
        else:
            obj = tenant_qs(request, Collection).select_related().get(pk=int(id))
        obj.isApproved = True
        obj.approvedBy_id = user.pk
        obj.approvedOn = timezone.now()
        obj.save()
        log_audit_event(
            obj,
            AuditLog.Action.APPROVE,
            actor=request.user,
            changes={
                'isApproved': {'from': False, 'to': True},
                'approvedBy_id': {'from': None, 'to': user.pk},
                'approvedOn': {'from': None, 'to': obj.approvedOn.isoformat() if obj.approvedOn else None},
            },
            snapshot=snapshot_instance(obj),
        )
        try:
            sync_party_ledger_entries_for_object(obj)
        except Exception:
            pass
        try:
            msg = "Sir, Your Payment has been received by {} Rs.{}/- on Dt.{} ag. Ref. No. {}, Kindly confirm the same. If you have any query Please feel free to contact on this no. 7005607770. Thanks, BSS".format(
                obj.modeOfPayment, obj.paidAmount, str(obj.collectionDateTime.strftime('%d-%m-%Y')), obj.paymentID)
            try:
                numbers = str(obj.partyID.phone).split('.')
                for n in numbers:
                    send_message(n, msg, obj.partyID.name)
            except:
                send_message(obj.partyID.phone, msg, obj.partyID.name)
        except:
            pass
        return JsonResponse({'message': 'success'}, safe=False)
    except:
        return JsonResponse({'message': 'error'}, safe=False)
