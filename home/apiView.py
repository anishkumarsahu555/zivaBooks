import threading
from datetime import datetime, timedelta

import requests
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView
from weasyprint import CSS, HTML

from home.models import *

BASE_URL_WHATSAPP = "https://bot.betablaster.in/api/"


class LocationThread(threading.Thread):
    def __init__(self, lat, lng):
        self.lat = lat
        self.lng = lng
        self._is_running = True
        threading.Thread.__init__(self)

    def run(self):
        while (self._is_running):
            location = GeolocationPackage.objects.filter(isDeleted__exact=False).last()
            if location.used < location.balance:

                r = requests.get(
                    "https://revgeocode.search.hereapi.com/v1/revgeocode?at=" + self.lat + "%2C" + self.lng + "&lang=en-US&apikey=VtAAEVuyqdswCZr8X1BdBzM-4JTpeAdivmp4_3ILPs4")
                data = r.json()
                location.used = (location.used + 1)
                location.save()
                return data["items"][0]["address"]["label"]
            else:
                return ""

        self.stop()

    def stop(self):
        self._is_running = False


def get_location(lat, lng):
    LocationThread(lat, lng).start()


class MessageThread(threading.Thread):
    def __init__(self, number, message, partyName):
        self.number = number
        self.message = message
        self.partyName = partyName
        self._is_running = True
        threading.Thread.__init__(self)

    def run(self):
        while (self._is_running):
            msg = WhatsappMessage.objects.filter(isDeleted__exact=False).last()
            if msg.used < msg.balance:
                try:

                    # r = requests.get(
                    #     msg.name + "send?number=91" + self.number + "&type=text&message=" + self.message + "&instance_id=" + msg.instanceID + "&access_token=" + msg.apiKey,
                    #     verify=False)
                    # data = r.json()
                    obj = WhatsappMessageStatus()
                    obj.phone = self.number
                    obj.message = self.message
                    obj.messageTo = self.partyName
                    try:
                        # if data['status'] == 'success':
                        #     obj.status = 'Success'
                        # else:
                        obj.status = 'Fail'
                    except:
                        obj.status = 'Fail'
                    obj.save()

                    msg.used = (msg.used + 1)
                    msg.save()
                except:
                    obj = WhatsappMessageStatus()
                    obj.phone = self.number
                    obj.message = self.message
                    obj.messageTo = self.partyName
                    obj.status = 'Fail'
                    obj.save()

            else:
                obj = WhatsappMessageStatus()
                obj.phone = self.number
                obj.message = self.message
                obj.messageTo = self.partyName
                obj.status = 'Fail'
                obj.save()

            self.stop()

    def stop(self):
        self._is_running = False


def send_message(number, message, party):
    MessageThread(number, message, party).start()


def formatINR(number):
    s, *d = str(number).partition(".")
    r = ",".join([s[x - 2:x] for x in range(-3, -len(s), -2)][::-1] + [s[-3:]])
    return "".join([r] + d)


def _collection_list_base_qs():
    return Collection.objects.select_related(
        'partyID', 'transferredPartyID', 'bankID', 'collectedBy', 'approvedBy'
    ).only(
        'id', 'paymentID', 'paidAmount', 'modeOfPayment', 'detail', 'remark', 'collectionAddress',
        'collectionDateTime', 'datetime', 'isApproved', 'isTallied', 'chequeDate',
        'partyID__name', 'transferredPartyID__name', 'bankID__name',
        'collectedBy__name', 'approvedBy__name'
    )


def _sales_list_base_qs():
    return Sales.objects.select_related('partyID', 'createdBy', 'approvedBy').only(
        'id', 'paymentID', 'invoiceNumber', 'amount', 'buildDate', 'datetime', 'remark', 'isApproved',
        'partyID__name', 'createdBy__name', 'approvedBy__name'
    )


def _attendance_list_base_qs():
    return Attendance.objects.select_related('staffID').only(
        'id', 'datetime', 'isLogIn', 'isLogOut', 'loginDateTime', 'logoutDateTime',
        'login_remark', 'logout_remark', 'login_location', 'logout_location', 'staffID__name'
    )


def _cash_counter_list_base_qs():
    return CashCounter.objects.select_related('partyID', 'bankID', 'createdBy').only(
        'id', 'counterID', 'invoiceNumber', 'amount', 'mixCashAmount', 'mixCardAmount', 'mode',
        'expenseType', 'remark', 'datetime', 'partyID__name', 'bankID__name',
        'bankID__accountNumber', 'createdBy__name'
    )


def _cash_counter_collection_list_base_qs():
    return CollectionCashCounter.objects.select_related(
        'partyID', 'transferredPartyID', 'bankID', 'collectedBy', 'approvedBy'
    ).only(
        'id', 'paymentID', 'paidAmount', 'modeOfPayment', 'detail', 'remark', 'collectionAddress',
        'collectionDateTime', 'datetime', 'isApproved', 'chequeDate',
        'partyID__name', 'transferredPartyID__name', 'bankID__name', 'collectedBy__name', 'approvedBy__name'
    )


def _request_group_names(request):
    if not hasattr(request, "_group_name_cache"):
        request._group_name_cache = set(request.user.groups.values_list('name', flat=True))
    return request._group_name_cache


def _get_party_cache_version():
    version = cache.get('party_list_cache_version')
    if version is None:
        version = 1
        cache.set('party_list_cache_version', version, timeout=None)
    return version


def _bump_party_cache_version():
    version = cache.get('party_list_cache_version') or 1
    cache.set('party_list_cache_version', version + 1, timeout=None)


def deduct_location_balance(lat, lng):
    location = GeolocationPackage.objects.filter(isDeleted__exact=False).last()
    if location.used < location.balance:

        r = requests.get(
            "https://revgeocode.search.hereapi.com/v1/revgeocode?at=" + lat + "%2C" + lng + "&lang=en-US&apikey=VtAAEVuyqdswCZr8X1BdBzM-4JTpeAdivmp4_3ILPs4")
        data = r.json()
        location.used = (location.used + 1)
        location.save()
        return data["items"][0]["address"]["label"]
    else:
        return ""


def send_whatsapp_message(number, message):
    msg = WhatsappMessage.objects.filter(isDeleted__exact=False).last()
    if msg.used < msg.balance:
        # r = requests.get(
        #     msg.name + "send?number=91" + number + "&type=text&message=" + message + "&instance_id=" + msg.instanceID + "&access_token=" + msg.apiKey,
        #     verify=False)
        # data = r.json()
        msg.used = (msg.used + 1)
        msg.save()


#
@transaction.atomic
@csrf_exempt
def add_staff_api(request):
    if request.method == 'POST':
        try:
            CompanyUserName = request.POST.get("CompanyUserName")
            UserPhoneNo = request.POST.get("UserPhoneNo")
            UserEmail = request.POST.get("UserEmail")
            UserAddress = request.POST.get("UserAddress")
            UserGroup = request.POST.get("UserGroup")
            UserStatus = request.POST.get("UserStatus")
            UserPwd = request.POST.get("UserPwd")
            # PartyGroup = request.POST.get("PartyGroup")
            imageUpload = request.FILES["imageUpload"]
            imageUploadID = request.FILES["imageUploadID"]

            staff = StaffUser()
            staff.photo = imageUpload
            staff.idProof = imageUploadID
            staff.name = CompanyUserName
            staff.phone = UserPhoneNo
            staff.email = UserEmail
            staff.address = UserAddress
            staff.group = UserGroup
            staff.isActive = UserStatus
            staff.userPassword = UserPwd
            # try:
            #     staff.partyGroupID_id = int(PartyGroup)
            # except:
            #     pass
            staff.save()
            username = 'USER' + get_random_string(length=3, allowed_chars='1234567890')
            while User.objects.select_related().filter(username__exact=username).count() > 0:
                username = 'USER' + get_random_string(length=5, allowed_chars='1234567890')
            else:
                new_user = User()
                new_user.username = username
                new_user.set_password(UserPwd)

                new_user.save()
                staff.username = username
                staff.user_ID_id = new_user.pk

                staff.save()

                try:
                    g = Group.objects.get(name=UserGroup)
                    g.user_set.add(new_user.pk)
                    g.save()

                except:
                    g = Group()
                    g.name = UserGroup
                    g.save()
                    g.user_set.add(new_user.pk)
                    g.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


