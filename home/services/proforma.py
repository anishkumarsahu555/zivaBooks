from home.models import ProformaInvoice, StaffUser
from home.services.collections import has_any_permission
from home.tenancy import tenant_qs


def can_manage_proforma_invoice(user):
    return user.has_perm('home.can_manage_proforma_invoice')


def can_convert_proforma_invoice(user):
    return user.has_perm('home.can_convert_proforma_invoice')


def has_proforma_access(user):
    return has_any_permission(
        user,
        'home.can_manage_proforma_invoice',
        'home.can_convert_proforma_invoice',
        'home.can_view_proforma_invoice_reports',
        'home.view_proformainvoice',
        'home.add_proformainvoice',
        'home.change_proformainvoice',
    )


def can_edit_proforma_invoice(user):
    return has_any_permission(user, 'home.change_proformainvoice', 'home.can_manage_proforma_invoice')


def can_filter_all_proforma(user):
    return can_manage_proforma_invoice(user) or user.has_perm('home.can_view_proforma_invoice_reports') or can_convert_proforma_invoice(user)


def can_edit_proforma_record(user, proforma_obj):
    if not user.is_authenticated:
        return False
    if not can_edit_proforma_invoice(user):
        return False
    if proforma_obj is None or proforma_obj.status == ProformaInvoice.Status.CONVERTED:
        return False
    if can_manage_proforma_invoice(user):
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return proforma_obj.createdBy_id == staff.pk
