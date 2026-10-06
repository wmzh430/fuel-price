import requests
from django.conf import settings

from . import local_city_lookup

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = 'SpotterFuelRouteAPI/1.0 (take-home assessment; contact: dev@example.com)'


class GeocodingError(Exception):
    pass


def _try_census(text):
    """US Census geocoder: free, no key, authoritative for full street addresses.
    Does not resolve city-only queries (e.g. "Chicago, IL" -> no match)."""
    try:
        response = requests.get(
            settings.CENSUS_GEOCODER_URL,
            params={'address': text, 'benchmark': 'Public_AR_Current', 'format': 'json'},
            timeout=8,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    matches = response.json().get('result', {}).get('addressMatches', [])
    if not matches:
        return None

    coords = matches[0]['coordinates']
    return float(coords['y']), float(coords['x'])


def _try_nominatim(text):
    """OpenStreetMap Nominatim: handles city-only and full-address queries, but
    its public instance aggressively rate-limits/blocks automated and
    datacenter traffic, so it's used as a last resort rather than relied on."""
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={'q': text, 'format': 'json', 'countrycodes': 'us', 'limit': 1},
            headers={'User-Agent': USER_AGENT},
            timeout=6,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    results = response.json()
    if not results:
        return None
    return float(results[0]['lat']), float(results[0]['lon'])


def _looks_like_street_address(text):
    """Heuristic: a street address starts with a house number (e.g. "1600 Pennsylvania
    Ave"); a city-level query doesn't (e.g. "Chicago, IL"). Used to skip the Census
    geocoder's network round trip for the common city-only case, since it never
    resolves those anyway."""
    first_token = text.strip().split(' ', 1)[0]
    return first_token[:1].isdigit()


def geocode_address(text):
    """Resolve a free-text US location (city/state or full address) to (lat, lon).

    For full street addresses, tries the Census geocoder first (authoritative,
    free, no key). For city-level input ("City, ST"), it goes straight to the
    bundled US city/state centroid lookup, which has no network dependency and
    is effectively instant. Nominatim is a last resort for anything else.
    """
    resolvers = (
        (_try_census, local_city_lookup.lookup_city, _try_nominatim)
        if _looks_like_street_address(text)
        else (local_city_lookup.lookup_city, _try_census, _try_nominatim)
    )
    for resolver in resolvers:
        coords = resolver(text)
        if coords is not None:
            return coords

    raise GeocodingError(f'Could not find a US location matching {text!r}.')
