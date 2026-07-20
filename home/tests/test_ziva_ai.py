from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import Group, Permission
from unittest import mock
from home.tests.utils import SeededTenantMixin
from home.models import TenantAiUsage
from datetime import date, timedelta

class ZivaAiTests(SeededTenantMixin, TestCase):
    def setUp(self):
        super().setUp()
        import os
        self._orig_gemini_key = os.environ.get('GEMINI_API_KEY')
        self._orig_nvidia_key = os.environ.get('NVIDIA_API_KEY')
        os.environ['GEMINI_API_KEY'] = 'dummy_gemini_key'
        os.environ['NVIDIA_API_KEY'] = 'dummy_nvidia_key'

    def tearDown(self):
        import os
        if self._orig_gemini_key is not None:
            os.environ['GEMINI_API_KEY'] = self._orig_gemini_key
        else:
            os.environ.pop('GEMINI_API_KEY', None)
            
        if self._orig_nvidia_key is not None:
            os.environ['NVIDIA_API_KEY'] = self._orig_nvidia_key
        else:
            os.environ.pop('NVIDIA_API_KEY', None)
        super().tearDown()

    def test_ziva_ai_page_requires_login(self):
        response = self.client.get(reverse('homeApp:ziva_ai'))
        # Should redirect to login page
        self.assertEqual(response.status_code, 302)

    def test_ziva_ai_page_blocks_customer_login(self):
        # Log in as a customer
        self.client.force_login(self.customer)
        response = self.client.get(reverse('homeApp:ziva_ai'))
        # Customers should be redirected
        self.assertEqual(response.status_code, 302)
        self.assertTrue('customer_home' in response.url)

    def test_ziva_ai_page_allows_owner_and_initializes_usage(self):
        # Log in as owner
        self.client.force_login(self.owner)
        response = self.client.get(reverse('homeApp:ziva_ai'))
        self.assertEqual(response.status_code, 200)
        
        # Verify TenantAiUsage was created
        usage_exists = TenantAiUsage.objects.filter(tenant=self.tenant).exists()
        self.assertTrue(usage_exists)
        usage = TenantAiUsage.objects.get(tenant=self.tenant)
        self.assertEqual(usage.plan_name, 'Free')
        self.assertEqual(usage.max_queries_per_month, 5)
        self.assertEqual(usage.queries_used_this_month, 0)

    def test_ziva_ai_chat_api_requires_login(self):
        response = self.client.post(reverse('homeApp:ziva_ai_chat_api'), {}, content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_missing_provider_key_does_not_create_a_pending_message(self):
        from home.models import ZivaAiChatMessage

        self.client.force_login(self.owner)
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': ''}, clear=False):
            response = self.client.post(
                reverse('homeApp:ziva_ai_chat_api'),
                {'message': 'Hello AI'},
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(ZivaAiChatMessage.objects.filter(tenant=self.tenant, user=self.owner).count(), 0)

    def test_ziva_ai_chat_api_blocks_quota_exceeded(self):
        # Log in as owner
        self.client.force_login(self.owner)
        
        # Create usage row and set usage to max limit
        usage, _ = TenantAiUsage.objects.get_or_create(
            tenant=self.tenant,
            defaults={'plan_name': 'Free', 'max_queries_per_month': 5}
        )
        usage.queries_used_this_month = 5
        usage.save()
        
        response = self.client.post(
            reverse('homeApp:ziva_ai_chat_api'),
            {'message': 'Test query'},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'Quota Exceeded')

    def test_ziva_ai_chat_api_resets_monthly_usage_lazily(self):
        # Log in as owner
        self.client.force_login(self.owner)
        
        # Create usage row and set usage to max limit, but last_reset_date to last month
        usage, _ = TenantAiUsage.objects.get_or_create(
            tenant=self.tenant,
            defaults={'plan_name': 'Free', 'max_queries_per_month': 5}
        )
        usage.queries_used_this_month = 5
        # Set last_reset_date to a month ago
        usage.last_reset_date = date.today() - timedelta(days=32)
        usage.save()
        
        # The page view should trigger the lazy reset
        response = self.client.get(reverse('homeApp:ziva_ai'))
        self.assertEqual(response.status_code, 200)
        
        # Reload usage from DB
        usage.refresh_from_db()
        self.assertEqual(usage.queries_used_this_month, 0)
        self.assertEqual(usage.last_reset_date, date.today())

    @mock.patch('requests.post')
    def test_ziva_ai_chat_api_saves_messages(self, mock_post):
        from unittest import mock
        from home.models import ZivaAiChatMessage
        
        # mock response from Gemini generateContent endpoint
        mock_res = mock.Mock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'text': '{"response": "Hello, I am Ziva AI.", "suggestions": ["Show balance", "Show invoices"]}'
                    }]
                }
            }]
        }
        mock_post.return_value = mock_res
        
        self.client.force_login(self.owner)
        
        response = self.client.post(
            reverse('homeApp:ziva_ai_chat_api'),
            {'message': 'Hello AI'},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'pending')
        message_id = response.json()['message_id']

        status_response = self.client.get(
            reverse('homeApp:ziva_ai_chat_status_api', kwargs={'message_id': message_id})
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()['status'], 'completed')
        self.assertEqual(status_response.json()['suggestions'], ['Show balance', 'Show invoices'])
        self.assertEqual(status_response.json()['response'], 'Hello, I am Ziva AI.')
        
        messages = ZivaAiChatMessage.objects.filter(tenant=self.tenant, user=self.owner).order_by('timestamp')
        self.assertEqual(messages.count(), 2)
        
        # User message
        user_msg = messages[0]
        self.assertEqual(user_msg.role, 'user')
        self.assertEqual(user_msg.message, 'Hello AI')
        
        # Bot message
        bot_msg = messages[1]
        self.assertEqual(bot_msg.role, 'bot')
        self.assertEqual(bot_msg.message, 'Hello, I am Ziva AI.')

    @mock.patch('requests.post')
    def test_ziva_ai_chat_api_parses_fenced_markdown_json(self, mock_post):
        from unittest import mock
        from home.models import ZivaAiChatMessage
        
        # mock response with Markdown code fences around JSON
        mock_res = mock.Mock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'text': '```json\n{\n  "response": "Cleaned response text.",\n  "suggestions": ["Chip 1", "Chip 2"]\n}\n```'
                    }]
                }
            }]
        }
        mock_post.return_value = mock_res
        
        self.client.force_login(self.owner)
        
        response = self.client.post(
            reverse('homeApp:ziva_ai_chat_api'),
            {'message': 'Hello AI'},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'pending')
        message_id = response.json()['message_id']

        status_response = self.client.get(
            reverse('homeApp:ziva_ai_chat_status_api', kwargs={'message_id': message_id})
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()['status'], 'completed')
        self.assertEqual(status_response.json()['response'], 'Cleaned response text.')
        self.assertEqual(status_response.json()['suggestions'], ['Chip 1', 'Chip 2'])

    @mock.patch('requests.post')
    def test_ziva_ai_chat_api_supports_sequential_multi_tool_calls(self, mock_post):
        from unittest import mock
        
        # Define 3 sequential mocked responses
        mock_res_1 = mock.Mock()
        mock_res_1.status_code = 200
        mock_res_1.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'functionCall': {
                            'name': 'lookup_tenant_data',
                            'args': {
                                'model_name': 'Sales'
                            }
                        }
                    }]
                }
            }]
        }

        mock_res_2 = mock.Mock()
        mock_res_2.status_code = 200
        mock_res_2.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'functionCall': {
                            'name': 'lookup_tenant_data',
                            'args': {
                                'model_name': 'Collection'
                            }
                        }
                    }]
                }
            }]
        }

        mock_res_3 = mock.Mock()
        mock_res_3.status_code = 200
        mock_res_3.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'text': '{"response": "Multi-turn tool execution succeeded.", "suggestions": ["Ok"]}'
                    }]
                }
            }]
        }

        mock_post.side_effect = [mock_res_1, mock_res_2, mock_res_3]

        self.client.force_login(self.owner)
        
        response = self.client.post(
            reverse('homeApp:ziva_ai_chat_api'),
            {'message': 'show comparison stats'},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'pending')
        message_id = response.json()['message_id']

        status_response = self.client.get(
            reverse('homeApp:ziva_ai_chat_status_api', kwargs={'message_id': message_id})
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()['status'], 'completed')
        self.assertEqual(status_response.json()['response'], 'Multi-turn tool execution succeeded.')
        self.assertEqual(status_response.json()['suggestions'], ['Ok'])
        self.assertEqual(mock_post.call_count, 3)

    @mock.patch('openai.resources.chat.completions.Completions.create')
    def test_ziva_ai_chat_api_nvidia_saves_messages(self, mock_create):
        from unittest import mock
        from home.models import ZivaAiChatMessage
        
        mock_choice = mock.Mock()
        mock_choice.message = mock.Mock()
        mock_choice.message.content = '{"response": "Hello from Nvidia GLM-5.2.", "suggestions": ["Nvidia Ok"]}'
        mock_choice.message.tool_calls = None
        
        mock_completion = mock.Mock()
        mock_completion.choices = [mock_choice]
        mock_create.return_value = mock_completion
        
        self.client.force_login(self.owner)
        
        # Configure NVIDIA_API_KEY inside environment patch
        with mock.patch.dict('os.environ', {'NVIDIA_API_KEY': 'valid_key'}):
            response = self.client.post(
                reverse('homeApp:ziva_ai_chat_api'),
                {'message': 'Hello Nvidia', 'provider': 'nvidia'},
                content_type='application/json'
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'pending')
            message_id = response.json()['message_id']

            status_response = self.client.get(
                reverse('homeApp:ziva_ai_chat_status_api', kwargs={'message_id': message_id})
            )
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.json()['status'], 'completed')
            self.assertEqual(status_response.json()['response'], 'Hello from Nvidia GLM-5.2.')
            self.assertEqual(status_response.json()['suggestions'], ['Nvidia Ok'])
            
            messages = ZivaAiChatMessage.objects.filter(tenant=self.tenant, user=self.owner).order_by('timestamp')
            self.assertEqual(messages.count(), 2)
            
            # User message
            user_msg = messages[0]
            self.assertEqual(user_msg.role, 'user')
            self.assertEqual(user_msg.message, 'Hello Nvidia')
            
            # Bot message
            bot_msg = messages[1]
            self.assertEqual(bot_msg.role, 'bot')
            self.assertEqual(bot_msg.message, 'Hello from Nvidia GLM-5.2.')

    @mock.patch('openai.resources.chat.completions.Completions.create')
    def test_ziva_ai_chat_api_nvidia_supports_sequential_multi_tool_calls(self, mock_create):
        from unittest import mock
        
        # Define 2 sequential mocked responses
        mock_tc = mock.Mock()
        mock_tc.id = 'call-nv-1'
        mock_tc.type = 'function'
        mock_tc.function = mock.Mock()
        mock_tc.function.name = 'lookup_tenant_data'
        mock_tc.function.arguments = '{"model_name": "Sales"}'

        mock_choice_1 = mock.Mock()
        mock_choice_1.message = mock.Mock()
        mock_choice_1.message.content = None
        mock_choice_1.message.tool_calls = [mock_tc]

        mock_completion_1 = mock.Mock()
        mock_completion_1.choices = [mock_choice_1]

        mock_choice_2 = mock.Mock()
        mock_choice_2.message = mock.Mock()
        mock_choice_2.message.content = '{"response": "Nvidia multi-turn tool success.", "suggestions": ["Nvidia Chip"]}'
        mock_choice_2.message.tool_calls = None

        mock_completion_2 = mock.Mock()
        mock_completion_2.choices = [mock_choice_2]

        mock_create.side_effect = [mock_completion_1, mock_completion_2]

        self.client.force_login(self.owner)
        
        with mock.patch.dict('os.environ', {'NVIDIA_API_KEY': 'valid_key'}):
            response = self.client.post(
                reverse('homeApp:ziva_ai_chat_api'),
                {'message': 'show nvidia sales data', 'provider': 'nvidia'},
                content_type='application/json'
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'pending')
            message_id = response.json()['message_id']

            status_response = self.client.get(
                reverse('homeApp:ziva_ai_chat_status_api', kwargs={'message_id': message_id})
            )
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.json()['status'], 'completed')
            self.assertEqual(status_response.json()['response'], 'Nvidia multi-turn tool success.')
            self.assertEqual(status_response.json()['suggestions'], ['Nvidia Chip'])
            self.assertEqual(mock_create.call_count, 2)

    def test_ziva_ai_history_api_requires_login(self):
        response = self.client.get(reverse('homeApp:ziva_ai_history_api'))
        self.assertEqual(response.status_code, 401)

    def test_ziva_ai_history_api_returns_paginated_history(self):
        self.client.force_login(self.owner)
        from home.models import ZivaAiChatMessage
        
        # Create 60 dummy messages
        for i in range(60):
            ZivaAiChatMessage.objects.create(
                tenant=self.tenant,
                user=self.owner,
                role='user' if i % 2 == 0 else 'bot',
                message=f"Message {i}"
            )
            
        # Call history API
        response = self.client.get(reverse('homeApp:ziva_ai_history_api'), {'offset': 0, 'limit': 40})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['chats']), 40)
        self.assertTrue(data['has_more'])
        
        # Fetch next page
        response = self.client.get(reverse('homeApp:ziva_ai_history_api'), {'offset': 40, 'limit': 40})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['chats']), 20)
        self.assertFalse(data['has_more'])

    def test_ziva_ai_sessions_apis(self):
        self.client.force_login(self.owner)
        from home.models import ZivaAiChatMessage

        # 1. Create message under specific session
        sess_id = "test-session-123"
        ZivaAiChatMessage.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role='user',
            message="Hello Session",
            session_id=sess_id,
            session_title="Initial Title"
        )

        # 2. Test sessions list API
        list_url = reverse('homeApp:ziva_ai_sessions_list_api')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        sessions = response.json().get('sessions', [])
        # Ensure our session is listed
        my_session = next((s for s in sessions if s['session_id'] == sess_id), None)
        self.assertIsNotNone(my_session)
        self.assertEqual(my_session['session_title'], "Initial Title")

        # 3. Test sessions rename API
        rename_url = reverse('homeApp:ziva_ai_sessions_rename_api')
        response = self.client.post(
            rename_url,
            {'session_id': sess_id, 'title': "New Session Title"},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('success'))

        # Verify renaming happened in database
        refreshed_msg = ZivaAiChatMessage.objects.filter(session_id=sess_id).first()
        self.assertEqual(refreshed_msg.session_title, "New Session Title")

        # 4. Test sessions delete API
        delete_url = reverse('homeApp:ziva_ai_sessions_delete_api')
        response = self.client.post(
            delete_url,
            {'session_id': sess_id},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('success'))

        # Verify message is soft-deleted
        deleted_exists = ZivaAiChatMessage.objects.filter(session_id=sess_id, isDeleted=False).exists()
        self.assertFalse(deleted_exists)

    def test_non_admin_staff_cannot_access_ziva_ai(self):
        # Log in as a standard staff user (e.g. sales)
        self.client.force_login(self.sales)
        
        # 1. Page view should be forbidden
        response = self.client.get(reverse('homeApp:ziva_ai'))
        self.assertEqual(response.status_code, 403)
        
        # 2. Chat API should be forbidden
        response = self.client.post(
            reverse('homeApp:ziva_ai_chat_api'),
            {'message': 'Hello AI'},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)
        
        # 3. History API should be forbidden
        response = self.client.get(reverse('homeApp:ziva_ai_history_api'))
        self.assertEqual(response.status_code, 403)
        
        # 4. Sessions list API should be forbidden
        response = self.client.get(reverse('homeApp:ziva_ai_sessions_list_api'))
        self.assertEqual(response.status_code, 403)

    def test_staff_with_ziva_ai_permission_can_access_page_and_history_api(self):
        permission = Permission.objects.get(content_type__app_label='home', codename='can_access_ziva_ai')
        tenant_admin_group = Group.objects.get(name=f'{self.tenant.slug}:Tenant Admin')
        tenant_admin_group.permissions.remove(permission)
        self.sales.user_permissions.add(permission)

        self.client.force_login(self.sales)

        response = self.client.get(reverse('homeApp:ziva_ai'))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse('homeApp:ziva_ai_history_api'))
        self.assertEqual(response.status_code, 200)

    def test_group_based_data_lookup_permissions(self):
        from home.apis.ziva_ai import execute_tenant_lookup
        from django.contrib.auth.models import Group
        from django.contrib.auth import get_user_model

        User = get_user_model()

        # Get groups
        sales_group = Group.objects.get(name=f'{self.tenant.slug}:Sales Desk')
        collector_group = Group.objects.get(name=f'{self.tenant.slug}:Field Collections')
        admin_group = Group.objects.get(name=f'{self.tenant.slug}:Tenant Admin')

        # 1. Create a Sales User
        sales_user = User.objects.create_user(username='test_sales_user_perms', password='pass')
        sales_user.groups.add(sales_group)
        
        # Test sales model (should succeed)
        res_sales = execute_tenant_lookup(
            tenant=self.tenant,
            model_name='sales',
            is_superuser=False,
            user=sales_user
        )
        self.assertNotIn('error', res_sales)

        # Test bank model (should also succeed now under uniform behavior)
        res_bank = execute_tenant_lookup(
            tenant=self.tenant,
            model_name='bank',
            is_superuser=False,
            user=sales_user
        )
        self.assertNotIn('error', res_bank)

        # 2. Create a Collector User
        collector_user = User.objects.create_user(username='test_collector_user_perms', password='pass')
        collector_user.groups.add(collector_group)
        
        # Test collection model (should succeed)
        res_col = execute_tenant_lookup(
            tenant=self.tenant,
            model_name='collection',
            is_superuser=False,
            user=collector_user
        )
        self.assertNotIn('error', res_col)

        # Test bank model (should also succeed now under uniform behavior)
        res_col_bank = execute_tenant_lookup(
            tenant=self.tenant,
            model_name='bank',
            is_superuser=False,
            user=collector_user
        )
        self.assertNotIn('error', res_col_bank)

        # 3. Create an Admin User
        admin_user = User.objects.create_user(username='test_admin_user_perms', password='pass')
        admin_user.groups.add(admin_group)

        # Test bank model (should succeed)
        res_admin_bank = execute_tenant_lookup(
            tenant=self.tenant,
            model_name='bank',
            is_superuser=False,
            user=admin_user
        )
        self.assertNotIn('error', res_admin_bank)


