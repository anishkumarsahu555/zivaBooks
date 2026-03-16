from django.urls import path

from .apiView import *
from .views import *

urlpatterns = [
    # pages
    path('home/', homepage, name='homepage'),
    path('', loginPage, name='loginPage'),
    path('logout/', user_logout, name='logout'),
    path('postLogin/', postLogin, name='UserLogin'),

    # collection
    path('collection_home/', collection_home, name='collection_home'),
    path('customer_home/', customer_home, name='customer_home'),
    path('customer/change-password/', customer_change_password, name='customer_change_password'),
    path('myProfile/', my_profile_collection, name='my_profile_collection'),
    path('add_collection/', add_collection, name='add_collection'),
    path('my_collection/', my_collection, name='my_collection'),
    path('api/CollectionByStaffListJson/', CollectionByStaffListJson.as_view(), name='CollectionByStaffListJson'),

    #
    # # admin
    path('my_profile/', my_profile, name='my_profile'),
    path('admin_home/', admin_home, name='admin_home'),
    path('user_list/', user_list, name='user_list'),
    path('party_group_list/', party_group_list, name='party_group_list'),
    path('party_list/', party_list, name='party_list'),
    path('bank_list/', bank_list, name='bank_list'),

    # Admin Collection
    path('take_collection/', take_collection, name='take_collection'),
    path('edit_collection/<int:id>/', edit_collection, name='edit_collection'),
    path('collection_list/', collection_list, name='collection_list'),
    path('cheque_reminder_list/', cheque_reminder_list, name='cheque_reminder_list'),
    path('api/delete_collection/', delete_collection, name='delete_collection'),
    path('api/approve_collection/', approve_collection, name='approve_collection'),
    path('api/CollectionByAdminListJson/', CollectionByAdminListJson.as_view(), name='CollectionByAdminListJson'),
    path('api/ChequeReminderCollectionListJson/', ChequeReminderCollectionListJson.as_view(),
         name='ChequeReminderCollectionListJson'),

    # # api Staff
    path('api/add_staff_api/', add_staff_api, name='add_staff_api'),
    path('api/delete_staff_user/', delete_staff_user, name='delete_staff_user'),
    path('api/get_staff_user_detail/', get_staff_user_detail, name='get_staff_user_detail'),
    path('api/edit_staff_api/', edit_staff_api, name='edit_staff_api'),
    path('api/StaffUserListJson/', StaffUserListJson.as_view(), name='StaffUserListJson'),

    # # api party group
    path('api/add_party_group_api/', add_party_group_api, name='add_party_group_api'),
    path('api/delete_part_group/', delete_part_group, name='delete_part_group'),
    path('api/get_party_group_detail/', get_party_group_detail, name='get_party_group_detail'),
    path('api/edit_part_group_api/', edit_part_group_api, name='edit_part_group_api'),
    path('api/PartyGroupListJson/', PartyGroupListJson.as_view(), name='PartyGroupListJson'),
    #
    # api Banks
    path('api/add_bank_api/', add_bank_api, name='add_bank_api'),
    path('api/BankListJson/', BankListJson.as_view(), name='BankListJson'),
    path('api/delete_bank/', delete_bank, name='delete_bank'),
    path('api/get_bank_detail/', get_bank_detail, name='get_bank_detail'),
    path('api/edit_bank_api/', edit_bank_api, name='edit_bank_api'),

    # api party
    path('api/add_party_api/', add_party_api, name='add_party_api'),
    path('api/generate_party_users_api/', generate_party_users_api, name='generate_party_users_api'),
    path('api/PartyListJson/', PartyListJson.as_view(), name='PartyListJson'),
    path('api/get_party_detail/', get_party_detail, name='get_party_detail'),
    path('api/edit_party_api/', edit_party_api, name='edit_party_api'),
    path('api/delete_party/', delete_party, name='delete_party'),
    path('api/list_party_api/', list_party_api, name='list_party_api'),
    path('api/list_party_by_executive_or_station_api/', list_party_by_executive_or_station_api,
         name='list_party_by_executive_or_station_api'),

    # Collection
    path('api/add_collection_by_staff_api/', add_collection_by_staff_api, name='add_collection_by_staff_api'),
    path('api/add_collection_by_admin_api/', add_collection_by_admin_api, name='add_collection_by_admin_api'),
    path('api/edit_collection_by_admin_api/', edit_collection_by_admin_api, name='edit_collection_by_admin_api'),

    # # change password
    path('api/change_password_api/', change_password_api, name='change_password_api'),

    path('api/get_admin_dashboard_report_api/', get_admin_dashboard_report_api, name='get_admin_dashboard_report_api'),
    path('api/get_staff_dashboard_report_api/', get_staff_dashboard_report_api, name='get_staff_dashboard_report_api'),

    #
    path('api/generate_collection_report/', generate_collection_report, name='generate_collection_report'),
    # path('get_party_data/', get_party_data, name='get_party_data'),
    path('generate_collection_date/', generate_collection_date, name='generate_collection_date'),

    # staff user
    path('staff_home/', staff_home, name='staff_home'),

    # attendance
    path('add_attendance/', add_attendance, name='add_attendance'),
    path('attendance_report/', attendance_report, name='attendance_report'),
    path('attendance_report_admin/', attendance_report_admin, name='attendance_report_admin'),
    path('api/add_attendance_api/', add_attendance_api, name='add_attendance_api'),
    path('api/generate_attendance_pdf_staff_report/', generate_attendance_pdf_staff_report,
         name='generate_attendance_pdf_staff_report'),
    path('api/generate_attendance_pdf_admin_report/', generate_attendance_pdf_admin_report,
         name='generate_attendance_pdf_admin_report'),
    path('api/StaffAttendanceListJson/', StaffAttendanceListJson.as_view(), name='StaffAttendanceListJson'),
    path('api/AdminAttendanceListJson/', AdminAttendanceListJson.as_view(), name='AdminAttendanceListJson'),

    # Sales
    path('add_sales/', add_sales, name='add_sales'),
    path('sales_list/', sales_list, name='sales_list'),
    path('my_sales_list/', my_sales_list, name='my_sales_list'),
    path('api/add_sales_by_admin_api/', add_sales_by_admin_api, name='add_sales_by_admin_api'),
    path('api/update_sales_by_admin_api/', update_sales_by_admin_api, name='update_sales_by_admin_api'),
    path('api/SalesByAdminListJson/', SalesByAdminListJson.as_view(), name='SalesByAdminListJson'),
    path('api/SalesByStaffListJson/', SalesByStaffListJson.as_view(), name='SalesByStaffListJson'),
    path('api/generate_sales_report/', generate_sales_report, name='generate_sales_report'),
    path('api/delete_sales/', delete_sales, name='delete_sales'),
    path('api/send_message_sales/', send_message_sales, name='send_message_sales'),
    path('edit_sales/<int:id>/', edit_sales, name='edit_sales'),

    # message List
    path('message_list/', message_list, name='message_list'),
    path('api/MessageListJson/', MessageListJson.as_view(), name='MessageListJson'),
    path('api/re_send_message_sales/', re_send_message_sales, name='re_send_message_sales'),
    path('api/enable_tally_post_api/', enable_tally_post_api, name='enable_tally_post_api'),

    # cashCounter
    path('cash_counter_home/', cash_counter_home, name='cash_counter_home'),
    path('cash_counter/', cash_counter, name='cash_counter'),
    path('cash_counter_collection/', cash_counter_collection, name='cash_counter_collection'),
    path('edit_cash_counter_collection/<int:id>/', edit_cash_counter_collection, name='edit_cash_counter_collection'),
    path('edit_cash_counter/<int:id>/', edit_cash_counter, name='edit_cash_counter'),
    path('my_cash_counter_list/', my_cash_counter_list, name='my_cash_counter_list'),
    path('api/add_cash_counter_entry_api/', add_cash_counter_entry_api, name='add_cash_counter_entry_api'),
    path('api/edit_cash_counter_collection_by_admin_api/', edit_cash_counter_collection_by_admin_api,
         name='edit_cash_counter_collection_by_admin_api'),
    path('api/CashCounterByStaffListJson/', CashCounterByStaffListJson.as_view(), name='CashCounterByStaffListJson'),
    path('api/CardCounterByStaffListJson/', CardCounterByStaffListJson.as_view(), name='CardCounterByStaffListJson'),
    path('api/ReturnCounterByStaffListJson/', ReturnCounterByStaffListJson.as_view(),
         name='ReturnCounterByStaffListJson'),
    path('api/CreditCounterByStaffListJson/', CreditCounterByStaffListJson.as_view(),
         name='CreditCounterByStaffListJson'),
    path('api/MixCounterByStaffListJson/', MixCounterByStaffListJson.as_view(), name='MixCounterByStaffListJson'),
    path('api/CollectionCounterByStaffListJson/', CollectionCounterByStaffListJson.as_view(),
         name='CollectionCounterByStaffListJson'),
    path('api/AdvanceCounterByStaffListJson/', AdvanceCounterByStaffListJson.as_view(),
         name='AdvanceCounterByStaffListJson'),
    path('api/ExpenseCounterByStaffListJson/', ExpenseCounterByStaffListJson.as_view(),
         name='ExpenseCounterByStaffListJson'),
    path('api/get_cash_counter_dashboard_report_api/', get_cash_counter_dashboard_report_api,
         name='get_cash_counter_dashboard_report_api'),
    path('api/get_cash_counter_dashboard_report_admin_api/', get_cash_counter_dashboard_report_admin_api,
         name='get_cash_counter_dashboard_report_admin_api'),
    path('api/delete_cash_counter/', delete_cash_counter, name='delete_cash_counter'),
    path('api/update_cash_counter_by_admin_api/', update_cash_counter_by_admin_api,
         name='update_cash_counter_by_admin_api'),
    path('api/generate_cash_counter_report/', generate_cash_counter_report, name='generate_cash_counter_report'),
    path('api/add_collection_from_cash_counter_api/', add_collection_from_cash_counter_api,
         name='add_collection_from_cash_counter_api'),
    path('api/CashCounterCollectionListJson/', CashCounterCollectionListJson.as_view(),
         name='CashCounterCollectionListJson'),
    path('api/get_all_unsent_messages/', get_all_unsent_messages, name='get_all_unsent_messages'),
    path('api/update_sent_msg_status/', update_sent_msg_status, name='update_sent_msg_status'),

]
