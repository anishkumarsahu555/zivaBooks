from __future__ import annotations

from threading import local
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType


_state = local()


def set_current_user(user):
    _state.user = user


def get_current_user():
    return getattr(_state, 'user', None)


def clear_current_user():
    if hasattr(_state, 'user'):
        delattr(_state, 'user')


def _serialize_audit_value(value):
    if hasattr(value, 'name') and hasattr(value, 'storage'):
        return value.name
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'pk'):
        return value.pk
    return value


def snapshot_instance(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        data[field.name] = _serialize_audit_value(getattr(instance, field.attname))
    return data


def log_audit_event(instance, action, actor=None, changes=None, snapshot=None):
    from .models import AuditLog

    AuditLog.objects.create(
        content_type=ContentType.objects.get_for_model(instance.__class__),
        object_id=instance.pk or 0,
        actor=actor if actor is not None else get_current_user(),
        action=action,
        object_repr=str(instance),
        changes=changes or {},
        snapshot=snapshot if snapshot is not None else snapshot_instance(instance),
    )
