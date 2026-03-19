from django.test import TestCase

from home.models import Order
from home.tests.utils import SeededTenantMixin


class OrdersApiTests(SeededTenantMixin, TestCase):
    def test_order_create_update_approve_delete(self):
        self.client.force_login(self.owner)

        create = self.client.post(
            "/api/add_order_api/",
            data={
                "buildDate": "19/03/2026",
                "party": str(self.party.pk),
                "orderText": "API order text",
                "remark": "API order remark",
                "lat": "0.0",
                "lng": "0.0",
                "loc": "N/A",
            },
        )
        self.assertEqual(create.status_code, 200)
        payload = create.json()
        self.assertEqual(payload.get("message"), "success")
        order_id = payload.get("order_id")
        self.assertTrue(order_id)

        update = self.client.post(
            "/api/update_order_api/",
            data={
                "ID": str(order_id),
                "orderText": "API order text (updated)",
                "remark": "updated",
                "status": Order.Status.SUBMITTED,
            },
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.json().get("message"), "success")

        approve = self.client.post("/api/approve_order/", data={"userID": str(order_id)})
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json().get("message"), "success")

        list_resp = self.client.get("/api/OrdersByAdminListJson/", data={"draw": "1", "start": "0", "length": "5"})
        self.assertEqual(list_resp.status_code, 200)

        delete = self.client.post("/api/delete_order/", data={"userID": str(order_id)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