from django.test import TransactionTestCase, Client
import threading
import time

class ZivaAiConcurrencyTests(SeededTenantMixin, TransactionTestCase):
    def setUp(self):
        self.setUpTestData()
        super().setUp()
        import os
        self._orig_gemini_key = os.environ.get('GEMINI_API_KEY')
        self._orig_nvidia_key = os.environ.get('NVIDIA_API_KEY')
        os.environ['GEMINI_API_KEY'] = 'dummy_gemini_key'
        os.environ['NVIDIA_API_KEY'] = 'dummy_nvidia_key'

    def tearDown(self):
        import os
        if self._orig_gemini_key is not None:
            os.environ['GEMINI_API_KEY'] = self._orig_gemini_key
        else:
            os.environ.pop('GEMINI_API_KEY', None)
            
        if self._orig_nvidia_key is not None:
            os.environ['NVIDIA_API_KEY'] = self._orig_nvidia_key
        else:
            os.environ.pop('NVIDIA_API_KEY', None)
        super().tearDown()

    @mock.patch('requests.post')
    def test_concurrent_chat_requests(self, mock_post):
        # We simulate a slow API response from Gemini to ensure overlapping requests
        def slow_mock_post(*args, **kwargs):
            time.sleep(0.5)
            mock_res = mock.Mock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                'candidates': [{
                    'content': {
                        'parts': [{
                            'text': '{"response": "Hello from Ziva AI.", "suggestions": ["Ok"]}'
                        }]
                    }
                }]
            }
            return mock_res

        mock_post.side_effect = slow_mock_post

        # We will spawn 3 concurrent threads making chat requests
        results = []
        errors = []

        def make_request(thread_idx):
            from django.db import connections
            client = Client()
            # Login as owner
            client.force_login(self.owner)
            try:
                response = client.post(
                    reverse('homeApp:ziva_ai_chat_api'),
                    {'message': f'Hello from thread {thread_idx}'},
                    content_type='application/json'
                )
                results.append((thread_idx, response.status_code, response.json()))
            except Exception as e:
                errors.append((thread_idx, str(e)))
            finally:
                connections.close_all()

        threads = []
        for i in range(3):
            t = threading.Thread(target=make_request, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Check for any exception raised during request execution (e.g. database locks)
        self.assertEqual(len(errors), 0, f"Encountered concurrent errors: {errors}")

        # Check that all requests returned status 200 (pending)
        for thread_idx, status_code, body in results:
            self.assertEqual(status_code, 200)
            self.assertEqual(body['status'], 'pending')


