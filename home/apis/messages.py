from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from home.models import WhatsappMessage, WhatsappMessageStatus
from home.tenancy import tenant_qs


class MessageListJson(BaseDatatableView):
    order_columns = ['messageTo', 'phone', 'message', 'datetime', 'status']

    def get_initial_queryset(self):
        return tenant_qs(self.request, WhatsappMessageStatus).filter(isDeleted__exact=False).only(
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


@transaction.atomic
def re_send_message_sales(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, WhatsappMessageStatus).select_related().get(pk=int(id))
            try:
                msg = tenant_qs(request, WhatsappMessage).filter(isDeleted__exact=False).last()
                if msg and msg.used < msg.balance:
                    try:
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
