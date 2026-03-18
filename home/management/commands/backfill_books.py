from __future__ import annotations

from django.core.management.base import BaseCommand

from home.books import backfill_books_for_tenant
from home.models import Tenant


class Command(BaseCommand):
    help = "Backfill cashbook/bankbook transactions from existing Collection/CashCounter data."

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default=None, help='Tenant slug (optional).')

    def handle(self, *args, **options):
        tenant_slug = options.get('tenant')
        tenants = Tenant.objects.all().order_by('id')
        if tenant_slug:
            tenants = tenants.filter(slug=tenant_slug)

        count = 0
        for tenant in tenants:
            self.stdout.write(f'Backfilling books for tenant: {tenant.slug}')
            backfill_books_for_tenant(tenant)
            count += 1
        self.stdout.write(self.style.SUCCESS(f'Backfilled {count} tenant(s).'))

