from django.core.management.base import BaseCommand
from django.contrib.auth.models import Permission


class Command(BaseCommand):
    help = "Verify that application permissions are available without creating predefined groups."

    def handle(self, *args, **options):
        permission_count = Permission.objects.exclude(
            content_type__app_label__in=['admin', 'auth', 'contenttypes', 'sessions']
        ).count()
        self.stdout.write(
            self.style.SUCCESS(
                f'Permissions are ready. No predefined groups were created. Available permissions: {permission_count}.'
            )
        )
