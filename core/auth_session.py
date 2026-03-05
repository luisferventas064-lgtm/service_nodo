from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

SESSION_KEY_ROLE = "nodo_role"
SESSION_KEY_ID = "nodo_profile_id"

LEGACY_ROLE_SESSION_KEYS = {
    "client": "client_id",
    "provider": "provider_id",
    "worker": "worker_id",
}


def set_session(request, role: str, profile_id: int):
    request.session[SESSION_KEY_ROLE] = role
    request.session[SESSION_KEY_ID] = profile_id

    for key in LEGACY_ROLE_SESSION_KEYS.values():
        request.session.pop(key, None)

    legacy_key = LEGACY_ROLE_SESSION_KEYS.get(role)
    if legacy_key:
        request.session[legacy_key] = profile_id


def clear_session(request):
    request.session.pop(SESSION_KEY_ROLE, None)
    request.session.pop(SESSION_KEY_ID, None)
    for key in LEGACY_ROLE_SESSION_KEYS.values():
        request.session.pop(key, None)


def require_role(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            role = request.session.get(SESSION_KEY_ROLE)
            profile_id = request.session.get(SESSION_KEY_ID)

            if role is None:
                for legacy_role, legacy_key in LEGACY_ROLE_SESSION_KEYS.items():
                    legacy_id = request.session.get(legacy_key)
                    if legacy_id:
                        role = legacy_role
                        profile_id = legacy_id
                        break
                if role and profile_id:
                    set_session(request, role=role, profile_id=profile_id)

            if role not in roles:
                messages.error(request, "Please log in.")
                return redirect("ui:root_login")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
