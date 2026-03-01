from .models import Client
from providers.models import Provider


def client_session_context(request):
    verify_actor_type = request.session.get("verify_actor_type")
    client_id = request.session.get("client_id")
    if not client_id and verify_actor_type == "client":
        client_id = request.session.get("verify_actor_id")
    provider_id = request.session.get("provider_id")
    if not provider_id and verify_actor_type == "provider":
        provider_id = request.session.get("verify_actor_id")

    client = Client.objects.filter(pk=client_id).first() if client_id else None
    provider = Provider.objects.filter(pk=provider_id).first() if provider_id else None
    return {
        "user_client": client,
        "user_provider": provider,
    }
