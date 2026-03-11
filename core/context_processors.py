def active_profile_nav(request):
    client = getattr(request, "client_profile", None)
    provider = getattr(request, "provider_profile", None)
    worker = getattr(request, "worker_profile", None)

    user_nav_label = "Account"

    if client:
        full_name = f"{client.first_name} {client.last_name}".strip()
        user_nav_label = f"{full_name} \u2013 Client" if full_name else "Client"
    elif provider:
        full_name = f"{provider.contact_first_name} {provider.contact_last_name}".strip()
        user_nav_label = f"{full_name} \u2013 Provider" if full_name else "Provider"
    elif worker:
        full_name = f"{worker.first_name} {worker.last_name}".strip()
        user_nav_label = f"{full_name} \u2013 Worker" if full_name else "Worker"

    return {
        "user_nav_label": user_nav_label,
    }
