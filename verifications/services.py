import hashlib
import logging
import random
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import PhoneVerification, SecurityEvent
from .twilio_service import send_sms


OTP_EXPIRATION_MINUTES = 10
MAX_OTP_ATTEMPTS = 5
OTP_ABUSE_BLOCK_SECONDS = 900


logger = logging.getLogger(__name__)


def _normalize_actor_type(actor_type: str) -> str:
    return (actor_type or "").strip().lower()


def _generate_otp_code() -> str:
    return f"{random.randint(100000, 999999)}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def record_security_event(
    *,
    event_type: str,
    phone_number: str | None = None,
    actor_type: str | None = None,
    actor_id: int | None = None,
    ip_address: str | None = None,
    metadata: dict | None = None,
) -> None:
    SecurityEvent.objects.create(
        event_type=event_type,
        phone_number=phone_number,
        actor_type=actor_type,
        actor_id=actor_id,
        ip_address=ip_address,
        metadata=metadata,
    )
    logger.warning(
        event_type,
        extra={
            "phone_number": phone_number,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "ip": ip_address,
            "metadata": metadata or {},
        },
    )


def get_phone_verification_actor(*, actor_type: str, actor_id: int):
    normalized_actor_type = _normalize_actor_type(actor_type)

    if normalized_actor_type == PhoneVerification.ActorType.CLIENT:
        from clients.models import Client

        model = Client
    elif normalized_actor_type == PhoneVerification.ActorType.PROVIDER:
        from providers.models import Provider

        model = Provider
    else:
        raise ValidationError("Invalid actor type.")

    try:
        actor = model.objects.get(pk=actor_id)
    except model.DoesNotExist as exc:
        raise ValidationError("Invalid actor.") from exc

    return normalized_actor_type, actor


def create_phone_verification(
    *, actor_type: str, actor_id: int, phone_number: str
) -> tuple[str, PhoneVerification]:
    normalized_actor_type = _normalize_actor_type(actor_type)
    if normalized_actor_type not in PhoneVerification.ActorType.values:
        raise ValidationError("Invalid actor type.")

    with transaction.atomic():
        now = timezone.now()

        PhoneVerification.objects.filter(
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            is_verified=False,
        ).update(expires_at=now)

        code = _generate_otp_code()
        code_hash = _hash_code(code)

        verification = PhoneVerification.objects.create(
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            phone_number=phone_number,
            code_hash=code_hash,
            expires_at=now + timedelta(minutes=OTP_EXPIRATION_MINUTES),
        )

        send_sms(
            phone_number=phone_number,
            message=f"Your verification code is: {code}",
        )

    return code, verification


def verify_phone_code(actor_type: str, actor_id: int, code: str) -> bool:
    normalized_actor_type = _normalize_actor_type(actor_type)
    error_message = None

    with transaction.atomic():
        verification = (
            PhoneVerification.objects.select_for_update()
            .filter(
                actor_type=normalized_actor_type,
                actor_id=actor_id,
                is_verified=False,
            )
            .order_by("-created_at")
            .first()
        )

        if not verification:
            error_message = "No active verification found."
        else:
            block_key = f"otp_block:{verification.phone_number}"
            if cache.get(block_key):
                record_security_event(
                    event_type=SecurityEvent.EventType.OTP_ABUSE_BLOCK,
                    phone_number=verification.phone_number,
                    actor_type=normalized_actor_type,
                    actor_id=actor_id,
                    metadata={"source": "verify_phone_code.cache_block"},
                )
                error_message = "Too many attempts."

            if error_message is None and verification.expires_at < timezone.now():
                error_message = "Code expired."

            if error_message is None and verification.attempts >= MAX_OTP_ATTEMPTS:
                cache.set(block_key, True, timeout=OTP_ABUSE_BLOCK_SECONDS)
                record_security_event(
                    event_type=SecurityEvent.EventType.OTP_ABUSE_BLOCK,
                    phone_number=verification.phone_number,
                    actor_type=normalized_actor_type,
                    actor_id=actor_id,
                    metadata={"source": "verify_phone_code.preexisting_attempts"},
                )
                error_message = "Too many attempts."

            if error_message is None:
                hashed_input = _hash_code(code)

                if hashed_input != verification.code_hash:
                    verification.attempts += 1
                    verification.save(update_fields=["attempts"])
                    if verification.attempts >= MAX_OTP_ATTEMPTS:
                        cache.set(block_key, True, timeout=OTP_ABUSE_BLOCK_SECONDS)
                        record_security_event(
                            event_type=SecurityEvent.EventType.OTP_ABUSE_BLOCK,
                            phone_number=verification.phone_number,
                            actor_type=normalized_actor_type,
                            actor_id=actor_id,
                            metadata={"source": "verify_phone_code.max_attempts_reached"},
                        )
                        error_message = "Too many attempts."
                    else:
                        error_message = "Invalid code."

            if error_message is None:
                verified_at = timezone.now()
                verification.is_verified = True
                verification.verified_at = verified_at
                verification.save(update_fields=["is_verified", "verified_at"])

                _, actor = get_phone_verification_actor(
                    actor_type=normalized_actor_type,
                    actor_id=actor_id,
                )

                actor.is_phone_verified = True
                actor.phone_verified_at = verified_at
                actor.phone_verification_attempts = verification.attempts
                actor.save(
                    update_fields=[
                        "is_phone_verified",
                        "phone_verified_at",
                        "phone_verification_attempts",
                    ]
                )

                return True

    if error_message is not None:
        raise ValidationError(error_message)

    return True
