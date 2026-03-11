import math


GRID_SIZE_DEGREES = 0.05
KM_PER_DEGREE = 111.0


def compute_geo_grid(lat, lng):
    latitude = float(lat)
    longitude = float(lng)
    return (
        math.floor(latitude / GRID_SIZE_DEGREES),
        math.floor(longitude / GRID_SIZE_DEGREES),
    )


def grid_window_for_radius(lat, lng, radius_km):
    latitude = float(lat)
    longitude = float(lng)
    grid_lat, grid_lng = compute_geo_grid(latitude, longitude)

    lat_cell_km = GRID_SIZE_DEGREES * KM_PER_DEGREE
    longitude_scale = max(abs(math.cos(math.radians(latitude))), 0.1)
    lng_cell_km = GRID_SIZE_DEGREES * KM_PER_DEGREE * longitude_scale

    lat_steps = max(1, math.ceil(float(radius_km) / lat_cell_km))
    lng_steps = max(1, math.ceil(float(radius_km) / lng_cell_km))

    return {
        "grid_lat": grid_lat,
        "grid_lng": grid_lng,
        "min_grid_lat": grid_lat - lat_steps,
        "max_grid_lat": grid_lat + lat_steps,
        "min_grid_lng": grid_lng - lng_steps,
        "max_grid_lng": grid_lng + lng_steps,
    }
