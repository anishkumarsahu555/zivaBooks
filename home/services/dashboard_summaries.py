from datetime import datetime, timedelta

from django.db.models import Count, Q, Sum

from home.models import Bank, Collection, GeolocationPackage, Order, Party, PartyGroup, Sales, StaffUser
from home.services.orders import can_filter_all_orders
from home.services.reports import formatINR
from home.services.sales import can_filter_all_sales
from home.tenancy import tenant_qs


def admin_dashboard_summary(request):
    today = datetime.today().date()
    party_count = tenant_qs(request, Party).filter(isDeleted=False).count()
    staff_count = tenant_qs(request, StaffUser).filter(isDeleted=False).count()
    location = tenant_qs(request, GeolocationPackage).filter(isDeleted=False).only('used', 'balance').order_by('-id').first()
    collection_total = tenant_qs(request, Collection).filter(
        isDeleted=False,
        collectionDateTime__date=today,
    ).aggregate(total=Sum('paidAmount'))['total'] or 0.0
    sales_total = tenant_qs(request, Sales).filter(
        isDeleted=False,
        buildDate__exact=today,
    ).aggregate(total=Sum('amount'))['total'] or 0.0
    return {
        'partyCount': formatINR(party_count),
        'staffCount': staff_count,
        'locationCount': str(int(location.used)) + '/' + str(int(location.balance)) if location else '0/0',
        'collection': formatINR(collection_total),
        'Sale': formatINR(sales_total),
    }


def staff_dashboard_summary(request):
    today = datetime.today().date()
    user_party_group_id = tenant_qs(request, StaffUser).filter(
        user_ID_id=request.user.pk,
        isDeleted=False,
    ).values_list('partyGroupID_id', flat=True).first()
    party_count = 0
    if user_party_group_id:
        party_count = tenant_qs(request, Party).filter(
            isDeleted=False,
            partyGroupID_id=user_party_group_id,
        ).count()
    collection_stats = tenant_qs(request, Collection).filter(
        isDeleted=False,
        collectedBy__user_ID_id=request.user.pk,
    ).aggregate(
        total_amount=Sum('paidAmount'),
        today_amount=Sum('paidAmount', filter=Q(collectionDateTime__date=today)),
        today_count=Count('id', filter=Q(collectionDateTime__date=today)),
    )
    return {
        'partyCount': formatINR(party_count),
        'collection_totalCount': collection_stats['today_count'] or 0,
        'collection_total': formatINR(collection_stats['total_amount'] or 0.0),
        'collection': formatINR(collection_stats['today_amount'] or 0.0),
    }


def party_dashboard_summary(request):
    today = datetime.today().date()
    thirty_days_ago = today - timedelta(days=30)
    parties = tenant_qs(request, Party).filter(isDeleted=False)
    party_groups = tenant_qs(request, PartyGroup).filter(isDeleted=False)
    staff = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    return {
        'party_total_count': parties.count(),
        'party_group_count': party_groups.count(),
        'party_assigned_count': parties.filter(assignTo__isnull=False).count(),
        'party_unassigned_count': parties.filter(assignTo__isnull=True).count(),
        'party_recent_count': parties.filter(datetime__date__gte=thirty_days_ago).count(),
        'party_my_assigned_count': parties.filter(assignTo=staff).count() if staff else 0,
    }


def bank_dashboard_summary(request):
    banks = tenant_qs(request, Bank).filter(isDeleted=False)
    total_banks = banks.count()
    banks_with_account = banks.exclude(accountNumber__isnull=True).exclude(accountNumber__exact='').count()
    banks_used = tenant_qs(request, Collection).filter(isDeleted=False, bankID__isnull=False).values('bankID').distinct().count()
    return {
        'bank_total_count': total_banks,
        'bank_used_count': banks_used,
        'bank_with_account_count': banks_with_account,
        'bank_missing_account_count': total_banks - banks_with_account,
    }


def sales_dashboard_summary(request):
    today = datetime.today().date()
    queryset = tenant_qs(request, Sales).filter(isDeleted=False)
    if not can_filter_all_sales(request.user):
        queryset = queryset.filter(createdBy__user_ID_id=request.user.pk)
    today_qs = queryset.filter(buildDate=today)
    return {
        'sales_today_total': formatINR(today_qs.aggregate(total=Sum('amount'))['total'] or 0.0),
        'sales_today_count': today_qs.count(),
        'sales_lifetime_total': formatINR(queryset.aggregate(total=Sum('amount'))['total'] or 0.0),
        'sales_record_count': queryset.count(),
    }


def orders_dashboard_summary(request):
    today = datetime.today().date()
    queryset = tenant_qs(request, Order).filter(isDeleted=False)
    if not can_filter_all_orders(request.user):
        queryset = queryset.filter(createdBy__user_ID_id=request.user.pk)
    today_qs = queryset.filter(buildDate=today)
    return {
        'orders_today_count': today_qs.count(),
        'orders_pending_count': queryset.filter(status=Order.Status.SUBMITTED).count(),
        'orders_approved_today_count': queryset.filter(status=Order.Status.APPROVED, approvedOn__date=today).count(),
        'orders_record_count': queryset.count(),
    }
