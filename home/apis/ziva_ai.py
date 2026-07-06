import json
import os
import requests
import django.apps
import typing

# Monkeypatch typing._eval_type for Python 3.14 Pydantic compatibility
original_eval_type = getattr(typing, '_eval_type', None)
if original_eval_type:
    def patched_eval_type(t, globalns=None, localns=None, type_params=None, *args, **kwargs):
        kwargs.pop('prefer_fwd_module', None)
        return original_eval_type(t, globalns, localns, type_params, *args, **kwargs)
    typing._eval_type = patched_eval_type

import openai
from openai import OpenAI
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from home.tenancy import get_current_tenant
from home.models import TenantAiUsage
from home.services.ai_context import get_tenant_business_data_snapshot

def get_model_query_fields(model_class, allowed_base_fields):
    fields_to_query = []
    for field_name in allowed_base_fields:
        try:
            field = model_class._meta.get_field(field_name)
            if field.is_relation and field.many_to_one:
                target_model = field.related_model
                target_fields = [f.name for f in target_model._meta.fields]
                
                # Dynamically resolve relationships to human-readable strings if available
                resolved = False
                for identifier in ('name', 'invoiceNumber', 'voucherNumber', 'paymentID', 'sku', 'code'):
                    if identifier in target_fields:
                        fields_to_query.append(f"{field.name}__{identifier}")
                        resolved = True
                
                # Include ID of the relation as fallback
                fields_to_query.append(f"{field.name}_id")
            else:
                fields_to_query.append(field_name)
        except Exception:
            fields_to_query.append(field_name)
            
    return fields_to_query

