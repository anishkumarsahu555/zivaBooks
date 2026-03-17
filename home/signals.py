from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save, pre_save

from .audit import get_current_user
from .models import AuditLog, AuditedModel


def _serialize_value(value):
    if hasattr(value, 'name') and hasattr(value, 'storage'):
        return value.name
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'pk'):
        return value.pk
    return value


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        data[field.name] = _serialize_value(getattr(instance, field.attname))
    return data


def _diff(before, after):
    changes = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {'from': old_value, 'to': new_value}
    return changes


def _build_log(instance, action, changes, snapshot):
    AuditLog.objects.create(
        content_type=ContentType.objects.get_for_model(instance.__class__),
        object_id=instance.pk or 0,
        actor=get_current_user(),
        action=action,
        object_repr=str(instance),
        changes=changes,
        snapshot=snapshot,
    )


def audited_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            existing = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            instance._audit_before = {}
        else:
            instance._audit_before = _snapshot(existing)
    else:
        instance._audit_before = {}


def audited_post_save(sender, instance, created, **kwargs):
    after = _snapshot(instance)
    before = getattr(instance, '_audit_before', {})
    action = AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE
    changes = after if created else _diff(before, after)
    _build_log(instance, action, changes, after)
    if hasattr(instance, '_audit_before'):
        delattr(instance, '_audit_before')


def audited_post_delete(sender, instance, **kwargs):
    snapshot = _snapshot(instance)
    _build_log(instance, AuditLog.Action.DELETE, snapshot, snapshot)


def connect_audit_signals():
    for model in apps.get_models():
        if model in {AuditLog}:
            continue
        if issubclass(model, AuditedModel) and not model._meta.abstract:
            pre_save.connect(audited_pre_save, sender=model, dispatch_uid=f'audit_pre_save_{model._meta.label}')
            post_save.connect(audited_post_save, sender=model, dispatch_uid=f'audit_post_save_{model._meta.label}')
            post_delete.connect(audited_post_delete, sender=model, dispatch_uid=f'audit_post_delete_{model._meta.label}')
