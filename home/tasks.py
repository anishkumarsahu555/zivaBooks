from celery import shared_task
from home.apis.ziva_ai import _ziva_ai_generate_in_background


@shared_task(name='home.tasks.ziva_ai_generate_task')
def ziva_ai_generate_task(
    tenant_id, user_id, user_message, session_id, session_title, bot_msg_id,
    provider, is_superuser, api_key, nvidia_model, system_instruction,
    permission_names=None,
):
    _ziva_ai_generate_in_background(
        tenant_id, user_id, user_message, session_id, session_title, bot_msg_id,
        provider, is_superuser, api_key, nvidia_model, system_instruction, permission_names
    )
