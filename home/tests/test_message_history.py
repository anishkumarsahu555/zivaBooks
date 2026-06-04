from datetime import datetime, timedelta
from django.test import TestCase
from django.urls import reverse

from home.models import Collection, WhatsappMessageStatus
from home.tests.utils import SeededTenantMixin


class MessageHistoryIntegrationTests(SeededTenantMixin, TestCase):
    def setUp(self):
        super().setUp()
        WhatsappMessageStatus.objects.all().delete()
        from home.models import WhatsappMessage
        WhatsappMessage.objects.all().delete()

    def test_msg_01_message_list_view_and_json(self):
        """
        MSG-01: Message list page loads (/message_list/)
        and Datatable API (/api/MessageListJson/) returns messages.
        """
        self.client.force_login(self.admin)

        # Create a WhatsApp message status record
        WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543210",
            message="Test message 1",
            messageTo="Customer",
            status="Success"
        )
        WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543211",
            message="Test message 2",
            messageTo="Customer",
            status="Fail"
        )

        # 1. Load list page
        response = self.client.get(reverse("homeApp:message_list"))
        self.assertEqual(response.status_code, 200)

        # 2. Get datatable JSON
        json_resp = self.client.get(reverse("homeApp:MessageListJson"))
        self.assertEqual(json_resp.status_code, 200)
        data = json_resp.json().get("data", [])
        
        # Verify both messages are in the output
        self.assertEqual(len(data), 2)
        phones = [row[1] for row in data]
        self.assertIn("9876543210", phones)
        self.assertIn("9876543211", phones)

    def test_msg_02_resend_message(self):
        """
        MSG-02: Resending messages via /api/re_send_message_sales/
        (without package configured, it returns success).
        """
        self.client.force_login(self.admin)

        status = WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543210",
            message="Resend test",
            messageTo="Customer",
            status="Fail"
        )

        response = self.client.post(
            reverse("homeApp:re_send_message_sales"),
            data={"userID": str(status.pk)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "success")

    def test_msg_03_unsent_message_polling(self):
        """
        MSG-03: Polling unsent messages via /api/get_all_unsent_messages/.
        """
        self.client.force_login(self.admin)

        # Unsent messages have: isDeleted=False, status='Fail', phone length=10, datetime within 5 days
        WhatsappMessageStatus.objects.all().delete()

        # 1. Matches all criteria
        m1 = WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543210",
            message="Polled message",
            messageTo="Customer",
            status="Fail"
        )

        # 2. Excluded: phone length != 10
        WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="98765432101",
            message="Wrong phone length",
            messageTo="Customer",
            status="Fail"
        )

        # 3. Excluded: status is Success
        WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543212",
            message="Already sent",
            messageTo="Customer",
            status="Success"
        )

        # 4. Excluded: older than 5 days
        m4 = WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543213",
            message="Too old",
            messageTo="Customer",
            status="Fail"
        )
        # Manually backdate m4.datetime
        WhatsappMessageStatus.objects.filter(pk=m4.pk).update(datetime=datetime.now() - timedelta(days=6))

        response = self.client.get(reverse("homeApp:get_all_unsent_messages"))
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Only m1 should be polled
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], m1.id)
        self.assertEqual(data[0]["phone"], "9876543210")

    def test_msg_04_update_sent_msg_status(self):
        """
        MSG-04: Updating sent message status via /api/update_sent_msg_status/.
        """
        self.client.force_login(self.admin)

        status = WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543210",
            message="Status change",
            messageTo="Customer",
            status="Fail"
        )

        response = self.client.post(
            reverse("homeApp:update_sent_msg_status"),
            data={"id": str(status.pk)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "success")

        status.refresh_from_db()
        self.assertEqual(status.status, "Success")

    def test_msg_05_enable_tally_post_api(self):
        """
        MSG-05: Toggling Tally posting flags via /api/enable_tally_post_api/.
        """
        self.client.force_login(self.admin)

        # Create an approved Collection
        collection = Collection.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            collectedBy=self.owner_staff,
            paidAmount=150.0,
            modeOfPayment="Cash",
            isApproved=True,
            isTallied=False
        )

        # POST /api/enable_tally_post_api/
        response = self.client.post(
            reverse("homeApp:enable_tally_post_api"),
            data={"tallyColID": str(collection.pk)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "success")

        collection.refresh_from_db()
        self.assertTrue(collection.isTallied)