def execute_tenant_lookup(tenant, model_name, filters_json=None, limit=15, is_superuser=False):
    allowed_models = {
        'tenant': ['id', 'name', 'legalName', 'gstNumber', 'phone', 'billingAddress'],
        'staffuser': ['id', 'name', 'phone', 'email', 'group', 'isActive'],
        'party': ['id', 'name', 'phone', 'openingBalance', 'isB2B', 'partyGroupID', 'assignTo', 'address'],
        'collection': ['id', 'paymentID', 'modeOfPayment', 'paidAmount', 'bankID', 'detail', 'remark', 'collectionDateTime', 'datetime', 'collectedBy', 'approvedBy', 'isApproved'],
        'sales': ['id', 'salesID', 'invoiceNumber', 'buildDate', 'amount', 'taxableAmount', 'gstAmount', 'modeOfPayment', 'bankID', 'isApproved', 'createdBy', 'approvedBy'],
        'order': ['id', 'orderNumber', 'buildDate', 'status', 'orderText', 'assignedTo', 'createdBy', 'approvedBy', 'remark'],
        'inventoryitem': ['id', 'name', 'sku', 'category', 'defaultSalesPrice', 'defaultPurchasePrice', 'mrp', 'gstRate', 'isActive'],
        'bank': ['id', 'name', 'accountNumber', 'description'],
        'purchaseinvoice': ['id', 'purchaseID', 'invoiceNumber', 'buildDate', 'gstTreatment', 'claimStatus', 'subtotalAmount', 'gstAmount', 'totalAmount', 'remark', 'createdBy', 'partyID'],
        'salesreturnvoucher': ['id', 'voucherNumber', 'entryDate', 'partyID', 'sales', 'totalAmount', 'createdBy', 'remark'],
        'purchasereturnvoucher': ['id', 'voucherNumber', 'entryDate', 'partyID', 'purchaseInvoice', 'totalAmount', 'createdBy', 'remark'],
        'cashcounter': ['id', 'invoiceNumber', 'counterID', 'buyerLegalName', 'buyerGstNumber', 'taxableAmount', 'gstAmount', 'mode', 'partyID'],
        'bookaccount': ['id', 'name', 'type', 'bank', 'openingBalance', 'isDefault'],
        'booktransaction': ['id', 'account', 'direction', 'amount', 'txDateTime', 'source', 'remark'],
        'attendance': ['id', 'staffID', 'datetime', 'isLogIn', 'loginDateTime', 'logoutDateTime'],
        'inventorywarehouse': ['id', 'name', 'code', 'address', 'isActive', 'isDefault'],
        'inventorystock': ['id', 'item', 'stockBucket', 'quantityOnHand']
    }

    try:
        aliases = {
            'posinvoice': 'cashcounter',
            'pos_invoice': 'cashcounter',
            'pos': 'cashcounter',
            'retailpos': 'cashcounter',
            'salesinvoice': 'sales',
            'sales_invoice': 'sales',
            'invoice': 'sales',
            'purchase': 'purchaseinvoice',
            'purchase_invoice': 'purchaseinvoice',
            'item': 'inventoryitem',
            'items': 'inventoryitem',
            'stock': 'inventorystock',
            'warehouse': 'inventorywarehouse',
            'staff': 'staffuser',
            'user': 'staffuser',
            'staff_user': 'staffuser',
            'customer': 'party',
            'supplier': 'party',
            'parties': 'party',
            'transaction': 'booktransaction',
            'transactions': 'booktransaction',
            'receipt': 'collection',
            'receipts': 'collection',
        }
        model_name_clean = model_name.strip().lower().replace(' ', '').replace('_', '')
        if model_name_clean in aliases:
            model_name_lower = aliases[model_name_clean]
        else:
            model_name_lower = model_name_clean

        if model_name_lower not in allowed_models:
            return {"error": f"Access denied or table '{model_name}' is not supported."}

        if model_name_lower == 'tenant' and not is_superuser:
            return {"error": "Access to the Tenant configuration is restricted to administrator users."}

        model_class_names = {
            'tenant': 'Tenant',
            'staffuser': 'StaffUser',
            'party': 'Party',
            'collection': 'Collection',
            'sales': 'Sales',
            'order': 'Order',
            'inventoryitem': 'InventoryItem',
            'bank': 'Bank',
            'purchaseinvoice': 'PurchaseInvoice',
            'salesreturnvoucher': 'SalesReturnVoucher',
            'purchasereturnvoucher': 'PurchaseReturnVoucher',
            'cashcounter': 'CashCounter',
            'bookaccount': 'BookAccount',
            'booktransaction': 'BookTransaction',
            'attendance': 'Attendance',
            'inventorywarehouse': 'InventoryWarehouse',
            'inventorystock': 'InventoryStock'
        }
        actual_model_name = model_class_names[model_name_lower]
        model_class = django.apps.apps.get_model('home', actual_model_name)
        if not model_class:
            return {"error": f"Table '{actual_model_name}' was not found."}

        # Query base: Superusers can look up records across all tenants, whereas regular users are locked to their own tenant
        if is_superuser:
            queryset = model_class.objects.all()
        else:
            if hasattr(model_class, 'tenant'):
                queryset = model_class.objects.filter(tenant=tenant)
            else:
                queryset = model_class.objects.all()

        if hasattr(model_class, 'isDeleted'):
            queryset = queryset.filter(isDeleted=False)

        filters = {}
        if filters_json:
            if isinstance(filters_json, dict):
                filters = filters_json
            elif isinstance(filters_json, str):
                try:
                    filters = json.loads(filters_json)
                except Exception:
                    return {"error": "Invalid format for filters."}

        # Apply filters safely
        cleaned_filters = {}
        for key, val in filters.items():
            base_key = key.split('__')[0]
            
            # Superusers can filter by tenant columns (e.g. tenant_id or tenant__name)
            if is_superuser and base_key in ('tenant', 'tenant_id'):
                cleaned_filters[key] = val
                continue
                
            if base_key not in allowed_models[model_name_lower]:
                return {"error": f"Field '{key}' is invalid or restricted for table '{actual_model_name}'."}
            cleaned_filters[key] = val

        queryset = queryset.filter(**cleaned_filters)

        limit = min(int(limit), 100) if limit else 15
        
        # Dynamically resolve relation sub-fields to obtain human-readable text details (e.g., partyID__name)
        fields = get_model_query_fields(model_class, allowed_models[model_name_lower])
        
        if is_superuser and hasattr(model_class, 'tenant') and model_name_lower != 'tenant':
            fields.extend(['tenant_id', 'tenant__name'])

        results = list(queryset.values(*fields)[:limit])
        
        # Format datetime/date/decimal values to serializable types
        import datetime
        from decimal import Decimal
        for row in results:
            for k, v in row.items():
                if isinstance(v, (datetime.datetime, datetime.date)):
                    row[k] = v.isoformat()
                elif isinstance(v, Decimal):
                    row[k] = float(v)

        return {"model": model_name, "count": len(results), "data": results}

    except Exception as e:
        return {"error": f"Failed to execute lookup: {str(e)}"}


