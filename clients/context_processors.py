from .models import Client
from providers.models import Provider
from workers.models import Worker


def client_session_context(request):
    client = getattr(request, "client_profile", None)
    provider = getattr(request, "provider_profile", None)
    worker = getattr(request, "worker_profile", None)

    if client is not None or provider is not None or worker is not None:
        return {
            "user_client": client,
            "user_provider": provider,
            "user_worker": worker,
        }

    verify_actor_type = request.session.get("verify_actor_type")
    client_id = request.session.get("client_id")
    if not client_id and verify_actor_type == "client":
        client_id = request.session.get("verify_actor_id")
    provider_id = request.session.get("provider_id")
    if not provider_id and verify_actor_type == "provider":
        provider_id = request.session.get("verify_actor_id")
    worker_id = request.session.get("worker_id")
    if not worker_id and verify_actor_type == "worker":
        worker_id = request.session.get("verify_actor_id")

    client = Client.objects.filter(pk=client_id).first() if client_id else None
    provider = Provider.objects.filter(pk=provider_id).first() if provider_id else None
    worker = Worker.objects.filter(pk=worker_id).first() if worker_id else None
    return {
        "user_client": client,
        "user_provider": provider,
        "user_worker": worker,
    }
