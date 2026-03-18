from django.core.management.base import BaseCommand

from home.party_ledger import backfill_party_ledger


class Command(BaseCommand):
    help = "Backfill Party Ledger entries from existing Collection/Sales/CashCounter sources."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None, help="Limit to one tenant id")

    def handle(self, *args, **options):
        tenant_id = options.get("tenant_id")
        backfill_party_ledger(tenant_id=tenant_id)
        if tenant_id:
            self.stdout.write(self.style.SUCCESS(f"Party Ledger backfill complete for tenant {tenant_id}."))
        else:
            self.stdout.write(self.style.SUCCESS("Party Ledger backfill complete for all tenants."))

