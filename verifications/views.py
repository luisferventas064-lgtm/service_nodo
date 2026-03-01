import json
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import PhoneVerification, SecurityEvent
from .services import (
    create_phone_verification,
    get_phone_verification_actor,
    record_security_event,
    verify_phone_code,
)


OTP_REQUEST_COOLDOWN_SECONDS = 60
OTP_REQUEST_RATE_LIMIT = 5
OTP_REQUEST_WINDOW_SECONDS = 60
OTP_CONFIRM_RATE_LIMIT = 10
OTP_CONFIRM_WINDOW_SECONDS = 60
OTP_PHONE_RATE_LIMIT = 3
OTP_PHONE_RATE_WINDOW_SECONDS = 60
OTP_PHONE_DAILY_LIMIT = 10
OTP_PHONE_DAILY_WINDOW_SECONDS = 86400
def _get_payload(request):
    if request.body:
        try:
            return json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    return request.POST.dict()


def _get_client_ip(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _rate_limited(request, *, scope: str, limit: int, window_seconds: int) -> bool:
    ip_address = _get_client_ip(request)
    cache_key = f"otp_throttle:{scope}:{ip_address}"
    now_ts = timezone.now().timestamp()
    window_start = now_ts - window_seconds
    hits = cache.get(cache_key, [])
    hits = [hit for hit in hits if hit >= window_start]

    if len(hits) >= limit:
        cache.set(cache_key, hits, timeout=window_seconds)
        return True

    hits.append(now_ts)
    cache.set(cache_key, hits, timeout=window_seconds)
    return False


@csrf_exempt
@require_POST
def request_phone_verification(request):
    ip_address = _get_client_ip(request)
    if _rate_limited(
        request,
        scope="request",
        limit=OTP_REQUEST_RATE_LIMIT,
        window_seconds=OTP_REQUEST_WINDOW_SECONDS,
    ):
        record_security_event(
            event_type=SecurityEvent.EventType.OTP_IP_RATE_LIMIT,
            ip_address=ip_address,
            metadata={"scope": "request"},
        )
        return JsonResponse({"detail": "Too many requests."}, status=429)

    payload = _get_payload(request)
    if payload is None:
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    actor_type = payload.get("actor_type")
    actor_id_raw = payload.get("actor_id")
    phone_number = payload.get("phone_number")

    if not all([actor_type, actor_id_raw, phone_number]):
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    try:
        actor_id = int(actor_id_raw)
        normalized_actor_type, actor = get_phone_verification_actor(
            actor_type=actor_type,
            actor_id=actor_id,
        )
    except (TypeError, ValueError, ValidationError):
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    if actor.is_phone_verified:
        return JsonResponse({"detail": "Phone already verified."}, status=400)

    phone_number = str(phone_number).strip()
    if phone_number != actor.phone_number:
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    block_key = f"otp_block:{phone_number}"
    if cache.get(block_key):
        record_security_event(
            event_type=SecurityEvent.EventType.OTP_ABUSE_BLOCK,
            phone_number=phone_number,
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            ip_address=ip_address,
            metadata={"source": "request_phone_verification.cache_block"},
        )
        return JsonResponse({"detail": "Too many requests. Try later."}, status=429)

    phone_key = f"otp_phone_rate:{phone_number}"
    phone_requests = cache.get(phone_key, 0)
    if phone_requests >= OTP_PHONE_RATE_LIMIT:
        record_security_event(
            event_type=SecurityEvent.EventType.OTP_PHONE_RATE_LIMIT,
            phone_number=phone_number,
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            ip_address=ip_address,
            metadata={"window_seconds": OTP_PHONE_RATE_WINDOW_SECONDS},
        )
        return JsonResponse({"detail": "Too many requests. Try later."}, status=429)

    daily_key = f"otp_daily:{phone_number}:{timezone.now().date()}"
    daily_count = cache.get(daily_key, 0)
    if daily_count >= OTP_PHONE_DAILY_LIMIT:
        record_security_event(
            event_type=SecurityEvent.EventType.OTP_DAILY_LIMIT,
            phone_number=phone_number,
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            ip_address=ip_address,
            metadata={"date": str(timezone.now().date())},
        )
        return JsonResponse({"detail": "Daily limit reached."}, status=429)

    cache.set(phone_key, phone_requests + 1, timeout=OTP_PHONE_RATE_WINDOW_SECONDS)
    cache.set(daily_key, daily_count + 1, timeout=OTP_PHONE_DAILY_WINDOW_SECONDS)

    now = timezone.now()
    latest_pending = (
        PhoneVerification.objects.filter(
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            is_verified=False,
        )
        .order_by("-created_at")
        .first()
    )
    if (
        latest_pending
        and latest_pending.expires_at > now
        and latest_pending.created_at >= now - timedelta(seconds=OTP_REQUEST_COOLDOWN_SECONDS)
    ):
        record_security_event(
            event_type=SecurityEvent.EventType.OTP_REQUEST_COOLDOWN,
            phone_number=phone_number,
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            ip_address=ip_address,
            metadata={"cooldown_seconds": OTP_REQUEST_COOLDOWN_SECONDS},
        )
        return JsonResponse(
            {"detail": "Please wait before requesting another code."},
            status=429,
        )

    try:
        _, verification = create_phone_verification(
            actor_type=normalized_actor_type,
            actor_id=actor_id,
            phone_number=phone_number,
        )
    except ValidationError:
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    return JsonResponse(
        {
            "detail": "Verification created.",
            "expires_at": verification.expires_at.isoformat(),
        },
        status=201,
    )


@csrf_exempt
@require_POST
def confirm_phone_verification(request):
    ip_address = _get_client_ip(request)
    if _rate_limited(
        request,
        scope="confirm",
        limit=OTP_CONFIRM_RATE_LIMIT,
        window_seconds=OTP_CONFIRM_WINDOW_SECONDS,
    ):
        record_security_event(
            event_type=SecurityEvent.EventType.OTP_IP_RATE_LIMIT,
            ip_address=ip_address,
            metadata={"scope": "confirm"},
        )
        return JsonResponse({"detail": "Too many requests."}, status=429)

    payload = _get_payload(request)
    if payload is None:
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    actor_type = payload.get("actor_type")
    actor_id_raw = payload.get("actor_id")
    code = payload.get("code")

    if not all([actor_type, actor_id_raw, code]):
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    try:
        actor_id = int(actor_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    try:
        verify_phone_code(
            actor_type=actor_type,
            actor_id=actor_id,
            code=str(code).strip(),
        )
    except ValidationError:
        return JsonResponse({"detail": "Invalid or expired code."}, status=400)

    return JsonResponse(
        {"detail": "Phone verified successfully."},
        status=200,
    )
