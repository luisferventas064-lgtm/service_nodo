from __future__ import annotations

import json
import os

import requests
from django.conf import settings

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _fcm_data_payload(payload: dict) -> dict[str, str]:
    data = {}
    for key, value in (payload or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            data[str(key)] = value
        else:
            data[str(key)] = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return data


def _resolve_fcm_credentials():
    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError(
            "google-auth is required when PUSH_PROVIDER=fcm"
        ) from exc

    project_id = getattr(settings, "FCM_PROJECT_ID", "").strip()
    credentials_file = getattr(settings, "FCM_CREDENTIALS_FILE", "").strip() or os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        "",
    ).strip()

    if credentials_file:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=[FCM_SCOPE],
        )
        project_id = project_id or getattr(credentials, "project_id", "") or ""
    else:
        credentials, detected_project_id = google.auth.default(scopes=[FCM_SCOPE])
        project_id = project_id or (detected_project_id or "")

    if not project_id:
        raise RuntimeError("FCM project id is not configured")

    credentials.refresh(GoogleAuthRequest())
    return credentials, project_id


def send_fcm_push(*, token: str, payload: dict) -> dict:
    credentials, project_id = _resolve_fcm_credentials()
    response = requests.post(
        f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "message": {
                "token": token,
                "data": _fcm_data_payload(payload),
            }
        },
        timeout=10,
    )

    try:
        response_json = response.json()
    except ValueError:
        response_json = {"text": response.text[:500]}

    return {
        "ok": response.ok,
        "provider": "fcm",
        "token": token,
        "status_code": response.status_code,
        "provider_message_id": response_json.get("name", ""),
        "response_json": response_json,
    }
