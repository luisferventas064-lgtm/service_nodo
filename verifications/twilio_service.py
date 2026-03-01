import logging

from django.conf import settings


logger = logging.getLogger(__name__)


def send_sms(phone_number: str, message: str) -> None:
    if settings.TWILIO_MODE == "mock":
        logger.info("[MOCK SMS] to=%s message=%s", phone_number, message)
        return

    if settings.TWILIO_MODE == "test":
        logger.info("[TEST MODE SMS] to=%s message=%s", phone_number, message)
        return

    if settings.TWILIO_MODE == "live":
        from twilio.rest import Client

        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
        )
        client.messages.create(
            body=message,
            from_=settings.TWILIO_FROM_NUMBER,
            to=phone_number,
        )
        return

    raise ValueError("Invalid TWILIO_MODE")