@login_required
@require_POST
def ziva_ai_chat_api(request):
    provider = 'gemini'
    try:
        tenant = get_current_tenant(request)
        if not tenant:
            return JsonResponse({'error': 'No active tenant workspace found.'}, status=400)

        # Get or create usage stats
        ai_usage, created = TenantAiUsage.objects.get_or_create(
            tenant=tenant,
            defaults={
                'plan_name': 'Free',
                'max_queries_per_month': 5,
                'queries_used_this_month': 0
            }
        )
        ai_usage.check_and_reset_usage()

        # Check limits
        if ai_usage.max_queries_per_month >= 0 and ai_usage.queries_used_this_month >= ai_usage.max_queries_per_month:
            return JsonResponse({
                'error': 'Quota Exceeded',
                'message': f"You have exhausted your monthly limit of {ai_usage.max_queries_per_month} free queries.",
                'used': ai_usage.queries_used_this_month,
                'limit': ai_usage.max_queries_per_month,
                'plan': ai_usage.plan_name
            }, status=403)

        # Parse request body
        try:
            body = json.loads(request.body)
            user_message = body.get('message', '').strip()
            session_id = body.get('session_id', 'default').strip()
            session_title = body.get('session_title', 'General Chat').strip()
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON body.'}, status=400)

        if not user_message:
            return JsonResponse({'error': 'Message cannot be empty.'}, status=400)

        if not session_id:
            session_id = 'default'

        from home.models import ZivaAiChatMessage
        
        # Lookup if session already exists to reuse its title, otherwise generate one
        existing_msg = ZivaAiChatMessage.objects.filter(
            tenant=tenant,
            user=request.user,
            session_id=session_id,
            isDeleted=False
        ).first()

        if existing_msg:
            session_title = existing_msg.session_title
        elif user_message:
            cleaned_title = user_message[:30] + '...' if len(user_message) > 30 else user_message
            session_title = cleaned_title

        # Save user message to database
        ZivaAiChatMessage.objects.create(
            tenant=tenant,
            user=request.user,
            role='user',
            message=user_message,
            session_id=session_id,
            session_title=session_title
        )

        # Read Provider selection from payload
        provider = body.get('provider', 'gemini').strip().lower()
        if provider not in ('gemini', 'nvidia'):
            provider = 'gemini'

        # Read API Key
        if provider == 'nvidia':
            api_key = os.getenv('NVIDIA_API_KEY')
            if not api_key or api_key == 'your_nvidia_api_key_here':
                return JsonResponse({
                    'error': 'Configuration Error',
                    'message': 'NVIDIA_API_KEY is not configured in the server environment (.env file).'
                }, status=500)
            nvidia_model = os.getenv('NVIDIA_MODEL', 'meta/llama-3.1-70b-instruct').strip()
        else:
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                return JsonResponse({
                    'error': 'Configuration Error',
                    'message': 'GEMINI_API_KEY is not configured in the server environment (.env file).'
                }, status=500)

        # Compile basic tenant summary metadata context (for fallback and overview)
        tenant_context = get_tenant_business_data_snapshot(tenant)

        # Build privilege indicator
        privilege_desc = ""
        if request.user.is_superuser:
            privilege_desc = (
                "You are signed in as a System Administrator (Superuser). You have cross-tenant clearance! "
                "You can query records across any tenant in the database, view the 'Tenant' configuration, "
                "or filter by specific tenant IDs/names. Do not reveal raw password hashes or internal sensitive tokens."
            )
        else:
            privilege_desc = (
                f"You are signed in as a standard staff member of the tenant \"{tenant.name}\". "
                "You can only query records belonging to this tenant workspace."
            )

        # System Instruction
        system_instruction = f"""You are Ziva AI, the premium virtual financial and inventory assistant built for zivaBooks.
Your purpose is to answer questions, analyze transactions, verify stock levels, check customer balances, or summarize business activities.

{privilege_desc}

CRITICAL RULES:
1. READ-ONLY ENFORCEMENT: You must never allow, suggest, or pretend to perform write, create, update, or delete operations. If the user asks you to "add a sale", "create a party", "delete an order", "edit bank account", or "update attendance", you must respond that you have read-only access and cannot perform modifications.
2. Rely only on the lookup tools or the provided basic business summary. Do not invent any numbers, customers, staff, or sales.
3. Be professional, direct, concise, and structured. Use bold formatting for clarity.
4. Format all currency values in Indian Rupees (₹).
5. TABULAR DATA PREFERENCE: Whenever presenting lists of transactions, items, staff, collections, bank accounts, or outstanding balances, you MUST format the response in a Markdown table (e.g. using `| Header |`) instead of plain text lists. This allows the client-side system to render it as a highly readable, sortable database grid.
6. DYNAMIC LOOKUP METHOD: Always use the `lookup_tenant_data` tool to dynamically query the database for records when a query asks for specific invoices, collections, inventories, stock counts, attendances, or other business logs. Do not rely solely on memory or hardcoded summaries if details are missing.
7. DEEP LINKING: Whenever you present a Sales Invoice, Order, Staff User, Party (Customer/Supplier), Bank Account, or Inventory Item, format their name/identifier as a relative markdown link.
   - Sales Invoice: [/edit_sales/<id>/](/edit_sales/<id>/) (e.g. "[INV-1002](/edit_sales/15/)")
   - Order: [/edit_order/<id>/](/edit_order/<id>/) (e.g. "[ORD-5012](/edit_order/8/)")
   - Staff User: [/user_list/?search=<name>](/user_list/?search=<name>) (e.g. "[John Doe](/user_list/?search=John%20Doe)")
   - Customer/Supplier (Party): [/party_list/?search=<name>](/party_list/?search=<name>) (e.g. "[Acme Corp](/party_list/?search=Acme%20Corp)")
   - Bank Account: [/bank_list/](/bank_list/) (e.g. "[HDFC Bank](/bank_list/)")
   - Inventory Item: [/inventory/items/](/inventory/items/) (e.g. "[Browser Normal Item](/inventory/items/)")
   Ensure the labels are human-readable (names/numbers) and URLs are correct.
8. RESPONSE FORMAT: You MUST return your final text answer formatted as a JSON object containing keys "response" (markdown string) and "suggestions" (array of 2 to 4 highly relevant short follow-up action queries or follow-up questions).
9. FRIENDLY ERROR HANDLING: If a database lookup fails, is denied, or requests an unsupported database model (e.g. permission tables like 'GroupPermission'), you MUST NOT print the raw error message or lists of tables/fields to the user. Instead, output a simple, friendly, and generic response stating that the requested details are restricted or not supported by the assistant, and guide the user on what records they can query.

--- BASIC BUSINESS SNAPSHOT SUMMARY ---
{tenant_context}
---------------------------
"""

        # Fetch recent chat messages history directly from database to construct conversation context for current session
        db_chats = ZivaAiChatMessage.objects.filter(
            tenant=tenant,
            user=request.user,
            session_id=session_id,
            isDeleted=False
        ).order_by('-timestamp')[:16]
        
        # Reverse to chronological order
        db_chats = list(reversed(db_chats))

        nvidia_raw_text = ""
        gemini_raw_text = ""

        if provider == 'nvidia':
            # Setup OpenAI Messages Format
            openai_messages = [{"role": "system", "content": system_instruction}]
            for chat in db_chats:
                role = 'user' if chat.role == 'user' else 'assistant'
                openai_messages.append({
                    'role': role,
                    'content': chat.message
                })

            # Setup OpenAI Tools Format
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_tenant_data",
                        "description": (
                            "Search, filter, and retrieve read-only details of the tenant's business records. "
                            "Use this to fetch records dynamically for invoices, collections, orders, inventory items, "
                            "staff members, bank accounts, warehouses, cashbooks, return vouchers, or attendances. "
                            "If you are a superuser, you can also query the 'Tenant' configuration model."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "model_name": {
                                    "type": "string",
                                    "description": (
                                        "The name of the database table to query. "
                                        "Allowed choices and their valid query/filter fields:\n"
                                        "- Tenant: id, name, legalName, gstNumber, phone, billingAddress\n"
                                        "- StaffUser: id, name, phone, email, group, isActive\n"
                                        "- Party (Customers/Suppliers): id, name, phone, openingBalance, isB2B, partyGroupID, assignTo, address\n"
                                        "- Collection (Payments received): id, paymentID, modeOfPayment, paidAmount, bankID, detail, remark, collectionDateTime, datetime, collectedBy, approvedBy, isApproved\n"
                                        "- Sales (Sales Invoices): id, salesID, invoiceNumber, buildDate, amount, taxableAmount, gstAmount, modeOfPayment, bankID, isApproved, createdBy, approvedBy\n"
                                        "- Order: id, orderNumber, buildDate, status, orderText, assignedTo, createdBy, approvedBy, remark\n"
                                        "- InventoryItem: id, name, sku, category, defaultSalesPrice, defaultPurchasePrice, mrp, gstRate, isActive\n"
                                        "- Bank: id, name, accountNumber, description\n"
                                        "- PurchaseInvoice: id, purchaseID, invoiceNumber, buildDate, gstTreatment, claimStatus, subtotalAmount, gstAmount, totalAmount, remark, createdBy, partyID\n"
                                        "- SalesReturnVoucher: id, voucherNumber, entryDate, partyID, sales, totalAmount, createdBy, remark\n"
                                        "- PurchaseReturnVoucher: id, voucherNumber, entryDate, partyID, purchaseInvoice, totalAmount, createdBy, remark\n"
                                        "- CashCounter (Retail POS Invoices): id, invoiceNumber, counterID, buyerLegalName, buyerGstNumber, taxableAmount, gstAmount, mode, partyID\n"
                                        "- BookAccount: id, name, type, bank, openingBalance, isDefault\n"
                                        "- BookTransaction: id, account, direction, amount, txDateTime, source, remark\n"
                                        "- Attendance: id, staffID, datetime, isLogIn, loginDateTime, logoutDateTime\n"
                                        "- InventoryWarehouse: id, name, code, address, isActive, isDefault\n"
                                        "- InventoryStock: id, item, stockBucket, quantityOnHand"
                                    )
                                },
                                "filters_json": {
                                    "type": "string",
                                    "description": (
                                        "Optional JSON string containing key-value filter parameters "
                                        "(e.g., '{\"invoiceNumber\": \"INV-1002\"}' or '{\"partyID__name__icontains\": \"John\"}')."
                                    )
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Optional maximum number of records to return (defaults to 15)."
                                }
                            },
                            "required": ["model_name"]
                        }
                    }
                }
            ]

            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key
            )

            completion = client.chat.completions.create(
                model=nvidia_model,
                messages=openai_messages,
                tools=openai_tools,
                temperature=1.0,
                top_p=1.0,
                max_tokens=4096,
                seed=42,
                timeout=120
            )

            max_tool_loops = 5
            tool_loop_count = 0

            while tool_loop_count < max_tool_loops:
                try:
                    choice = completion.choices[0]
                    message_obj = choice.message
                except (AttributeError, IndexError):
                    break

                tool_calls = message_obj.tool_calls
                if tool_calls:
                    # Append simplified assistant query explanation instead of raw tool call object
                    openai_messages.append({
                        'role': 'assistant',
                        'content': f"I need to search the database records using the lookup_tenant_data tool for model '{tool_calls[0].function.name}'."
                    })

                    for tool_call in tool_calls:
                        function_name = tool_call.function.name
                        args_str = tool_call.function.arguments or '{}'
                        
                        try:
                            args = json.loads(args_str)
                        except Exception:
                            args = {}

                        if function_name == 'lookup_tenant_data':
                            model_name_arg = args.get('model_name')
                            filters_json = args.get('filters_json')
                            limit = args.get('limit', 15)

                            result = execute_tenant_lookup(
                                tenant=tenant,
                                model_name=model_name_arg,
                                filters_json=filters_json,
                                limit=limit,
                                is_superuser=request.user.is_superuser
                            )

                            # Append database lookup result as a user message turn
                            openai_messages.append({
                                'role': 'user',
                                'content': f"[System Data Lookup Result]: Here is the database query output for model '{model_name_arg}': {json.dumps(result)}. Please use this data to complete your analysis and reply to my previous request."
                            })

                    # Follow-up request to Nvidia (omitting tools to get final text response)
                    completion = client.chat.completions.create(
                        model=nvidia_model,
                        messages=openai_messages,
                        temperature=1.0,
                        top_p=1.0,
                        max_tokens=4096,
                        seed=42,
                        timeout=120
                    )
                    tool_loop_count += 1
                else:
                    nvidia_raw_text = message_obj.content or ""
                    break

        else:
            # Prepare Gemini REST Payload contents
            contents = []
            for chat in db_chats:
                role = 'user' if chat.role == 'user' else 'model'
                contents.append({
                    'role': role,
                    'parts': [{'text': chat.message}]
                })

            # Define Dynamic Lookup Tool (Gemini REST Schema)
            tools = [
                {
                    "functionDeclarations": [
                        {
                            "name": "lookup_tenant_data",
                            "description": (
                                "Search, filter, and retrieve read-only details of the tenant's business records. "
                                "Use this to fetch records dynamically for invoices, collections, orders, inventory items, "
                                "staff members, bank accounts, warehouses, cashbooks, return vouchers, or attendances. "
                                "If you are a superuser, you can also query the 'Tenant' configuration model."
                            ),
                            "parameters": {
                              "type": "OBJECT",
                              "properties": {
                                "model_name": {
                                  "type": "STRING",
                                  "description": (
                                      "The name of the database table to query. "
                                      "Allowed choices and their valid query/filter fields:\n"
                                      "- Tenant: id, name, legalName, gstNumber, phone, billingAddress\n"
                                      "- StaffUser: id, name, phone, email, group, isActive\n"
                                      "- Party (Customers/Suppliers): id, name, phone, openingBalance, isB2B, partyGroupID, assignTo, address\n"
                                      "- Collection (Payments received): id, paymentID, modeOfPayment, paidAmount, bankID, detail, remark, collectionDateTime, datetime, collectedBy, approvedBy, isApproved\n"
                                      "- Sales (Sales Invoices): id, salesID, invoiceNumber, buildDate, amount, taxableAmount, gstAmount, modeOfPayment, bankID, isApproved, createdBy, approvedBy\n"
                                      "- Order: id, orderNumber, buildDate, status, orderText, assignedTo, createdBy, approvedBy, remark\n"
                                      "- InventoryItem: id, name, sku, category, defaultSalesPrice, defaultPurchasePrice, mrp, gstRate, isActive\n"
                                      "- Bank: id, name, accountNumber, description\n"
                                      "- PurchaseInvoice: id, purchaseID, invoiceNumber, buildDate, gstTreatment, claimStatus, subtotalAmount, gstAmount, totalAmount, remark, createdBy, partyID\n"
                                      "- SalesReturnVoucher: id, voucherNumber, entryDate, partyID, sales, totalAmount, createdBy, remark\n"
                                      "- PurchaseReturnVoucher: id, voucherNumber, entryDate, partyID, purchaseInvoice, totalAmount, createdBy, remark\n"
                                      "- CashCounter (Retail POS Invoices): id, invoiceNumber, counterID, buyerLegalName, buyerGstNumber, taxableAmount, gstAmount, mode, partyID\n"
                                      "- BookAccount: id, name, type, bank, openingBalance, isDefault\n"
                                      "- BookTransaction: id, account, direction, amount, txDateTime, source, remark\n"
                                      "- Attendance: id, staffID, datetime, isLogIn, loginDateTime, logoutDateTime\n"
                                      "- InventoryWarehouse: id, name, code, address, isActive, isDefault\n"
                                      "- InventoryStock: id, item, stockBucket, quantityOnHand"
                                  )
                                },
                                "filters_json": {
                                  "type": "OBJECT",
                                  "description": (
                                      "Optional key-value filter parameters object "
                                      "(e.g., {\"invoiceNumber\": \"INV-1002\"} or {\"partyID__name__icontains\": \"John\"})."
                                  )
                                },
                                "limit": {
                                  "type": "INTEGER",
                                  "description": "Optional maximum number of records to return (defaults to 15)."
                                }
                              },
                              "required": ["model_name"]
                            }
                        }
                    ]
                }
            ]

            # Define Structured Response Schema Configuration
            generation_config = {
                'temperature': 0.2,
                'maxOutputTokens': 2048
            }

            payload = {
                'contents': contents,
                'systemInstruction': {
                    'parts': [{'text': system_instruction}]
                },
                'tools': tools,
                'generationConfig': generation_config
            }

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            headers = {'Content-Type': 'application/json'}

            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            res_json = response.json()

            max_tool_loops = 5
            tool_loop_count = 0

            while tool_loop_count < max_tool_loops:
                try:
                    candidate = res_json['candidates'][0]
                    content_obj = candidate['content']
                    part = content_obj['parts'][0]
                except (KeyError, IndexError):
                    break

                # Check if model wants to call the tool
                if 'functionCall' in part:
                    fc = part['functionCall']
                    function_name = fc['name']
                    args = fc.get('args', {})

                    if function_name == 'lookup_tenant_data':
                        model_name_arg = args.get('model_name')
                        filters_json = args.get('filters_json')
                        limit = args.get('limit', 15)

                        # Execute the lookup dynamically
                        result = execute_tenant_lookup(
                            tenant=tenant,
                            model_name=model_name_arg,
                            filters_json=filters_json,
                            limit=limit,
                            is_superuser=request.user.is_superuser
                        )

                        # Append model's functionCall turn to dialogue contents
                        contents.append({
                            'role': 'model',
                            'parts': [part]
                        })

                        # Append server's functionResponse turn to dialogue contents
                        contents.append({
                            'role': 'function',
                            'parts': [{
                                'functionResponse': {
                                    'name': 'lookup_tenant_data',
                                    'response': result
                                }
                            }]
                        })

                        # Follow-up request to Gemini
                        followup_payload = {
                            'contents': contents,
                            'systemInstruction': {
                                'parts': [{'text': system_instruction}]
                            },
                            'tools': tools,
                            'generationConfig': generation_config
                        }

                        followup_response = requests.post(url, headers=headers, json=followup_payload, timeout=120)
                        followup_response.raise_for_status()
                        res_json = followup_response.json()
                        tool_loop_count += 1
                    else:
                        break
                else:
                    gemini_raw_text = part.get('text', '')
                    break

        raw_text = nvidia_raw_text if provider == 'nvidia' else gemini_raw_text
        if not raw_text:
            provider_label = 'Nvidia GLM-5.2' if provider == 'nvidia' else 'Google Gemini'
            return JsonResponse({'error': 'API Error', 'message': f'Received empty or malformed response from {provider_label}.'}, status=502)


        # Parse the structured JSON response
        ai_text = ""
        suggestions = []
        
        # Pre-clean markdown code fences if present in the raw text response
        cleaned_text = raw_text.strip()
        if cleaned_text.startswith("```"):
            lines = cleaned_text.splitlines()
            if len(lines) >= 2:
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned_text = "\n".join(lines).strip()

        try:
            parsed_data = json.loads(cleaned_text)
            ai_text = parsed_data.get('response', '').strip()
            suggestions = parsed_data.get('suggestions', [])
        except Exception:
            # Safe fallback if API returns raw plain text instead of JSON
            ai_text = raw_text.strip()
            suggestions = ["Show profile summary", "Show stock levels", "List active staff"]

        if not ai_text:
            ai_text = raw_text.strip()

        # Save AI generated bot response to database
        ZivaAiChatMessage.objects.create(
            tenant=tenant,
            user=request.user,
            role='bot',
            message=ai_text,
            session_id=session_id,
            session_title=session_title
        )

        # Successful query: update and save usage
        ai_usage.queries_used_this_month += 1
        ai_usage.save()

        return JsonResponse({
            'response': ai_text,
            'suggestions': suggestions,
            'session_id': session_id,
            'session_title': session_title,
            'used': ai_usage.queries_used_this_month,
            'limit': ai_usage.max_queries_per_month,
            'plan': ai_usage.plan_name
        })

    except openai.OpenAIError as e:
        provider_name = 'Nvidia GLM-5.2' if provider == 'nvidia' else 'Google Gemini'
        message = str(e)
        status_code = 502
        if isinstance(e, openai.APIStatusError):
            status_code = e.status_code
            if e.status_code == 401:
                message = f"Request to {provider_name} failed: Unauthorized. Please check your credentials in the .env file."
            elif e.status_code == 429:
                message = f"You have hit the {provider_name} API rate limit (Too Many Requests). Please wait a moment and try again."
        return JsonResponse({
            'error': 'AI Service Unavailable',
            'message': message
        }, status=status_code)
    except requests.exceptions.RequestException as e:
        provider_name = 'Nvidia GLM-5.2' if provider == 'nvidia' else 'Google Gemini'
        if e.response is not None:
            if e.response.status_code == 429:
                return JsonResponse({
                    'error': 'Rate Limit Exceeded',
                    'message': f'You have hit the {provider_name} API rate limit (Too Many Requests). Please wait a moment and try again.'
                }, status=429)
            elif e.response.status_code == 400:
                return JsonResponse({
                    'error': 'Bad Request',
                    'message': f'The request was rejected by {provider_name}. This is usually caused by prompt safety filters or content constraints.'
                }, status=400)
            elif e.response.status_code == 401:
                return JsonResponse({
                    'error': 'Unauthorized',
                    'message': f'Request to {provider_name} failed: Unauthorized. Please check your credentials in the .env file.'
                }, status=401)
        return JsonResponse({
            'error': 'AI Service Unavailable',
            'message': f"Request to {provider_name} API failed: {str(e)}"
        }, status=502)
    except Exception as e:
        return JsonResponse({
            'error': 'Internal Server Error',
            'message': str(e)
        }, status=500)


