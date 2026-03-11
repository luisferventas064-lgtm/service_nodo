import requests
from django.conf import settings


GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def geocode_address(postal_code, city=None, province=None, country="Canada"):
    if not settings.GOOGLE_MAPS_API_KEY or not postal_code:
        return None

    address_parts = [postal_code]

    if city:
        address_parts.append(city)

    if province:
        address_parts.append(province)

    if country:
        address_parts.append(country)

    address = ", ".join(address_parts)

    params = {
        "address": address,
        "key": settings.GOOGLE_MAPS_API_KEY,
    }

    try:
        response = requests.get(GEOCODE_URL, params=params, timeout=15)
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    if data["status"] != "OK":
        return None

    result = data["results"][0]
    location = result["geometry"]["location"]

    return {
        "lat": location["lat"],
        "lng": location["lng"],
        "formatted_address": result["formatted_address"],
        "components": result["address_components"],
    }


def extract_province(components):
    for comp in components:
        if "administrative_area_level_1" in comp["types"]:
            return comp["short_name"]

    return None
