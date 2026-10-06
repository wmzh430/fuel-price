# Fuel Route Planner API

Given a start and finish location in the USA, this API returns a driving route, a
cost-minimizing set of fuel stops for a vehicle with a 500-mile range, and the total
fuel cost for the trip assuming 10 miles per gallon.

## Stack

- Django 6.1 + Django REST Framework
- SQLite (default Django DB; swap `DATABASES` in `config/settings.py` for Postgres etc.)
- [OSRM](http://project-osrm.org/) public demo server for routing (free, no API key)
- [Nominatim](https://nominatim.org/) / US Census Geocoder for geocoding (free, no API key)
- numpy for vectorized distance calculations

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

python manage.py migrate
python manage.py import_fuel_prices   # one-time: loads routing/data/*.csv into the DB
python manage.py runserver
```

## API

### `POST /api/route/`

**Request**
```json
{"start": "Los Angeles, CA", "finish": "Chicago, IL"}
```
`start`/`finish` accept either `"City, State"` or a full street address.

**Response** (200)
```json
{
  "start": {"query": "Los Angeles, CA", "latitude": 34.03, "longitude": -118.28},
  "finish": {"query": "Chicago, IL", "latitude": 41.85, "longitude": -87.68},
  "distance_miles": 2021.0,
  "duration_hours": 35.45,
  "route_geometry": [[34.03, -118.28], ...],
  "map_url": "https://www.google.com/maps/dir/?api=1&origin=...&waypoints=...",
  "fuel_stops": [
    {
      "stop_order": 1,
      "name": "MORTONS TRUCK STOP",
      "address": "I-15, EXIT 46",
      "city": "North Las Vegas",
      "state": "NV",
      "latitude": 36.25,
      "longitude": -115.15,
      "price_per_gallon": 3.399,
      "gallons_purchased": 17.7,
      "cost": 60.16,
      "distance_from_start_miles": 278.0
    }
  ],
  "total_fuel_cost": 477.87,
  "assumptions": {
    "vehicle_range_miles": 500,
    "vehicle_mpg": 10,
    "starts_with_a_full_tank": true,
    "fuel_station_corridor_miles": 3,
    "note": "total_fuel_cost covers fuel purchased during the trip; the initial full tank at departure is treated as a sunk cost incurred before this route."
  }
}
```

Errors: `400` for bad/unresolvable locations, `422` if a stretch of the route has no
fuel station within 500 miles (trip isn't completable), `502` if the routing provider
is unreachable.

## Design decisions

**One call to the routing API per request.** OSRM is called exactly once, to fetch the
full route geometry; everything else (fuel-stop selection) runs against pre-geocoded,
locally stored data. Geocoding the two endpoints is a separate concern from "the
map/routing API" and is resolved without any network call at all for the common
`"City, State"` case (see below), keeping the request fast and not dependent on any
rate-limited third party for its hot path.

**Fuel stations are geocoded once, offline, not per-request.** The ~8,000-row price
CSV has no coordinates, only city/state. Geocoding every station via a live API at
request time would be slow and would hammer a free geocoder. Instead,
`import_fuel_prices` joins each station's city/state against
`routing/data/us_city_coords.csv` — a ~29,500-row city/state centroid table built once
from GeoNames' public-domain US postal code data — which covers 100% of the stations
in the provided dataset (two near-miss spelling variants, e.g. "De Forest" vs
"DeForest", are caught by a punctuation/whitespace-insensitive fallback match). This
means fuel-stop lookup at request time is a pure local computation: a DB bounding-box
query plus a vectorized numpy distance calculation, with zero network calls.

**Duplicate CSV rows.** Many stations appear multiple times under the same OPIS
Truckstop ID with different retail prices and no timestamp (undated historical/grade
readings). Each station is imported once, using the **average** of its price readings,
rather than cherry-picking the minimum.

**Geocoding start/finish.** The US Census geocoder is free and authoritative but only
resolves full street addresses, not "City, State" queries. OpenStreetMap's Nominatim
resolves both but aggressively blocks automated/datacenter traffic in practice. So:
city-level input resolves instantly from the same local city-centroid table used for
fuel stations (no network call); full street addresses go to the Census geocoder;
Nominatim is kept only as a last-resort fallback.

**Matching stations to the route.** OSRM's route geometry is dense on turns and sparse
on long straight highway stretches, so it's resampled into points exactly 1 mile apart
before being used for proximity checks. A station counts as "on the route" if it's
within 3 miles of the nearest sampled point — tunable via `ROUTE_CORRIDOR_MILES` in
settings. A DB bounding-box filter on the route's lat/lon extent runs first so the
(cheap, but still O(n·m)) numpy distance matrix only has to consider nearby stations.

**Choosing where to stop (the optimization).** This is the classic "minimum-cost
refueling with a range limit" problem: continuous (non-discrete) purchase amounts, one
tank, no per-stop fee. The optimal strategy is a greedy scan along the route: at the
station you're currently at, look ahead within one full tank's range.
- If a cheaper station is within reach, buy *just enough* fuel to get there.
- Otherwise, fill the tank completely and drive to the farthest reachable station,
  since everything closer is more expensive anyway.

This runs in O(n log n) (sort once) and matches the provably optimal solution for this
problem shape. The vehicle is assumed to **depart with a full tank** — that fuel is a
sunk cost from before the trip, so `total_fuel_cost` reflects only what's purchased
along the way (`$0` for trips under 500 miles). If a stretch of the route has no
station within 500 miles of the previous one, the API returns a `422` rather than
silently producing a wrong answer.

**Map output.** The response includes the full route as a `[lat, lon]` polyline
(thinned to ~5-mile spacing to keep the payload small) for rendering on any map
library, plus a ready-to-click `map_url` (a plain Google Maps directions deep link —
not the paid Maps API, just a URL) with the chosen fuel stops as waypoints.

## Performance

For a 2,000+ mile cross-country route against the full ~6,600-station dataset:
roughly 0.1s for geocoding (local lookup), ~1.5s for the single OSRM call (the public
demo instance's latency, outside this API's control), and well under 0.2s for route
resampling, station matching, and optimization combined.

## Tests / verification

Run `python manage.py check` for the system check. There's no bundled automated test
suite; the implementation was verified end-to-end via live requests covering a
cross-country trip, a full-street-address input, a short (<500mi) trip (0 stops
expected), and invalid-location handling (see git history / dev notes for the exact
commands used).

## Data

- `routing/data/fuel-prices-for-be-assessment.csv` — the provided fuel price list.
- `routing/data/us_city_coords.csv` — generated once from GeoNames' free, public-domain
  [US postal code export](http://download.geonames.org/export/zip/US.zip); not
  regenerated at runtime.
