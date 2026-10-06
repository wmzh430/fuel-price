import math

import numpy as np

from routing.models import FuelStation

EARTH_RADIUS_MILES = 3958.8


class InfeasibleRouteError(Exception):
    pass


def _haversine_miles(lat1, lon1, lat2, lon2):
    """Vectorized great-circle distance in miles. Inputs may be numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def resample_route(geometry, interval_miles):
    """Resample a (lat, lon) polyline into evenly spaced points with cumulative mileage.

    The raw OSRM geometry is densest on curvy segments and sparsest on long
    straight highway stretches, which makes it unreliable for "is this station
    N miles from the route" checks. Resampling gives uniform spatial resolution.

    Returns a list of (lat, lon, cumulative_miles), always ending at the final point.
    """
    if len(geometry) < 2:
        return [(geometry[0][0], geometry[0][1], 0.0)] if geometry else []

    lats = np.array([p[0] for p in geometry], dtype=float)
    lons = np.array([p[1] for p in geometry], dtype=float)

    seg_distances = _haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    cumulative = np.concatenate(([0.0], np.cumsum(seg_distances)))
    total_distance = float(cumulative[-1])

    samples = []
    target = 0.0
    seg_idx = 0
    n_segs = len(seg_distances)

    while target <= total_distance:
        while seg_idx < n_segs - 1 and cumulative[seg_idx + 1] < target:
            seg_idx += 1

        seg_len = seg_distances[seg_idx] if seg_distances[seg_idx] > 0 else 1e-9
        t = (target - cumulative[seg_idx]) / seg_len
        t = min(max(t, 0.0), 1.0)
        lat = lats[seg_idx] + t * (lats[seg_idx + 1] - lats[seg_idx])
        lon = lons[seg_idx] + t * (lons[seg_idx + 1] - lons[seg_idx])
        samples.append((float(lat), float(lon), float(target)))
        target += interval_miles

    if samples[-1][2] < total_distance - 1e-6:
        samples.append((float(lats[-1]), float(lons[-1]), total_distance))

    return samples


def find_candidate_stations(route_samples, corridor_miles):
    """Find fuel stations within `corridor_miles` of the resampled route.

    A cheap bounding-box query narrows the candidate set in the database,
    then numpy computes exact distance to the nearest route sample for each
    candidate in one vectorized pass.

    Returns a list of {'mile': float, 'price': float, 'station': FuelStation}.
    """
    if not route_samples:
        return []

    lats = np.array([s[0] for s in route_samples])
    lons = np.array([s[1] for s in route_samples])
    miles = np.array([s[2] for s in route_samples])

    avg_lat = float(np.mean(lats))
    lat_buffer = corridor_miles / 69.0
    lon_buffer = corridor_miles / (69.0 * max(math.cos(math.radians(avg_lat)), 0.1))

    bbox_stations = list(FuelStation.objects.filter(
        latitude__gte=float(lats.min()) - lat_buffer,
        latitude__lte=float(lats.max()) + lat_buffer,
        longitude__gte=float(lons.min()) - lon_buffer,
        longitude__lte=float(lons.max()) + lon_buffer,
    ))
    if not bbox_stations:
        return []

    st_lat = np.array([s.latitude for s in bbox_stations])
    st_lon = np.array([s.longitude for s in bbox_stations])

    # stations x samples distance matrix, vectorized.
    dist_matrix = _haversine_miles(
        st_lat[:, None], st_lon[:, None], lats[None, :], lons[None, :]
    )
    nearest_sample_idx = np.argmin(dist_matrix, axis=1)
    nearest_dist = dist_matrix[np.arange(len(bbox_stations)), nearest_sample_idx]

    candidates = []
    for station, dist, sample_idx in zip(bbox_stations, nearest_dist, nearest_sample_idx):
        if dist <= corridor_miles:
            candidates.append({
                'mile': float(miles[sample_idx]),
                'price': float(station.price_per_gallon),
                'station': station,
            })
    return candidates


def optimize_fuel_stops(route_distance_miles, candidates, vehicle_range_miles, mpg):
    """Choose where to refuel to minimize total cost.

    Assumes the vehicle departs with a full tank (that initial fuel is a sunk
    cost outside this trip) and must buy enough fuel along the way to reach
    the destination without ever exceeding vehicle_range_miles between fills.

    Greedy strategy (optimal for continuous-amount purchases with a single
    tank capacity and no per-stop fee): at the station you're currently at,
    look ahead within one full tank's range. If a cheaper station is within
    reach, buy just enough to get there. Otherwise, fill the tank completely
    and drive to the farthest reachable station, since everything closer is
    more expensive.

    Returns (stops, total_cost) where stops is a list of purchase dicts
    ordered along the route.
    """
    stations = sorted(candidates, key=lambda c: c['mile'])

    at_mile = 0.0
    at_price = float('inf')
    at_station = None  # None = still at the trip origin, not a purchasable stop.
    range_remaining = float(vehicle_range_miles)

    stops = []
    total_cost = 0.0

    def ahead_within(limit_mile):
        return [s for s in stations if at_mile < s['mile'] <= limit_mile]

    def buy(miles_needed, price, station):
        nonlocal total_cost
        if miles_needed <= 1e-9 or station is None:
            return
        gallons = miles_needed / mpg
        cost = gallons * price
        total_cost += cost
        stops.append({
            'station': station,
            'gallons_purchased': round(gallons, 2),
            'price_per_gallon': price,
            'cost': round(cost, 2),
            'distance_from_start_miles': round(at_mile, 1),
        })

    while route_distance_miles - at_mile > range_remaining + 1e-9:
        full_reach = at_mile + vehicle_range_miles
        candidates_ahead = ahead_within(full_reach)
        cheaper = [s for s in candidates_ahead if s['price'] < at_price]

        if cheaper:
            target = min(cheaper, key=lambda s: s['mile'])
            distance = target['mile'] - at_mile
            buy(distance - range_remaining, at_price, at_station)
            range_remaining = max(range_remaining - distance, 0.0)
            at_mile, at_price, at_station = target['mile'], target['price'], target['station']
            continue

        if route_distance_miles <= full_reach:
            distance = route_distance_miles - at_mile
            buy(distance - range_remaining, at_price, at_station)
            at_mile = route_distance_miles
            range_remaining = 0.0
            break

        if not candidates_ahead:
            raise InfeasibleRouteError(
                f'No fuel station is within {vehicle_range_miles} miles of mile '
                f'{at_mile:.1f} along the route, so a vehicle with a '
                f'{vehicle_range_miles}-mile range cannot complete this trip.'
            )

        # No cheaper option ahead: fill up completely and skip past the pricier stations.
        buy(vehicle_range_miles - range_remaining, at_price, at_station)
        farthest = max(candidates_ahead, key=lambda s: s['mile'])
        distance = farthest['mile'] - at_mile
        range_remaining = vehicle_range_miles - distance
        at_mile, at_price, at_station = farthest['mile'], farthest['price'], farthest['station']

    return stops, round(total_cost, 2)
