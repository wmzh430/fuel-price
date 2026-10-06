import csv
import re
from pathlib import Path

from django.conf import settings

_CITY_COORDS_CSV = Path(settings.BASE_DIR) / 'routing' / 'data' / 'us_city_coords.csv'

_STATE_NAME_TO_ABBR = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
    'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA',
    'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
    'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
    'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI',
    'wyoming': 'WY', 'district of columbia': 'DC',
}
_VALID_ABBRS = set(_STATE_NAME_TO_ABBR.values())

_cache = None


def _normalize(name):
    return re.sub(r'[^A-Z0-9]', '', name.upper())


def _load():
    global _cache
    if _cache is not None:
        return _cache

    exact = {}
    normalized = {}
    with open(_CITY_COORDS_CSV, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            coords = (float(row['latitude']), float(row['longitude']))
            exact[(row['city'], row['state'])] = coords
            normalized[(_normalize(row['city']), row['state'])] = coords

    _cache = (exact, normalized)
    return _cache


def _resolve_state(token):
    token = token.strip()
    upper = token.upper()
    if upper in _VALID_ABBRS:
        return upper
    return _STATE_NAME_TO_ABBR.get(token.lower())


def lookup_city(text):
    """Best-effort local lookup for "City, ST" / "City, State" style input.

    Returns (lat, lon) or None if the text doesn't parse as city+state or
    isn't in the dataset. This covers city-level queries without depending
    on a third-party geocoding API.
    """
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if len(parts) < 2:
        return None

    city, state_token = parts[0], parts[1]
    state = _resolve_state(state_token)
    if not state:
        return None

    exact, normalized = _load()
    coords = exact.get((city.upper(), state))
    if coords is None:
        coords = normalized.get((_normalize(city), state))
    return coords