def ziva_ai_history_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    tenant = get_current_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'No active tenant workspace'}, status=400)

    try:
        offset = int(request.GET.get('offset', 0))
        limit = int(request.GET.get('limit', 50))
    except ValueError:
        return JsonResponse({'error': 'Invalid offset or limit'}, status=400)

    session_id = request.GET.get('session_id', 'default')

    from home.models import ZivaAiChatMessage
    db_chats = ZivaAiChatMessage.objects.filter(
        tenant=tenant,
        user=request.user,
        session_id=session_id,
        isDeleted=False
    ).order_by('-timestamp')[offset:offset+limit]

    chats_list = []
    for chat in db_chats:
        chats_list.append({
            'role': chat.role,
            'message': chat.message,
            'timestamp': chat.timestamp.isoformat(),
            'formatted_time': chat.timestamp.strftime("%d %b %Y, %I:%M %p")
        })

    return JsonResponse({
        'chats': chats_list,
        'has_more': len(chats_list) == limit
    })


def ziva_ai_sessions_list_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    tenant = get_current_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'No active tenant workspace'}, status=400)

    from home.models import ZivaAiChatMessage
    from django.db.models import Max

    sessions = ZivaAiChatMessage.objects.filter(
        tenant=tenant,
        user=request.user,
        isDeleted=False
    ).values('session_id').annotate(
        latest_timestamp=Max('timestamp')
    ).order_by('-latest_timestamp')

    sessions_list = []
    for s in sessions:
        sess_id = s['session_id']
        latest_msg = ZivaAiChatMessage.objects.filter(
            tenant=tenant,
            user=request.user,
            session_id=sess_id,
            isDeleted=False
        ).order_by('-timestamp').first()
        
        title = latest_msg.session_title if latest_msg else "General Chat"
        sessions_list.append({
            'session_id': sess_id,
            'session_title': title,
            'latest_timestamp': s['latest_timestamp'].isoformat() if s['latest_timestamp'] else None
        })

    return JsonResponse({'sessions': sessions_list})


@require_POST
def ziva_ai_sessions_delete_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    tenant = get_current_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'No active tenant workspace'}, status=400)

    try:
        body = json.loads(request.body)
        session_id = body.get('session_id')
    except json.JSONDecodeError:
        session_id = request.POST.get('session_id')

    if not session_id:
        return JsonResponse({'error': 'session_id is required'}, status=400)

    from home.models import ZivaAiChatMessage
    ZivaAiChatMessage.objects.filter(
        tenant=tenant,
        user=request.user,
        session_id=session_id
    ).update(isDeleted=True)

    return JsonResponse({'success': True})


@require_POST
def ziva_ai_sessions_rename_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    tenant = get_current_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'No active tenant workspace'}, status=400)

    try:
        body = json.loads(request.body)
        session_id = body.get('session_id')
        title = body.get('title')
    except json.JSONDecodeError:
        session_id = request.POST.get('session_id')
        title = request.POST.get('title')

    if not session_id or not title:
        return JsonResponse({'error': 'session_id and title are required'}, status=400)

    from home.models import ZivaAiChatMessage
    ZivaAiChatMessage.objects.filter(
        tenant=tenant,
        user=request.user,
        session_id=session_id
    ).update(session_title=title)

    return JsonResponse({'success': True})
