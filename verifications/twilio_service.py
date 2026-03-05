import logging

from django.conf import settings


logger = logging.getLogger(__name__)


def send_sms(phone_number: str, message: str) -> None:
    print("=== ENTERED TWILIO SERVICE ===")
    print("TWILIO_MODE:", settings.TWILIO_MODE)
    print("PHONE RECEIVED:", phone_number)

    if settings.TWILIO_MODE == "mock":
        logger.info("[MOCK SMS] to=%s message=%s", phone_number, message)
        return

    if settings.TWILIO_MODE == "test":
        logger.info("[TEST MODE SMS] to=%s message=%s", phone_number, message)
        return

    if settings.TWILIO_MODE == "live":
        try:
            from twilio.rest import Client
        except ModuleNotFoundError:
            logger.warning("Twilio SDK is not installed; skipping SMS send in live mode.")
            return

        masked_token = ""
        if settings.TWILIO_AUTH_TOKEN:
            masked_token = "*" * max(len(settings.TWILIO_AUTH_TOKEN) - 4, 0)
            masked_token += settings.TWILIO_AUTH_TOKEN[-4:]

        print("=== TWILIO DEBUG ===")
        print("MODE:", settings.TWILIO_MODE)
        print("SID:", settings.TWILIO_ACCOUNT_SID)
        print("TOKEN:", masked_token)
        print("FROM:", settings.TWILIO_FROM_NUMBER)
        print("TO:", phone_number)

        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
        )
        try:
            twilio_message = client.messages.create(
                body=message,
                from_=settings.TWILIO_FROM_NUMBER,
                to=phone_number,
            )
            print("=== TWILIO RESPONSE ===")
            print(twilio_message.sid)
        except Exception as exc:
            print("=== TWILIO ERROR ===")
            print(str(exc))
            raise
        return

    raise ValueError("Invalid TWILIO_MODE")
