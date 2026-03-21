from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from home.models import Collection, Order, StaffUser, TenantMembership
from home.tests.utils import SeededTenantMixin


User = get_user_model()


class OrderCollectionApprovalPermissionTests(SeededTenantMixin, TestCase):
    def test_collector_without_approve_permission_cannot_approve_collection(self):
        collection = Collection.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            paymentID="TEST-COLLECTION-APPROVAL",
            modeOfPayment="Cash",
            paidAmount=500,
            collectedBy=self.owner_staff,
            collectionDateTime=datetime.now(),
            isApproved=False,
        )

        self.client.force_login(self.collector)
        response = self.client.post(
            reverse("homeApp:approve_collection"),
            data={"userID": str(collection.pk)},
        )

        self.assertEqual(response.status_code, 403)
        collection.refresh_from_db()
        self.assertFalse(collection.isApproved)

    def test_orders_manager_without_approve_permission_cannot_approve_order(self):
        manager_user = User.objects.create_user(
            username="orders.manager.only",
            email="orders.manager.only@example.com",
            password="secret123",
        )
        manager_perms = list(
            Permission.objects.filter(
                content_type__app_label="home",
                codename__in=["can_manage_orders", "view_order", "change_order", "add_order"],
            )
        )
        manager_user.user_permissions.set(manager_perms)
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=manager_user,
            role=TenantMembership.Role.ADMIN,
            isDefault=True,
        )
        StaffUser.objects.create(
            tenant=self.tenant,
            user_ID=manager_user,
            name="Orders Manager Only",
            phone="9999999999",
            email=manager_user.email,
            username=manager_user.username,
            userPassword="secret123",
            address="Orders Desk",
            partyGroupID=self.owner_staff.partyGroupID,
            staffGroup=self.any_staff_group(),
            isActive="Active",
        )

        order = Order.objects.create(
            tenant=self.tenant,
            orderNumber="ORD-APPROVAL-TEST",
            partyID=self.party,
            buildDate=date.today(),
            createdBy=self.owner_staff,
            status=Order.Status.SUBMITTED,
        )

        self.client.force_login(manager_user)
        response = self.client.post(
            reverse("homeApp:approve_order"),
            data={"userID": str(order.pk)},
        )

        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.SUBMITTED)