#
class StaffUserListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'username', 'userPassword', 'group', 'phone', 'address',
                     'isActive', 'datetime']

    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        return StaffUser.objects.filter(isDeleted__exact=False).only(
            'id', 'photo', 'name', 'username', 'userPassword', 'group', 'phone',
            'address', 'isActive', 'datetime'
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(username__icontains=search)
                | Q(group__icontains=search) | Q(phone__icontains=search)
                | Q(address__icontains=search) | Q(isActive__icontains=search)
                | Q(datetime__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            images = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)
            if 'Admin' in group_names:
                action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>
                  <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button></td>'''.format(item.pk, item.pk),
                username = item.username
                password = item.userPassword
            else:
                action = '''<div class="ui tiny label">
                  Denied
                </div>'''
                username = "*********"
                password = "*********"

            json_data.append([
                images,  # escape HTML for security reasons
                escape(item.name),
                username,
                password,
                escape(item.group),
                escape(item.phone),
                escape(item.address),
                escape(item.isActive),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
def delete_staff_user(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            staff = StaffUser.objects.select_related().get(pk=int(id))
            staff.isDeleted = True
            staff.save()
            new_user = User.objects.select_related().get(pk=staff.user_ID_id)
            new_user.is_active = False
            new_user.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


def get_staff_user_detail(request):
    id = request.GET.get('id')
    C_User = get_object_or_404(StaffUser, id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)
    try:
        party = C_User.partyGroupID.id
    except:
        party = 'N/A'
    data = {
        'ID': C_User.pk,
        'UserName': C_User.name,
        'UserPhone': C_User.phone,
        'UserAddress': C_User.address,
        'UserEmail': C_User.email,
        'UserPassword': C_User.userPassword,
        'UserGroup': C_User.group,
        'PartyGroup': party,
        'IsActive': C_User.isActive,
        'ImgUrl': C_User.photo.medium.url,
        'IDUrl': C_User.idProof.medium.url

    }
    return JsonResponse({'data': data}, safe=False)


@transaction.atomic
@csrf_exempt
def edit_staff_api(request):
    if request.method == 'POST':
        try:
            EditUserId = request.POST.get("EditUserId")
            CompanyUserName = request.POST.get("CompanyUserName")
            UserPhoneNo = request.POST.get("UserPhoneNo")
            UserEmail = request.POST.get("UserEmail")
            UserAddress = request.POST.get("UserAddress")
            UserGroup = request.POST.get("UserGroup")
            UserStatus = request.POST.get("UserStatus")
            UserPwd = request.POST.get("UserPwd")
            # PartyGroup = request.POST.get("PartyGroup")

            staff = StaffUser.objects.select_related().get(pk=int(EditUserId))
            try:
                imageUpload = request.FILES["imageUpload"]
                staff.photo = imageUpload
            except:
                pass
            try:
                imageUploadID = request.FILES["imageUploadID"]
                staff.idProof = imageUploadID
            except:
                pass
            staff.name = CompanyUserName
            staff.phone = UserPhoneNo
            staff.email = UserEmail
            staff.address = UserAddress
            staff.group = UserGroup
            # try:
            #     staff.partyGroupID_id = int
            #     (PartyGroup)
            # except:
            #     pass
            staff.isActive = UserStatus
            staff.userPassword = UserPwd
            staff.save()

            new_user = User.objects.select_related().get(pk=staff.user_ID_id)
            new_user.set_password(UserPwd)
            if UserStatus == 'Active':
                new_user.is_active = True
            else:
                new_user.is_active = False
            new_user.save()
            new_user.groups.clear()
            try:
                g = Group.objects.get(name=UserGroup)
                g.user_set.add(new_user.pk)
                g.save()

            except:
                g = Group()
                g.name = UserGroup
                g.save()
                g.user_set.add(new_user.pk)
                g.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


#
# # ---------------------------------Party Groups ----------------------------------------------------
#
@transaction.atomic
@csrf_exempt
def add_party_group_api(request):
    if request.method == 'POST':
        try:
            groupName = request.POST.get("groupName")
            description = request.POST.get("description")

            obj = PartyGroup()
            obj.name = groupName
            obj.description = description
            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


#
@transaction.atomic
@csrf_exempt
def delete_part_group(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = PartyGroup.objects.select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


class PartyGroupListJson(BaseDatatableView):
    order_columns = ['name', 'description', 'datetime']

    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        return PartyGroup.objects.filter(isDeleted__exact=False).only(
            'id', 'name', 'description', 'datetime'
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
                | Q(datetime__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:
                action = '''<button  data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button></td>'''.format(item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
                  Denied
                </div>'''
            json_data.append([
                escape(item.name),
                escape(item.description),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


def get_party_group_detail(request):
    id = request.GET.get('id')
    instance = get_object_or_404(PartyGroup, id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)

    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'Description': instance.description,
    }
    return JsonResponse({'data': data}, safe=False)


#
@transaction.atomic
@csrf_exempt
def edit_part_group_api(request):
    if request.method == 'POST':
        try:
            Id = request.POST.get("EditId")
            name = request.POST.get("groupName")
            description = request.POST.get("description")
            obj = PartyGroup.objects.select_related().get(id=int(Id))
            obj.name = name
            obj.description = description

            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


# # ----------------------------------Banks------------------------
@transaction.atomic
@csrf_exempt
def add_bank_api(request):
    if request.method == 'POST':
        try:
            bankName = request.POST.get("bankName")
            accountNumber = request.POST.get("accountNumber")
            description = request.POST.get("description")

            obj = Bank()
            obj.name = bankName
            obj.accountNumber = accountNumber
            obj.description = description
            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


class BankListJson(BaseDatatableView):
    order_columns = ['name', 'accountNumber', 'description', 'datetime']

    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        return Bank.objects.filter(isDeleted__exact=False).only(
            'id', 'name', 'accountNumber', 'description', 'datetime'
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(accountNumber__icontains=search) | Q(description__icontains=search)
                | Q(datetime__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''<button  data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button></td>'''.format(item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
                  Denied
                </div>'''
            json_data.append([
                escape(item.name),
                escape(item.accountNumber),
                escape(item.description),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


def get_bank_detail(request):
    id = request.GET.get('id')
    instance = get_object_or_404(Bank, id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)

    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'AccountNumber': instance.accountNumber,
        'Description': instance.description,
    }
    return JsonResponse({'data': data}, safe=False)


@transaction.atomic
@csrf_exempt
def edit_bank_api(request):
    if request.method == 'POST':
        try:
            Id = request.POST.get("EditId")
            name = request.POST.get("bankName")
            accountNumber = request.POST.get("accountNumber")
            description = request.POST.get("description")
            obj = Bank.objects.select_related().get(id=int(Id))
            obj.name = name
            obj.accountNumber = accountNumber
            obj.description = description

            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def delete_bank(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = Bank.objects.select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


# ----------------------------- Party ---------------------
@transaction.atomic
@csrf_exempt
def add_party_api(request):
    if request.method == 'POST':
        try:
            partyName = request.POST.get("partyName")
            phoneNumber = request.POST.get("phoneNumber")
            partyGroup = request.POST.get("partyGroup")
            # address = request.POST.get("address")
            staffID = request.POST.get("staffID")

            obj = Party()
            obj.name = partyName
            obj.phone = phoneNumber
            try:
                obj.assignTo_id = int(staffID)
            except:
                pass
            # obj.address = address
            try:
                obj.partyGroupID_id = int(partyGroup)
            except:
                pass

            username = get_random_string(length=6, allowed_chars='0123456789')
            while User.objects.filter(username__exact=username).exists():
                username = get_random_string(length=6, allowed_chars='0123456789')

            password = get_random_string(length=6, allowed_chars='0123456789')

            new_user = User()
            new_user.username = username
            new_user.set_password(password)
            new_user.save()

            group, _ = Group.objects.get_or_create(name='Customer')
            group.user_set.add(new_user.pk)

            obj.username = username
            obj.password = password
            obj.userID_id = new_user.pk
            obj.save()
            _bump_party_cache_version()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def generate_party_users_api(request):
    if request.method == 'GET':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)

        if not _request_group_names(request).intersection({'Admin', 'Moderator'}):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

        try:
            customer_group, _ = Group.objects.get_or_create(name='Customer')
            created = 0
            skipped = 0

            parties = Party.objects.filter(isDeleted__exact=False).order_by('id')
            for party in parties:
                if party.userID_id:
                    skipped = skipped + 1
                    continue

                username = get_random_string(length=6, allowed_chars='0123456789')
                while User.objects.filter(username__exact=username).exists():
                    username = get_random_string(length=6, allowed_chars='0123456789')
                password = get_random_string(length=6, allowed_chars='0123456789')

                new_user = User()
                new_user.username = username
                new_user.set_password(password)
                new_user.save()

                customer_group.user_set.add(new_user.pk)

                party.username = username
                party.password = password
                party.userID_id = new_user.pk
                party.save(update_fields=['username', 'password', 'userID'])
                created = created + 1
            if created > 0:
                _bump_party_cache_version()

            return JsonResponse({
                'message': 'success',
                'created': created,
                'skipped': skipped,
            }, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


class PartyListJson(BaseDatatableView):
    order_columns = ['name', 'phone', 'username', 'password', 'partyGroupID.name', 'assignTo.name', 'datetime']

    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        return Party.objects.select_related('partyGroupID', 'assignTo').filter(isDeleted__exact=False).only(
            'id', 'name', 'phone', 'username', 'password', 'address', 'datetime',
            'partyGroupID__name', 'assignTo__name'
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(phone__icontains=search) | Q(username__icontains=search)
                | Q(password__icontains=search) | Q(address__icontains=search)
                | Q(partyGroupID__name__icontains=search) | Q(assignTo__name__icontains=search) | Q(
                    datetime__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''<button  data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button></td>'''.format(item.pk, item.pk),

            else:
                action = '''<button  data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>'''.format(item.pk)
            try:
                party = item.partyGroupID.name
            except:
                party = 'N/A'
            try:
                usr = item.assignTo.name
            except:
                usr = 'N/A'

            if item.username:
                username = '''{} <button data-copy="{}" class="ui mini icon button copy-btn" type="button" title="Copy Username"><i class="copy outline icon"></i></button>'''.format(
                    escape(item.username),
                    escape(item.username)
                )
            else:
                username = 'N/A'

            if item.password:
                password = '''{} <button data-copy="{}" class="ui mini icon button copy-btn" type="button" title="Copy Password"><i class="copy outline icon"></i></button>'''.format(
                    escape(item.password),
                    escape(item.password)
                )
            else:
                password = 'N/A'
            json_data.append([
                escape(item.name),
                escape(item.phone),
                username,
                password,
                party,
                usr,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


def get_party_detail(request):
    id = request.GET.get('id')
    instance = get_object_or_404(Party, id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)

    try:
        party = instance.partyGroupID.pk
    except:
        party = 'N/A'

    try:
        usr = instance.assignTo.pk
    except:
        usr = 'N/A'
    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'PhoneNumber': instance.phone,
        'Address': instance.address,
        'PartyGroup': party,
        'AssignTo': usr,
    }
    return JsonResponse({'data': data}, safe=False)


@transaction.atomic
@csrf_exempt
def edit_party_api(request):
    if request.method == 'POST':
        try:
            Id = request.POST.get("EditId")
            name = request.POST.get("partyName")
            phoneNumber = request.POST.get("phoneNumber")
            # address = request.POST.get("address")
            partyGroup = request.POST.get("partyGroup")
            staffID = request.POST.get("staffID")
            obj = Party.objects.select_related().get(id=int(Id))
            obj.name = name
            obj.phone = phoneNumber
            # obj.address = address
            try:
                obj.partyGroupID_id = int(partyGroup)
            except:
                pass
            try:
                obj.assignTo_id = int(staffID)
            except:
                pass
            obj.save()
            _bump_party_cache_version()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def delete_party(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = Party.objects.select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            _bump_party_cache_version()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


def list_party_api(request):
    try:
        version = _get_party_cache_version()
        cache_key = f'PartyList:v{version}:all'
        o_list = cache.get(cache_key)
        if o_list is None:
            o_list = []
            obj_list = Party.objects.select_related('partyGroupID').filter(isDeleted__exact=False).only(
                'id', 'name', 'phone', 'partyGroupID__name'
            ).order_by('name')

            for obj in obj_list:
                party = obj.partyGroupID.name if obj.partyGroupID else 'N/A'
                obj_dic = {
                    'ID': obj.pk,
                    'Name': obj.name,
                    'PartyGroup': party,
                    'Phone': obj.phone,
                    'Detail': obj.name + ' - ' + party + ' @ ' + str(obj.pk),
                    'DisplayDetail': obj.name + ' - ' + party
                }
                o_list.append(obj_dic)
            cache.set(cache_key, o_list, timeout=60 * 30)

        return JsonResponse({'message': 'success', 'data': o_list}, safe=False)
    except:
        return JsonResponse({'message': 'error'}, safe=False)


def list_party_by_executive_or_station_api(request):
    try:
        executive = request.GET.get('executive')
        station = request.GET.get('station')
        o_list = []
        version = _get_party_cache_version()
        cache_key = f'PartyList:v{version}:exec:{executive}:station:{station}'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse({'message': 'success', 'data': cached}, safe=False)

        obj_list = Party.objects.select_related('partyGroupID').filter(
            isDeleted__exact=False
        ).only(
            'id', 'name', 'phone', 'partyGroupID__name'
        )

        if executive != 'All' and station == 'All':
            obj_list = obj_list.filter(assignTo__id=int(executive))
        elif executive == 'All' and station != 'All':
            obj_list = obj_list.filter(partyGroupID__id=int(station))
        elif executive != 'All' and station != 'All':
            obj_list = obj_list.filter(assignTo__id=int(executive), partyGroupID__id=int(station))

        obj_list = obj_list.order_by('name')
        for obj in obj_list:
            try:
                party = obj.partyGroupID.name
            except:
                party = 'N/A'
            obj_dic = {
                'ID': obj.pk,
                'Name': obj.name,
                'PartyGroup': party,
                'Phone': obj.phone,
                'Detail': obj.name + ' - ' + party + ' @ ' + str(obj.pk),
                'DisplayDetail': obj.name + ' - ' + party
            }
            o_list.append(obj_dic)
        cache.set(cache_key, o_list, timeout=60 * 15)

        return JsonResponse({'message': 'success', 'data': o_list}, safe=False)
    except:
        return JsonResponse({'message': 'error'}, safe=False)


# ------------------------------Collection ----------------------------------------
# ----------------------------- Party ---------------------
@transaction.atomic
@csrf_exempt
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
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            c = str(party).split('@')
            cus = Party.objects.select_related().get(pk=int(c[1]))
            obj = Collection()
            obj.isTallied = False
            obj.partyID_id = cus.pk
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            try:
                obj.bankID_id = int(bank)
            except:
                pass
            if paymentMode == 'Cheque':
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = Party.objects.select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass

            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk
            try:
                # obj.collectionAddress = deduct_location_balance(lat, lng)
                obj.collectionAddress = "From Field"
            except:
                pass

            obj.save()
            obj.paymentID = str(obj.pk).zfill(8)
            obj.collectionDateTime = obj.datetime
            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
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
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            collectDate = request.POST.get("collectDate")
            c = str(party).split('@')
            cus = Party.objects.select_related().get(pk=int(c[1]))
            obj = Collection()
            obj.partyID_id = cus.pk
            obj.isTallied = False
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            try:
                obj.bankID_id = int(bank)
            except:
                pass
            if paymentMode == 'Cheque':
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = Party.objects.select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass

            obj.collectionDateTime = datetime.strptime(collectDate, '%d/%m/%Y')
            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk

            obj.collectionAddress = "From Shop."

            obj.save()
            obj.paymentID = str(obj.pk).zfill(8)
            # obj.collectionDateTime = obj.datetime
            obj.save()
            if 'Admin' in _request_group_names(request):
                try:
                    obj.isApproved = True
                    obj.approvedBy_id = user.pk
                    # msg = "Sir, Our Executive has collected the payment of {} Rs.{}/- from you with Ref. No. {}, Kindly confirm the same. If you have any query Please feel free contact on this no. 7005607770. Thanks, BSS".format(
                    #     obj.modeOfPayment, obj.paidAmount, obj.paymentID)
                    msg = "Sir, Your Payment has been received by {} Rs.{}/- on Dt.{} ag. Ref. No. {}, Kindly confirm the same. If you have any query Please feel free to contact on this no. 7005607770. Thanks, BSS".format(
                        obj.modeOfPayment, obj.paidAmount, str(obj.collectionDateTime.strftime('%d-%m-%Y')),
                        obj.paymentID)
                    # send_whatsapp_message(obj.partyID.phone, msg)
                    # send_message(obj.partyID.phone, msg, obj.partyID.name)
                    try:
                        numbers = str(obj.partyID.phone).split('.')
                        for n in numbers:
                            send_message(n, msg, obj.partyID.name)
                    except:
                        send_message(obj.partyID.phone, msg, obj.partyID.name)
                    obj.save()
                except:
                    pass
            else:
                pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
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
            # cus = Party.objects.select_related().get(pk=int(c[1]))
            cus = Party.objects.select_related().get(pk=int(party))

            obj = Collection.objects.get(pk=int(ID))
            request_groups = _request_group_names(request)
            if 'Admin' in request_groups or 'Moderator' in request_groups:
                obj.partyID_id = cus.pk
                obj.modeOfPayment = paymentMode
                obj.paidAmount = float(amountPaid)
                obj.detail = detail
                obj.remark = remark
                obj.transferredPartyID = None
                obj.collectionDateTime = datetime.strptime(cDate, '%d/%m/%Y')
                try:
                    obj.bankID_id = int(bank)
                except:
                    pass
                if paymentMode == 'Cheque':
                    obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
                if paymentMode == 'Party':
                    try:
                        tparty = Party.objects.select_related().get(pk=int(transferredParty))
                        obj.transferredPartyID_id = tparty.pk
                    except:
                        pass
                obj.save()
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
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'modeOfPayment', 'collectionDateTime', 'datetime',
                     'bankID.name', 'detail', 'transferredPartyID',
                     'remark',
                     ]

    def get_initial_queryset(self):
        # user = StaffUser.objects.get(user_ID__id = self.request.pk)
        # if 'Admin' in group_names:
        return _collection_list_base_qs().filter(
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
        group_names = _request_group_names(self.request)
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
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'action', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'isApproved', 'approvedBy.name', 'bankID.name', 'isTallied', 'detail', 'chequeDate',
                     'transferredPartyID',
                     'collectionAddress', 'remark'
                     ]

    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')

            if staffID == 'All':
                return _collection_list_base_qs().filter(
                    isDeleted__exact=False,
                    collectionDateTime__date__range=[sDate.date(), eDate.date()]
                )
            else:
                return _collection_list_base_qs().filter(
                    isDeleted__exact=False,
                    collectionDateTime__date__range=[sDate.date(), eDate.date()],
                    collectedBy_id=int(staffID)
                )
        except:
            return _collection_list_base_qs().filter(
                isDeleted__exact=False,
                collectionDateTime__date=datetime.today().date()
            )

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
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                   <i class="whatsapp icon"></i>
                  </button>
                  <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = ''' <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Cheque Date Only" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>'''.format(item.pk)
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


class ChequeReminderCollectionListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'isApproved', 'approvedBy.name', 'bankID.name', 'detail', 'chequeDate', 'collectionAddress',
                     'remark'
                     ]

    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')

            if staffID == 'All':
                return _collection_list_base_qs().filter(
                    isDeleted__exact=False,
                    chequeDate__range=[sDate.date(), eDate.date()],
                    modeOfPayment__exact='Cheque'
                )
            else:
                return _collection_list_base_qs().filter(
                    isDeleted__exact=False,
                    chequeDate__range=[sDate.date(), eDate.date()],
                    collectedBy_id=int(staffID),
                    modeOfPayment__exact='Cheque'
                )
        except:
            return _collection_list_base_qs().filter(
                isDeleted__exact=False,
                chequeDate__exact=datetime.today().date(),
                modeOfPayment__exact='Cheque'
            )

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
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                   <i class="whatsapp icon"></i>
                  </button>
                  <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = ''' <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Cheque Date Only" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                        <i class="pen icon"></i>
                      </a>'''.format(item.pk)
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
@csrf_exempt
def delete_collection(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = Collection.objects.select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def approve_collection(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = Collection.objects.select_related().get(pk=int(id))
            user = StaffUser.objects.get(user_ID__id=request.user.pk)
            obj.isApproved = True
            obj.approvedBy_id = user.pk
            obj.save()
            try:
                msg = "Sir, Your Payment has been received by {} Rs.{}/- on Dt.{} ag. Ref. No. {}, Kindly confirm the same. If you have any query Please feel free to contact on this no. 7005607770. Thanks, BSS".format(
                    obj.modeOfPayment, obj.paidAmount, str(obj.collectionDateTime.strftime('%d-%m-%Y')), obj.paymentID)

                # send_whatsapp_message(obj.partyID.phone, msg)
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
def change_password_api(request):
    if request.method == 'POST':
        try:
            password = request.POST.get('password')
            data = StaffUser.objects.select_related().get(user_ID_id=request.user.pk)
            data.userPassword = password
            data.save()
            user = User.objects.select_related().get(pk=request.user.pk)
            user.set_password(password)
            user.save()
            user = authenticate(request, username=user.username, password=password)
            if user is not None:
                login(request, user)
                return JsonResponse({'message': 'success'}, safe=False)

            return JsonResponse({'message': 'success'}, safe=False)

        except:
            return JsonResponse({'message': 'error'}, safe=False)


@cache_page(60 * 5)
def get_admin_dashboard_report_api(request):
    today = datetime.today().date()
    party_count = Party.objects.filter(isDeleted=False).count()
    staff_count = StaffUser.objects.filter(isDeleted=False).count()
    location = GeolocationPackage.objects.filter(isDeleted=False).only('used', 'balance').order_by('-id').first()
    collection_total = Collection.objects.filter(
        isDeleted=False,
        collectionDateTime__date=today
    ).aggregate(total=Sum('paidAmount'))['total'] or 0.0
    sales_total = Sales.objects.filter(
        isDeleted=False,
        buildDate__exact=today
    ).aggregate(total=Sum('amount'))['total'] or 0.0

    data = {
        'partyCount': formatINR(party_count),
        'staffCount': staff_count,
        'locationCount': str(int(location.used)) + '/' + str(int(location.balance)) if location else '0/0',
        'collection': formatINR(collection_total),
        'Sale': formatINR(sales_total),
    }
    return JsonResponse({'data': data}, safe=False)


@cache_page(60 * 5)
def get_staff_dashboard_report_api(request):
    today = datetime.today().date()
    user_party_group_id = StaffUser.objects.filter(
        user_ID_id=request.user.pk,
        isDeleted=False
    ).values_list('partyGroupID_id', flat=True).first()

    party_count = 0
    if user_party_group_id:
        party_count = Party.objects.filter(
            isDeleted=False,
            partyGroupID_id=user_party_group_id
        ).count()

    collection_stats = Collection.objects.filter(
        isDeleted=False,
        collectedBy__user_ID_id=request.user.pk
    ).aggregate(
        total_amount=Sum('paidAmount'),
        today_amount=Sum('paidAmount', filter=Q(collectionDateTime__date=today)),
        today_count=Count('id', filter=Q(collectionDateTime__date=today)),
    )

    data = {
        'partyCount': formatINR(party_count),
        'collection_totalCount': collection_stats['today_count'] or 0,
        'collection_total': formatINR(collection_stats['total_amount'] or 0.0),
        'collection': formatINR(collection_stats['today_amount'] or 0.0)
    }
    return JsonResponse({'data': data}, safe=False)


def generate_collection_report(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    a_total_cash = 0.0
    a_total_cheque = 0.0
    a_total_online = 0.0
    a_total_cheque_cc = 0.0
    a_total_party = 0.0
    if staffID == 'All':
        col = Collection.objects.select_related().filter(
            collectionDateTime__date=colDate.date(),
            isApproved__exact=True,
            isDeleted__exact=False
        ).order_by('collectedBy__name')
        staffName = 'All'
    else:
        col = Collection.objects.select_related().filter(
            collectionDateTime__date=colDate.date(),
            isApproved__exact=True,
            isDeleted__exact=False,
            collectedBy_id=int(staffID)
        ).order_by(
            'collectedBy__name')
        user = StaffUser.objects.get(pk=int(staffID))
        staffName = user.name + ' - ' + user.partyGroupID.name

    for a in col:
        if a.modeOfPayment == 'Cash':
            a_total_cash = a_total_cash + a.paidAmount
        if a.modeOfPayment == 'Cheque':
            a_total_cheque = a_total_cheque + a.paidAmount
        if a.modeOfPayment == 'Online':
            a_total_online = a_total_online + a.paidAmount
        if a.modeOfPayment == 'Cheque CC':
            a_total_cheque_cc = a_total_cheque_cc + a.paidAmount
        if a.modeOfPayment == 'Party':
            a_total_party = a_total_party + a.paidAmount
    context = {
        'date': colDate,
        'col': col,
        'staffName': staffName,
        'a_total_cash': a_total_cash,
        'a_total_cheque': a_total_cheque,
        'a_total_online': a_total_online,
        'a_total_cheque_cc': a_total_cheque_cc,
        'a_total_party': a_total_party,
        'total': a_total_cash + a_total_cheque + a_total_online + a_total_cheque_cc + a_total_party
    }

    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/admin/collectionReportPDF.html", context)

    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


# ----------- attendance API----------------
@transaction.atomic
@csrf_exempt
def add_attendance_api(request):
    if request.method == 'POST':
        try:
            remark = request.POST.get("remark")
            stat = request.POST.get("stat")
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            obj = Attendance.objects.get(datetime__date=datetime.today().date(),
                                         staffID__user_ID__id=request.user.pk)

            if stat == 'Login':
                obj.isLogIn = True
                obj.login_remark = remark
                obj.login_latitude = lat
                obj.login_longitude = lng
                obj.loginDateTime = datetime.today().now()
                try:
                    obj.login_location = deduct_location_balance(lat, lng)
                except:
                    pass
            if stat == 'Logout':
                obj.isLogOut = True
                obj.logout_remark = remark
                obj.logout_latitude = lat
                obj.logout_longitude = lng
                obj.logoutDateTime = datetime.today().now()
                try:
                    obj.logout_location = deduct_location_balance(lat, lng)
                except:
                    pass
            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


class StaffAttendanceListJson(BaseDatatableView):
    order_columns = ['datetime', 'loginDateTime', 'login_location', 'login_remark', 'logoutDateTime', 'logout_location',
                     'logout_remark',
                     ]

    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')

            return _attendance_list_base_qs().filter(
                isDeleted__exact=False,
                datetime__range=(sDate.date(), eDate.date() + timedelta(days=1)),
                staffID__user_ID__id=self.request.user.pk
            )
        except:
            return _attendance_list_base_qs().filter(
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
        group_names = _request_group_names(self.request)
        for item in qs:
            if item.isLogIn == True:
                login_time = item.loginDateTime.strftime('%I:%M %p')
                login_remark = item.login_remark
                login_location = item.login_location
            else:
                login_time = '-'
                login_remark = '-'
                login_location = '-'

            if item.isLogOut == True:
                logout_time = item.logoutDateTime.strftime('%I:%M %p')
                logout_remark = item.logout_remark
                logout_location = item.logout_location
            else:
                logout_time = '-'
                logout_remark = '-'
                logout_location = '-'

            json_data.append([
                escape(item.datetime.strftime('%d-%m-%Y')),
                login_time,
                login_location,
                login_remark,
                logout_time,
                logout_location,
                logout_remark

            ])

        return json_data


class AdminAttendanceListJson(BaseDatatableView):
    order_columns = ['datetime', 'staffID.name', 'loginDateTime', 'logoutDateTime', 'login_location', 'login_remark',
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

                return _attendance_list_base_qs().filter(
                    isDeleted__exact=False,
                    datetime__range=(sDate.date(), eDate.date() + timedelta(days=1))
                )

            else:

                return _attendance_list_base_qs().filter(
                    isDeleted__exact=False,
                    datetime__range=(sDate.date(), eDate.date() + timedelta(days=1)),
                    staffID_id=int(staffID)
                )

        except:
            return _attendance_list_base_qs().filter(
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
        group_names = _request_group_names(self.request)
        for item in qs:
            if item.isLogIn == True:
                login_time = item.loginDateTime.strftime('%I:%M %p')
                login_remark = item.login_remark
                login_location = item.login_location
            else:
                login_time = '-'
                login_remark = '-'
                login_location = '-'

            if item.isLogOut == True:
                logout_time = item.logoutDateTime.strftime('%I:%M %p')
                logout_remark = item.logout_remark
                logout_location = item.logout_location
            else:
                logout_time = '-'
                logout_remark = '-'
                logout_location = '-'

            json_data.append([
                escape(item.datetime.strftime('%d-%m-%Y')),
                escape(item.staffID.name),
                login_time,
                logout_time,
                login_location,
                login_remark,

                logout_location,
                logout_remark

            ])

        return json_data


def generate_attendance_pdf_staff_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')

    col = Attendance.objects.select_related().filter(datetime__range=(
        startDate.date(), endDate.date() + timedelta(days=1)),
        isDeleted__exact=False, staffID__user_ID_id__exact=request.user.pk).order_by(
        'datetime')
    user = StaffUser.objects.get(user_ID__id=request.user.pk)

    context = {
        'startDate': startDate,
        'endDate': endDate,
        'col': col,
        'staffName': user.name,
    }

    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/staffAttendanceReportPDF.html", context)

    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_attendance_pdf_admin_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    staffID = request.GET.get('staffID')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    if staffID == 'All':
        col = Attendance.objects.select_related().filter(datetime__range=(
            startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False).order_by('staffID__name')
        staffName = "All"
    else:
        col = Attendance.objects.select_related().filter(datetime__range=(
            startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False, staffID_id=int(staffID)).order_by('datetime')

        user = StaffUser.objects.get(id=int(staffID))
        staffName = user.name

    context = {
        'startDate': startDate,
        'endDate': endDate,
        'col': col,
        'staffName': staffName,
    }

    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/adminAttendanceReportPDF.html", context)

    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


# -------------------Message List---------------
class MessageListJson(BaseDatatableView):
    order_columns = ['messageTo', 'phone', 'message', 'datetime', 'status']

    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        return WhatsappMessageStatus.objects.filter(isDeleted__exact=False).only(
            'id', 'messageTo', 'phone', 'message', 'datetime', 'status'
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(messageTo__icontains=search) | Q(phone__icontains=search) | Q(message__icontains=search)
                | Q(status__icontains=search) | Q(datetime__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if item.status == 'Success':
                status = '''<div class="ui tiny green label">
              Success
            </div>'''
            else:
                status = '''<a class="ui tiny red label" onclick="ResendMsg('{}')">
                             Fail
                           </a>'''.format(item.pk)

            json_data.append([
                escape(item.messageTo),
                escape(item.phone),
                escape(item.message),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                status,

            ])

        return json_data


# ----------------------Sales-----------------


@transaction.atomic
@csrf_exempt
def add_sales_by_admin_api(request):
    if request.method == 'POST':
        try:
            invoiceSeriesSelect = request.POST.get("invoiceSeriesSelect")
            invoiceNo = request.POST.get("invoiceNo")
            invoiceYearSelect = request.POST.get("invoiceYearSelect")
            colDate = request.POST.get("colDate")
            party = request.POST.get("party")
            amount = request.POST.get("amount")
            remark = request.POST.get("remark")
            c = str(party).split('@')
            cus = Party.objects.select_related().get(pk=int(c[1]))
            obj = Sales()
            obj.partyID_id = cus.pk
            obj.invoiceNumber = invoiceSeriesSelect + '/' + invoiceNo + '/' + invoiceYearSelect
            obj.amount = float(amount)
            obj.remark = remark
            obj.buildDate = datetime.strptime(colDate, '%d/%m/%Y')
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.createdBy_id = user.pk
            obj.save()
            obj.paymentID = 'S' + str(obj.pk).zfill(8)
            obj.save()
            # try:
            #     msg = "Your order has been dispatched Ag. Bill No. {} Dt. {} Amt. Rs. {}, Please confirm received the material. If you have any queries about your order, Please feel free contact on this no 7005607770. Thanks, BSS".format(obj.invoiceNumber, obj.buildDate.strftime('%d-%m-%Y'), obj.amount)
            #     try:
            #         numbers = str(obj.partyID.phone).split('.')
            #         for n in numbers:
            #             send_message(n, msg, obj.partyID.name)
            #     except:
            #         send_message(obj.partyID.phone, msg, obj.partyID.name)
            # except:
            #     pass

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


class SalesByAdminListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'invoiceNumber', 'amount', 'action', 'buildDate', 'createdBy.name',
                     'isApproved', 'approvedBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            if staffID == 'All':
                return _sales_list_base_qs().filter(
                    isDeleted__exact=False,
                    buildDate__range=[sDate.date(), eDate.date()]
                )
            else:
                return _sales_list_base_qs().filter(
                    isDeleted__exact=False,
                    buildDate__range=[sDate.date(), eDate.date()],
                    createdBy_id=int(staffID)
                )
        except:
            return _sales_list_base_qs().filter(
                isDeleted__exact=False,
                buildDate__exact=datetime.today().date()
            )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search) | Q(buildDate__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in self.request.user.groups.values_list('name',
                                                               flat=True) or 'Moderator' in self.request.user.groups.values_list(
                'name', flat=True):

                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
               <i class="whatsapp icon"></i>
              </button>
              <a href="/edit_sales/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            elif 'SalesAdmin' in group_names:
                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                               <i class="whatsapp icon"></i>
                              </button>'''.format(item.pk)
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'

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
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                action,
                escape(item.buildDate.strftime('%d-%m-%Y')),
                createdBy,
                isApproved,
                approvedBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),

            ])

        return json_data


class SalesByStaffListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'invoiceNumber', 'amount', 'buildDate', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if self.request.user.username == 'USER376':
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                staffID = self.request.GET.get("staffID")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                if staffID == 'All':
                    return _sales_list_base_qs().filter(
                        isDeleted__exact=False,
                        buildDate__range=[sDate.date(), eDate.date()]
                    )
                else:
                    return _sales_list_base_qs().filter(
                        isDeleted__exact=False,
                        buildDate__range=[sDate.date(), eDate.date()],
                        createdBy_id=int(staffID)
                    )
            except:
                return _sales_list_base_qs().filter(
                    isDeleted__exact=False,
                    buildDate__exact=datetime.today().date()
                )

        else:
            return _sales_list_base_qs().filter(
                isDeleted__exact=False,
                buildDate__exact=datetime.today().date()
            )

    #
    # else:
    #
    #     return Sales.objects.select_related().filter(isDeleted__exact=False,
    #                                                       buildDate__exact=datetime.today().date(), createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search) | Q(buildDate__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''<button  data-inverted="" data-tooltip="Send Message" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
               <i class="whatsapp icon"></i>
              </button>
              <a href="/edit_sales/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'

            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                escape(item.buildDate.strftime('%d-%m-%Y')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,

            ])

        return json_data


def generate_sales_report(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    a_total = 0.0
    if staffID == 'All':
        col = Sales.objects.select_related().filter(
            buildDate__exact=colDate.date(),
            isDeleted__exact=False
        ).order_by('createdBy__name')
        staffName = 'All'
    else:
        col = Sales.objects.select_related().filter(
            buildDate__exact=colDate.date(),
            isDeleted__exact=False,
            createdBy_id=int(staffID)
        ).order_by(
            'createdBy__name')
        user = StaffUser.objects.get(pk=int(staffID))
        staffName = user.name + ' - ' + user.partyGroupID.name

    for a in col:
        a_total = a_total + a.amount

    context = {
        'date': colDate,
        'col': col,
        'staffName': staffName,
        'total': a_total,

    }

    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/salesReportPDF.html", context)

    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


@transaction.atomic
@csrf_exempt
def delete_sales(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = Sales.objects.select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def update_sales_by_admin_api(request):
    if request.method == 'POST':
        try:
            invoiceSeriesSelect = request.POST.get("invoiceSeriesSelect")
            invoiceNo = request.POST.get("invoiceNo")
            invoiceYearSelect = request.POST.get("invoiceYearSelect")
            colDate = request.POST.get("colDate")
            party = request.POST.get("party")
            amount = request.POST.get("amount")
            remark = request.POST.get("remark")
            ID = request.POST.get("ID")
            # c = str(party).split('@')
            # cus = Party.objects.select_related().get(pk=int(c[1]))
            cus = Party.objects.select_related().get(pk=int(party))
            obj = Sales.objects.get(pk=int(ID))
            obj.partyID_id = cus.pk
            obj.invoiceNumber = invoiceSeriesSelect + '/' + invoiceNo + '/' + invoiceYearSelect
            obj.amount = float(amount)
            obj.remark = remark
            obj.buildDate = datetime.strptime(colDate, '%d/%m/%Y')
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.createdBy_id = user.pk
            obj.save()

            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def send_message_sales(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = Sales.objects.select_related().get(pk=int(id))
            user = StaffUser.objects.get(user_ID__id=request.user.pk)
            obj.isApproved = True
            obj.approvedBy_id = user.pk
            obj.save()
            try:
                msg = "Your order has been dispatched Ag. Bill No. {} Dt. {} Amt. Rs. {}, Please confirm received the material. If you have any queries about your order, Please feel free contact on this no 7005607770. Thanks, BSS".format(
                    obj.invoiceNumber, obj.buildDate.strftime('%d-%m-%Y'), obj.amount)

                # send_whatsapp_message(obj.partyID.phone, msg)
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
@csrf_exempt
def re_send_message_sales(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = WhatsappMessageStatus.objects.select_related().get(pk=int(id))
            try:
                msg = WhatsappMessage.objects.filter(isDeleted__exact=False).last()
                if msg.used < msg.balance:
                    try:
                        # r = requests.get(
                        #     msg.name + "send?number=91" + obj.phone + "&type=text&message=" + obj.message + "&instance_id=" + msg.instanceID + "&access_token=" + msg.apiKey,
                        #     verify=False)
                        # data = r.json()
                        # if data['status'] == 'success':
                        #     obj.status = 'Success'
                        #     msg.used = (msg.used + 1)
                        #     msg.save()
                        #     obj.save()
                        #     return JsonResponse({'message': 'success'}, safe=False)

                        # else:
                        obj.status = 'Fail'
                        obj.save()
                        return JsonResponse({'message': 'error'}, safe=False)

                    except:
                        obj.status = 'Fail'
                        obj.save()
                        return JsonResponse({'message': 'error'}, safe=False)
            except:
                return JsonResponse({'message': 'error'}, safe=False)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def enable_tally_post_api(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("tallyColID")
            obj = Collection.objects.get(pk=int(id), isApproved__exact=True)
            try:
                obj.isTallied = True
                obj.save()
                return JsonResponse({'message': 'success'}, safe=False)
            except:
                return JsonResponse({'message': 'error'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


# cash counter api
@transaction.atomic
@csrf_exempt
def add_cash_counter_entry_api(request):
    if request.method == 'POST':
        try:
            invoiceSeriesSelect = request.POST.get("invoiceSeriesSelect")
            invoiceNo = request.POST.get("invoiceNo")
            invoiceYearSelect = request.POST.get("invoiceYearSelect")
            party = request.POST.get("party")
            amount = request.POST.get("amount")
            remark = request.POST.get("remark")
            amountMixCash = request.POST.get("amountMixCash")
            amountMixCard = request.POST.get("amountMixCard")
            expenseType = request.POST.get("expenseType")
            bank = request.POST.get("bank")
            mode = request.POST.get("mode")
            obj = CashCounter()
            if mode == "Credit" or mode == "Collection" or mode == "Advance":
                c = str(party).split('@')
                cus = Party.objects.select_related().get(pk=int(c[1]))
                obj.partyID_id = cus.pk
            if mode == "Cash" or mode == "Mix" or mode == "Card" or mode == "Credit" or mode == "Return":
                obj.invoiceNumber = invoiceSeriesSelect + '/' + invoiceNo + '/' + invoiceYearSelect
            if mode == "Mix":
                obj.mixCashAmount = float(amountMixCash)
                obj.mixCardAmount = float(amountMixCard)
            else:
                obj.amount = float(amount)
            if mode == "Expense":
                obj.expenseType = expenseType
            if mode == "Card" or mode == "Mix":
                obj.bankID_id = int(bank)
            obj.mode = mode
            obj.remark = remark
            obj.entryDate = datetime.now()
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.createdBy_id = user.pk
            obj.save()
            obj.counterID = 'CC' + str(obj.pk).zfill(8)
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


class CashCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):

        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Cash")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Cash")
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               createdBy__user_ID_id=self.request.user.pk,
                                                               mode__iexact="Cash")

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'

            json_data.append([
                escape(item.counterID),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,

            ])

        return json_data


class CardCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Card")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Card")

        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Card",
                                                               createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(bankID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                remark = escape(item.bankID.name) + ' - ' + escape(item.bankID.accountNumber) + ' - ' + escape(
                    item.remark)
            except:
                remark = escape(item.remark)

            json_data.append([
                escape(item.counterID),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                remark,
                action,

            ])

        return json_data


class ReturnCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Return")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Return")
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Return",
                                                               createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'

            json_data.append([
                escape(item.counterID),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,

            ])

        return json_data


class CreditCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'partyID.name', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Credit")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Credit")
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Credit",
                                                               createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                party = item.partyID.name
            except:
                party = 'N/A'

            json_data.append([
                escape(item.counterID),
                escape(party),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,

            ])

        return json_data


class MixCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'mixCashAmount', 'mixCardAmount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Mix")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Mix",
                                                                   createdBy__user_ID_id=self.request.user.pk)
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               createdBy__user_ID_id=self.request.user.pk,
                                                               mode__iexact="Mix")

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(bankID__name__icontains=search) | Q(
                    mixCashAmount__icontains=search) | Q(
                    mixCardAmount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                remark = escape(item.bankID.name) + ' - ' + escape(item.bankID.accountNumber) + ' - ' + escape(
                    item.remark)
            except:
                remark = escape(item.remark)
            json_data.append([
                escape(item.counterID),
                escape(item.invoiceNumber),
                formatINR(format(item.mixCashAmount, '.0f')),
                formatINR(format(item.mixCardAmount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                remark,
                action,

            ])

        return json_data


class CollectionCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'partyID.name', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Collection")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Collection")
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Collection",
                                                               createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                party = item.partyID.name
            except:
                party = 'N/A'

            json_data.append([
                escape(item.counterID),
                escape(party),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,

            ])

        return json_data


class AdvanceCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'partyID.name', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Advance")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Advance")
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Advance",
                                                               createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                party = item.partyID.name
            except:
                party = 'N/A'

            json_data.append([
                escape(item.counterID),
                escape(party),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,

            ])

        return json_data


class ExpenseCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'remark', 'amount', 'createdBy.name',
                     'datetime'
                     ]

    def get_initial_queryset(self):
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Expense")

            except:
                return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Expense")
        else:
            return _cash_counter_list_base_qs().filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Expense",
                                                               createdBy__user_ID_id=self.request.user.pk)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(expenseType__icontains=search) | Q(
                    amount__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)

            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''

            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'

            json_data.append([
                escape(item.counterID),
                escape(item.expenseType) + ' - ' + escape(item.remark),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


@cache_page(60 * 30)
def get_cash_counter_dashboard_report_api(request):
    today = datetime.today().date()
    base_qs = CashCounter.objects.filter(
        isDeleted=False,
        datetime__date=today,
        createdBy__user_ID=request.user.pk
    )
    totals = base_qs.aggregate(
        cash_total=Sum('amount', filter=Q(mode="Cash")),
        card_total=Sum('amount', filter=Q(mode="Card")),
        credit_total=Sum('amount', filter=Q(mode="Credit")),
        collection_total=Sum('amount', filter=Q(mode="Collection")),
        expense_total=Sum('amount', filter=Q(mode="Expense")),
        advance_total=Sum('amount', filter=Q(mode="Advance")),
        return_total=Sum('amount', filter=Q(mode="Return")),
        mix_cash_total=Sum('mixCashAmount', filter=Q(mode="Mix")),
        mix_card_total=Sum('mixCardAmount', filter=Q(mode="Mix")),
    )
    data = {
        'cash_total': formatINR(totals['cash_total'] or 0.0),
        'card_total': formatINR(totals['card_total'] or 0.0),
        'credit_total': formatINR(totals['credit_total'] or 0.0),
        'collection_total': formatINR(totals['collection_total'] or 0.0),
        'expense_total': formatINR(totals['expense_total'] or 0.0),
        'advance_total': formatINR(totals['advance_total'] or 0.0),
        'return_total': formatINR(totals['return_total'] or 0.0),
        'mix_cash_total': formatINR(totals['mix_cash_total'] or 0.0),
        'mix_card_total': formatINR(totals['mix_card_total'] or 0.0)
    }
    return JsonResponse({'data': data}, safe=False)


@cache_page(60 * 30)
def get_cash_counter_dashboard_report_admin_api(request):
    today = datetime.today().date()
    totals = CashCounter.objects.filter(
        isDeleted=False,
        datetime__date=today
    ).aggregate(
        cash_total=Sum('amount', filter=Q(mode="Cash")),
        card_total=Sum('amount', filter=Q(mode="Card")),
        credit_total=Sum('amount', filter=Q(mode="Credit")),
        collection_total=Sum('amount', filter=Q(mode="Collection")),
        expense_total=Sum('amount', filter=Q(mode="Expense")),
        advance_total=Sum('amount', filter=Q(mode="Advance")),
        return_total=Sum('amount', filter=Q(mode="Return")),
        mix_cash_total=Sum('mixCashAmount', filter=Q(mode="Mix")),
        mix_card_total=Sum('mixCardAmount', filter=Q(mode="Mix")),
    )
    data = {
        'cash_total': formatINR(totals['cash_total'] or 0.0),
        'card_total': formatINR(totals['card_total'] or 0.0),
        'credit_total': formatINR(totals['credit_total'] or 0.0),
        'collection_total': formatINR(totals['collection_total'] or 0.0),
        'expense_total': formatINR(totals['expense_total'] or 0.0),
        'advance_total': formatINR(totals['advance_total'] or 0.0),
        'return_total': formatINR(totals['return_total'] or 0.0),
        'mix_cash_total': formatINR(totals['mix_cash_total'] or 0.0),
        'mix_card_total': formatINR(totals['mix_card_total'] or 0.0)
    }
    return JsonResponse({'data': data}, safe=False)


@transaction.atomic
@csrf_exempt
def delete_cash_counter(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            if 'c' in id:
                id = id.split("c")[1]
                obj = CollectionCashCounter.objects.get(pk=int(id))
            else:
                id = id
                obj = CashCounter.objects.get(pk=int(id))
            # Output: 56

            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
def update_cash_counter_by_admin_api(request):
    if request.method == 'POST':
        try:
            invoiceSeriesSelect = request.POST.get("invoiceSeriesSelect")
            invoiceNo = request.POST.get("invoiceNo")
            invoiceYearSelect = request.POST.get("invoiceYearSelect")
            party = request.POST.get("party")
            amount = request.POST.get("amount")
            remark = request.POST.get("remark")
            expenseType = request.POST.get("expenseType")
            amountMixCash = request.POST.get("amountMixCash")
            amountMixCard = request.POST.get("amountMixCard")
            mode = request.POST.get("mode")
            editID = request.POST.get("editID")
            bank = request.POST.get("bank")
            obj = CashCounter.objects.get(pk=int(editID))
            if mode == "Credit" or mode == "Collection" or mode == "Advance":
                cus = Party.objects.select_related().get(pk=int(party))
                obj.partyID_id = cus.pk
            if mode == "Cash" or mode == "Mix" or mode == "Card" or mode == "Credit" or mode == "Return":
                obj.invoiceNumber = invoiceSeriesSelect + '/' + invoiceNo + '/' + invoiceYearSelect
            if mode == "Mix":
                obj.mixCashAmount = float(amountMixCash)
                obj.mixCardAmount = float(amountMixCard)
            else:
                obj.amount = float(amount)
            if mode == "Expense":
                obj.expenseType = expenseType
            if mode == "Card" or mode == "Mix":
                obj.bankID_id = int(bank)

            obj.mode = mode
            obj.remark = remark
            obj.entryDate = datetime.now()
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.createdBy_id = user.pk
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


def generate_cash_counter_report(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    a_total_cash = 0.0
    a_total_cheque = 0.0
    a_total_online = 0.0
    a_total_cheque_cc = 0.0
    a_total_party = 0.0
    ccol = _cash_counter_collection_list_base_qs().filter(
        collectionDateTime__date=colDate.date(),
        isDeleted__exact=False
    ).order_by('collectedBy__name')

    for a in ccol:
        if a.modeOfPayment == 'Cash':
            a_total_cash = a_total_cash + a.paidAmount
        if a.modeOfPayment == 'Cheque':
            a_total_cheque = a_total_cheque + a.paidAmount
        if a.modeOfPayment == 'Online':
            a_total_online = a_total_online + a.paidAmount
        if a.modeOfPayment == 'Cheque CC':
            a_total_cheque_cc = a_total_cheque_cc + a.paidAmount
        if a.modeOfPayment == 'Party':
            a_total_party = a_total_party + a.paidAmount
    context = {
    }

    cash_total = 0.0
    card_total = 0.0
    credit_total = 0.0
    return_total = 0.0
    expense_total = 0.0
    advance_total = 0.0
    mix_cash_total = 0.0
    mix_card_total = 0.0
    collection_total = 0.0

    col = _cash_counter_list_base_qs().filter(
        datetime__date=colDate.date(),
        isDeleted__exact=False
    ).order_by(
        'mode')
    for a in col:
        if a.mode == 'Cash':
            cash_total += a.amount
        if a.mode == 'Card':
            card_total += a.amount
        if a.mode == 'Credit':
            credit_total += a.amount
        if a.mode == 'Return':
            return_total += a.amount
        if a.mode == 'Expense':
            expense_total = + a.amount
        if a.mode == 'Advance':
            advance_total = + a.amount
        if a.mode == 'Collection':
            collection_total = + a.amount
        if a.mode == 'Mix':
            mix_cash_total = + a.mixCashAmount
            mix_card_total = + a.mixCardAmount
    context = {
        'date': colDate,
        'col': col,
        'cash_total': cash_total,
        'card_total': card_total,
        'credit_total': credit_total,
        'expense_total': expense_total,
        'return_total': return_total,
        'advance_total': advance_total,
        'collection_total': collection_total,
        'mix_cash_total': mix_cash_total,
        'mix_card_total': mix_card_total,
        'RokadValue': (cash_total + collection_total + mix_cash_total) - (expense_total + advance_total + return_total),
        'total': (
                cash_total + card_total + credit_total + collection_total + mix_cash_total + mix_card_total + return_total + expense_total + advance_total),
        'ccol': ccol,
        'a_total_cash': a_total_cash,
        'a_total_cheque': a_total_cheque,
        'a_total_online': a_total_online,
        'a_total_cheque_cc': a_total_cheque_cc,
        'a_total_party': a_total_party,
        'a_total': a_total_cash + a_total_cheque + a_total_online + a_total_cheque_cc + a_total_party

    }

    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/cashCounterReportPDF.html", context)

    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


@transaction.atomic
@csrf_exempt
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
            c = str(party).split('@')
            cus = Party.objects.select_related().get(pk=int(c[1]))
            obj = CollectionCashCounter()
            obj.isTallied = False
            obj.partyID_id = cus.pk
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            try:
                obj.bankID_id = int(bank)
            except:
                pass
            if paymentMode == 'Cheque':
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = Party.objects.select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass

            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = StaffUser.objects.get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk
            try:
                # obj.collectionAddress = deduct_location_balance(lat, lng)
                obj.collectionAddress = "From Shop Counter"
            except:
                pass

            obj.save()
            obj.paymentID = 'CCC' + str(obj.pk).zfill(8)
            obj.collectionDateTime = obj.datetime
            obj.save()

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
        if 'Admin' in self.request.user.groups.values_list('name',
                                                           flat=True) or 'Moderator' in self.request.user.groups.values_list(
            'name', flat=True):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')

                return _cash_counter_collection_list_base_qs().filter(isDeleted__exact=False,
                                                                             collectionDateTime__date__range=[
                                                                                 sDate.date(),
                                                                                 eDate.date()])

            except:
                return _cash_counter_collection_list_base_qs().filter(isDeleted__exact=False,
                                                                             datetime__date=datetime.today().date())
        else:
            return _cash_counter_collection_list_base_qs().filter(isDeleted__exact=False,
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
        group_names = _request_group_names(self.request)
        for item in qs:
            if 'Admin' in group_names:

                action = '''
                  <a href="/edit_cash_counter_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('c{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk),
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

            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(bank),
                escape(item.detail),
                escape(chequeDate),
                escape(tparty),
                escape(item.remark),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
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
            # cus = Party.objects.select_related().get(pk=int(c[1]))
            cus = Party.objects.select_related().get(pk=int(party))

            obj = CollectionCashCounter.objects.get(pk=int(ID))
            request_groups = _request_group_names(request)
            if 'Admin' in request_groups or 'Moderator' in request_groups:
                obj.partyID_id = cus.pk
                obj.modeOfPayment = paymentMode
                obj.paidAmount = float(amountPaid)
                obj.detail = detail
                obj.remark = remark
                obj.transferredPartyID = None
                obj.collectionDateTime = datetime.strptime(cDate, '%d/%m/%Y')
                try:
                    obj.bankID_id = int(bank)
                except:
                    pass
                if paymentMode == 'Cheque':
                    obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
                if paymentMode == 'Party':
                    try:
                        tparty = Party.objects.select_related().get(pk=int(transferredParty))
                        obj.transferredPartyID_id = tparty.pk
                    except:
                        pass
                obj.save()
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
