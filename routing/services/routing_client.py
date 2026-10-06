import requests
from django.conf import settings

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    pass


def get_route(start, finish):
    """Fetch a driving route from OSRM's free public routing API.

    start/finish: (latitude, longitude) tuples.
    Returns: {'distance_miles': float, 'duration_hours': float, 'geometry': [(lat, lon), ...]}

    This is the single call made to the map/routing API per request.
    """
    lat1, lon1 = start
    lat2, lon2 = finish
    url = f'{settings.OSRM_BASE_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}'

    try:
        response = requests.get(
            url,
            params={'overview': 'full', 'geometries': 'geojson', 'steps': 'false'},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f'Routing service error: {exc}') from exc

    data = response.json()
    if data.get('code') != 'Ok' or not data.get('routes'):
        raise RoutingError(f"Routing failed: {data.get('code')} {data.get('message', '')}".strip())

    route = data['routes'][0]
    geometry = [(lat, lon) for lon, lat in route['geometry']['coordinates']]

    return {
        'distance_miles': route['distance'] / METERS_PER_MILE,
        'duration_hours': route['duration'] / 3600,
        'geometry': geometry,
    }
