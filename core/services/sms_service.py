from core.utils.phone import best_effort_normalize_phone
from verifications.twilio_service import send_sms as send_verification_sms


def send_sms(to_number, message):
    normalized_number = best_effort_normalize_phone(to_number)
    if (
        normalized_number
        and not normalized_number.startswith("+")
        and normalized_number.isdigit()
        and len(normalized_number) == 10
    ):
        normalized_number = f"+1{normalized_number}"

    print("=== SMS FUNCTION CALLED ===")
    print("TO:", normalized_number)
    send_verification_sms(phone_number=normalized_number, message=message)
