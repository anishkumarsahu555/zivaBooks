from django.test import TestCase
from django.urls import reverse
from unittest import mock
from home.tests.utils import SeededTenantMixin
from home.models import TenantAiUsage
from datetime import date, timedelta

class ZivaAiTests(SeededTenantMixin, TestCase):
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
        self.assertEqual(response.json()['suggestions'], ['Show balance', 'Show invoices'])
        
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
        self.assertEqual(response.json()['response'], 'Cleaned response text.')
        self.assertEqual(response.json()['suggestions'], ['Chip 1', 'Chip 2'])

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
        self.assertEqual(response.json()['response'], 'Multi-turn tool execution succeeded.')
        self.assertEqual(response.json()['suggestions'], ['Ok'])
        self.assertEqual(mock_post.call_count, 3)

