import sys
import threading

from utils.logger import logger
from home.models import WhatsappMessage, WhatsappMessageStatus
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs


class MessageThread(threading.Thread):
    def __init__(self, number, message, partyName):
        self.number = number
        self.message = message
        self.partyName = partyName
        self._is_running = True
        threading.Thread.__init__(self)

    def run(self):
        while self._is_running:
            msg = tenant_qs(get_current_tenant(None), WhatsappMessage).filter(isDeleted__exact=False).last()
            if msg and msg.used < msg.balance:
                try:
                    obj = WhatsappMessageStatus()
                    assign_tenant(obj, get_current_tenant(None))
                    obj.phone = self.number
                    obj.message = self.message
                    obj.messageTo = self.partyName
                    obj.status = 'Fail'
                    obj.save()
                    msg.used = msg.used + 1
                    msg.save()
                    logger.info("[Messages] Dispatched message to %s (%s)", self.partyName, self.number)
                except Exception as exc:
                    logger.error("[Messages] Failed to dispatch message to %s: %s", self.number, str(exc))
                    obj = WhatsappMessageStatus()
                    assign_tenant(obj, get_current_tenant(None))
                    obj.phone = self.number
                    obj.message = self.message
                    obj.messageTo = self.partyName
                    obj.status = 'Fail'
                    obj.save()
            else:
                logger.info("[Messages] Message quota exceeded or no active configuration for tenant")
                obj = WhatsappMessageStatus()
                assign_tenant(obj, get_current_tenant(None))
                obj.phone = self.number
                obj.message = self.message
                obj.messageTo = self.partyName
                obj.status = 'Fail'
                obj.save()
            self.stop()

    def stop(self):
        self._is_running = False


def send_message(number, message, party):
    if 'test' in sys.argv:
        MessageThread(number, message, party).run()
    else:
        MessageThread(number, message, party).start()


def send_whatsapp_message(number, message):
    msg = tenant_qs(get_current_tenant(None), WhatsappMessage).filter(isDeleted__exact=False).last()
    if msg and msg.used < msg.balance:
        msg.used = msg.used + 1
        msg.save()
