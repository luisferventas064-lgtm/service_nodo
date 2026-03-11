from clients.models import Client
from core.auth_session import LEGACY_ROLE_SESSION_KEYS, SESSION_KEY_ID, SESSION_KEY_ROLE
from providers.models import Provider
from workers.models import Worker


class ActiveProfileMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.client_profile = None
        request.provider_profile = None
        request.worker_profile = None

        session_role = request.session.get(SESSION_KEY_ROLE)
        session_profile_id = request.session.get(SESSION_KEY_ID)
        verify_actor_type = request.session.get("verify_actor_type")
        verify_actor_id = request.session.get("verify_actor_id")

        client_id = request.session.get(LEGACY_ROLE_SESSION_KEYS["client"])
        provider_id = request.session.get(LEGACY_ROLE_SESSION_KEYS["provider"])
        worker_id = request.session.get(LEGACY_ROLE_SESSION_KEYS["worker"])

        if session_role == "client" and session_profile_id and not client_id:
            client_id = session_profile_id
        elif session_role == "provider" and session_profile_id and not provider_id:
            provider_id = session_profile_id
        elif session_role == "worker" and session_profile_id and not worker_id:
            worker_id = session_profile_id

        if verify_actor_type == "client" and verify_actor_id and not client_id:
            client_id = verify_actor_id
        elif verify_actor_type == "provider" and verify_actor_id and not provider_id:
            provider_id = verify_actor_id
        elif verify_actor_type == "worker" and verify_actor_id and not worker_id:
            worker_id = verify_actor_id

        if client_id:
            request.client_profile = Client.objects.filter(pk=client_id).first()
        if provider_id:
            request.provider_profile = Provider.objects.filter(pk=provider_id).first()
        if worker_id:
            request.worker_profile = Worker.objects.filter(pk=worker_id).first()

        return self.get_response(request)
