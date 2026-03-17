from django.core.management.base import BaseCommand

from home.apiView import seed_default_groups
from home.models import StaffGroup, Tenant


class Command(BaseCommand):
    help = "Seed default Django groups, permissions, and tenant staff groups."

    def handle(self, *args, **options):
        seed_default_groups()
        from django.contrib.auth.models import Group

        for tenant in Tenant.objects.all():
            for group_name in ['Owner', 'Admin', 'Moderator', 'Collection', 'SalesAdmin', 'CashCounter', 'NormalStaff', 'Customer', 'Viewer']:
                base_group = Group.objects.filter(name=group_name).first()
                django_group, _ = Group.objects.get_or_create(name=f'{tenant.slug}:{group_name}')
                if base_group is not None:
                    django_group.permissions.set(base_group.permissions.all())
                staff_group, created = StaffGroup.objects.get_or_create(
                    tenant=tenant,
                    name=group_name,
                    defaults={'description': f'Default {group_name} role', 'djangoGroup': django_group},
                )
                if staff_group.djangoGroup_id is None and django_group is not None:
                    staff_group.djangoGroup = django_group
                    staff_group.save(update_fields=['djangoGroup', 'lastUpdatedOn'])
        self.stdout.write(self.style.SUCCESS('Seeded roles and permissions.'))
