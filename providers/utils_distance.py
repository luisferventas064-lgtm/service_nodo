import math

from django.core.exceptions import ObjectDoesNotExist


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two points on Earth using Haversine formula.
    Returns distance in kilometers.
    """

    radius_km = 6371

    lat1 = math.radians(float(lat1))
    lon1 = math.radians(float(lon1))
    lat2 = math.radians(float(lat2))
    lon2 = math.radians(float(lon2))

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return radius_km * c


def providers_within_radius(job_location, providers, radius_km=30):
    results = []

    if job_location is None:
        return results

    for provider in providers:
        try:
            provider_location = provider.location
        except (AttributeError, ObjectDoesNotExist):
            continue

        distance = haversine_distance_km(
            job_location.latitude,
            job_location.longitude,
            provider_location.latitude,
            provider_location.longitude,
        )

        if distance <= radius_km:
            results.append((provider, distance))

    return results
