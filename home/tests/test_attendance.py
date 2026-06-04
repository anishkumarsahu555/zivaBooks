from datetime import date, datetime, timedelta
from django.test import TestCase
from django.urls import reverse

from home.models import Attendance, StaffUser
from home.tests.utils import SeededTenantMixin, tiny_png


class AttendanceIntegrationTests(SeededTenantMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.collector_staff = StaffUser.objects.get(tenant=self.tenant, user_ID=self.collector)
        # Clear existing seeded attendance data for this tenant
        Attendance.objects.filter(tenant=self.tenant).delete()

    def test_att_01_add_attendance_flow(self):
        """
        ATT-01: Adding staff attendance via /api/add_attendance_api/
        and verifying database record creation.
        """
        # First login as collector
        self.client.force_login(self.collector)

        # GET /add_attendance/ to initialize today's Attendance object
        response = self.client.get(reverse("homeApp:add_attendance"))
        self.assertEqual(response.status_code, 200)

        # Verify today's Attendance object exists
        today = date.today()
        att_qs = Attendance.objects.filter(
            tenant=self.tenant,
            staffID=self.collector_staff,
            datetime__date=today
        )
        self.assertTrue(att_qs.exists())
        att = att_qs.first()
        self.assertFalse(att.isLogIn)
        self.assertFalse(att.isLogOut)

        # Test Error: Missing location
        response = self.client.post(
            reverse("homeApp:add_attendance_api"),
            data={
                "stat": "Login",
                "lat": "0.0",
                "lng": "0.0",
                "remark": "Missing location"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "error")
        self.assertIn("location is required", response.json().get("error", ""))

        # Test Success: Login
        photo = tiny_png("login.png")
        response = self.client.post(
            reverse("homeApp:add_attendance_api"),
            data={
                "stat": "Login",
                "lat": "12.9716",
                "lng": "77.5946",
                "remark": "Clock in",
                "photo": photo
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "success")

        att.refresh_from_db()
        self.assertTrue(att.isLogIn)
        self.assertEqual(att.login_remark, "Clock in")
        self.assertEqual(att.login_latitude, "12.9716")
        self.assertEqual(att.login_longitude, "77.5946")
        self.assertTrue(bool(att.loginPhoto))

        # Test Success: Logout
        photo_out = tiny_png("logout.png")
        response = self.client.post(
            reverse("homeApp:add_attendance_api"),
            data={
                "stat": "Logout",
                "lat": "12.9720",
                "lng": "77.5950",
                "remark": "Clock out",
                "photo": photo_out
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "success")

        att.refresh_from_db()
        self.assertTrue(att.isLogOut)
        self.assertEqual(att.logout_remark, "Clock out")
        self.assertEqual(att.logout_latitude, "12.9720")
        self.assertEqual(att.logout_longitude, "77.5950")
        self.assertTrue(bool(att.logoutPhoto))

    def test_att_02_staff_attendance_report_scoping(self):
        """
        ATT-02: Scoping of staff attendance reports
        (/api/StaffAttendanceListJson/ showing only their own records).
        """
        # Create a mock attendance record for another staff user
        sales_staff = StaffUser.objects.get(tenant=self.tenant, user_ID=self.sales)
        Attendance.objects.create(
            tenant=self.tenant,
            staffID=sales_staff,
            datetime=datetime.now() - timedelta(days=1),
            isLogIn=True,
            loginDateTime=datetime.now() - timedelta(days=1)
        )

        # Create a mock attendance record for collector
        Attendance.objects.create(
            tenant=self.tenant,
            staffID=self.collector_staff,
            datetime=datetime.now() - timedelta(days=1),
            isLogIn=True,
            loginDateTime=datetime.now() - timedelta(days=1)
        )

        self.client.force_login(self.collector)
        response = self.client.get(
            reverse("homeApp:StaffAttendanceListJson"),
            {"startDate": (date.today() - timedelta(days=2)).strftime("%d/%m/%Y"),
             "endDate": date.today().strftime("%d/%m/%Y")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json().get("data", [])
        
        # Verify that sales_staff attendance is NOT in the collector's datatable response
        # In StaffAttendanceListJson, the columns are: Date, LoginTime, LoginPhoto, LoginLocation, LoginRemark, LogoutTime, LogoutPhoto, LogoutLocation, LogoutRemark
        # Since it only contains own staff records, we should only see 1 record (the collector's)
        self.assertEqual(len(data), 1)

    def test_att_03_admin_attendance_report_scoping(self):
        """
        ATT-03: Admin-level attendance reports
        (/api/AdminAttendanceListJson/ showing all staff records for the tenant).
        """
        # Ensure we have records for both collector and sales
        sales_staff = StaffUser.objects.get(tenant=self.tenant, user_ID=self.sales)
        
        Attendance.objects.filter(tenant=self.tenant).delete()
        
        Attendance.objects.create(
            tenant=self.tenant,
            staffID=sales_staff,
            datetime=datetime.now() - timedelta(days=1),
            isLogIn=True,
            loginDateTime=datetime.now() - timedelta(days=1)
        )
        Attendance.objects.create(
            tenant=self.tenant,
            staffID=self.collector_staff,
            datetime=datetime.now() - timedelta(days=1),
            isLogIn=True,
            loginDateTime=datetime.now() - timedelta(days=1)
        )

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("homeApp:AdminAttendanceListJson"),
            {
                "startDate": (date.today() - timedelta(days=2)).strftime("%d/%m/%Y"),
                "endDate": date.today().strftime("%d/%m/%Y"),
                "staffID": "All"
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json().get("data", [])
        
        # Admin should see both staff members' records (total 2 records)
        # In AdminAttendanceListJson, column index 1 contains the staff name.
        self.assertEqual(len(data), 2)
        names = [row[1] for row in data]
        self.assertIn(sales_staff.name, names)
        self.assertIn(self.collector_staff.name, names)

    def test_att_04_export_endpoints(self):
        """
        ATT-04: Invoking PDF/Excel generation endpoints for attendance reports
        to ensure they return standard responses.
        """
        # Create a mock record
        Attendance.objects.create(
            tenant=self.tenant,
            staffID=self.collector_staff,
            datetime=datetime.now(),
            isLogIn=True,
            loginDateTime=datetime.now()
        )

        self.client.force_login(self.collector)

        # Excel Export (Staff)
        excel_resp = self.client.get(
            reverse("homeApp:export_attendance_list_excel"),
            {
                "startDate": date.today().strftime("%d/%m/%Y"),
                "endDate": date.today().strftime("%d/%m/%Y")
            }
        )
        self.assertEqual(excel_resp.status_code, 200)
        self.assertEqual(excel_resp["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        # PDF Export (Staff)
        pdf_resp = self.client.get(
            reverse("homeApp:generate_attendance_pdf_staff_report"),
            {
                "cDate": date.today().strftime("%d/%m/%Y"),
                "eDate": date.today().strftime("%d/%m/%Y")
            }
        )
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")

        # Excel Staff report
        excel_staff_resp = self.client.get(
            reverse("homeApp:generate_attendance_excel_staff_report"),
            {
                "cDate": date.today().strftime("%d/%m/%Y"),
                "eDate": date.today().strftime("%d/%m/%Y")
            }
        )
        self.assertEqual(excel_staff_resp.status_code, 200)

        # Monthly PDF staff report
        pdf_month_resp = self.client.get(
            reverse("homeApp:generate_attendance_monthly_pdf_staff_report"),
            {
                "cDate": date.today().strftime("%d/%m/%Y")
            }
        )
        self.assertEqual(pdf_month_resp.status_code, 200)
        self.assertEqual(pdf_month_resp["Content-Type"], "application/pdf")

        # Monthly Excel staff report
        excel_month_resp = self.client.get(
            reverse("homeApp:generate_attendance_monthly_excel_staff_report"),
            {
                "cDate": date.today().strftime("%d/%m/%Y")
            }
        )
        self.assertEqual(excel_month_resp.status_code, 200)
