from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .audit import log_audit_event, snapshot_instance
from .models import AuditLog, CredentialShareToken
from .tenancy import get_current_tenant


DEFAULT_CREDENTIAL_LINK_MINUTES = 30


def hash_credential_share_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def create_credential_share_token(
    request_or_user,
    *,
    user,
    subject_type: str,
    staff_user=None,
    party=None,
    created_by=None,
    expiry_minutes: int = DEFAULT_CREDENTIAL_LINK_MINUTES,
):
    tenant = get_current_tenant(request_or_user)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_credential_share_token(raw_token)
    expires_at = timezone.now() + timedelta(minutes=expiry_minutes)

    with transaction.atomic():
        CredentialShareToken.objects.filter(
            user=user,
            subjectType=subject_type,
            usedAt__isnull=True,
            isDeleted=False,
        ).update(isDeleted=True)
        token = CredentialShareToken.objects.create(
            tenant=tenant,
            user=user,
            staffUser=staff_user,
            party=party,
            subjectType=subject_type,
            tokenHash=token_hash,
            expiresAt=expires_at,
            createdBy=created_by,
        )
    return raw_token, token


def get_valid_credential_share_token(raw_token: str):
    if not raw_token:
        return None
    token_hash = hash_credential_share_token(raw_token)
    token = (
        CredentialShareToken.objects
        .select_related('tenant', 'user', 'staffUser', 'party')
        .filter(tokenHash=token_hash, isDeleted=False)
        .first()
    )
    if token is None:
        return None
    now = timezone.now()
    if token.usedAt is not None or token.expiresAt <= now:
        return None
    return token


def credential_share_token_status(raw_token: str):
    if not raw_token:
        return None, 'missing'
    token_hash = hash_credential_share_token(raw_token)
    token = (
        CredentialShareToken.objects
        .select_related('tenant', 'user', 'staffUser', 'party')
        .filter(tokenHash=token_hash, isDeleted=False)
        .first()
    )
    if token is None:
        return None, 'missing'
    if token.usedAt is not None:
        return token, 'used'
    if token.expiresAt <= timezone.now():
        return token, 'expired'
    return token, 'valid'


def consume_credential_share_token(token: CredentialShareToken, new_password: str, claimed_from_ip: str | None = None):
    if token.usedAt is not None or token.expiresAt <= timezone.now():
        return False

    with transaction.atomic():
        user = token.user
        user.set_password(new_password)
        user.save(update_fields=['password'])

        if token.staffUser_id:
            token.staffUser.userPassword = new_password
            token.staffUser.save(update_fields=['userPassword'])
            log_audit_event(
                token.staffUser,
                AuditLog.Action.PASSWORD_CHANGE,
                changes={'userPassword': {'from': '***', 'to': '***'}},
                snapshot=snapshot_instance(token.staffUser),
            )
        if token.party_id:
            token.party.password = new_password
            token.party.save(update_fields=['password'])
            log_audit_event(
                token.party,
                AuditLog.Action.PASSWORD_CHANGE,
                changes={'password': {'from': '***', 'to': '***'}},
                snapshot=snapshot_instance(token.party),
            )

        token.usedAt = timezone.now()
        if claimed_from_ip:
            token.claimedFromIP = claimed_from_ip
        token.save(update_fields=['usedAt', 'claimedFromIP', 'lastUpdatedOn'])

        CredentialShareToken.objects.filter(
            user=user,
            usedAt__isnull=True,
            isDeleted=False,
        ).exclude(pk=token.pk).update(isDeleted=True)

    return True
