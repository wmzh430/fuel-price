from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RoutePlanRequestSerializer
from .services import fuel_optimizer
from .services.geocoding import GeocodingError, geocode_address
from .services.routing_client import RoutingError, get_route

MAP_POLYLINE_SAMPLE_STRIDE = 5  # thin the 1-mile samples to keep the response payload small
MAX_MAP_WAYPOINTS = 9  # Google Maps' simple directions deep link supports up to ~9-10 waypoints


class RoutePlanView(APIView):
    """POST /api/route/ {"start": "...", "finish": "..."}

    Geocodes both locations, fetches one driving route from OSRM, and selects
    a cost-minimizing set of fuel stops along it for a 500-mile-range,
    10-mpg vehicle.
    """

    def post(self, request):
        serializer = RoutePlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start_text = serializer.validated_data['start']
        finish_text = serializer.validated_data['finish']

        try:
            start_coords = geocode_address(start_text)
            finish_coords = geocode_address(finish_text)
        except GeocodingError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = get_route(start_coords, finish_coords)
        except RoutingError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        samples = fuel_optimizer.resample_route(route['geometry'], settings.ROUTE_SAMPLE_INTERVAL_MILES)
        candidates = fuel_optimizer.find_candidate_stations(samples, settings.ROUTE_CORRIDOR_MILES)

        try:
            stops, total_cost = fuel_optimizer.optimize_fuel_stops(
                route['distance_miles'], candidates, settings.VEHICLE_RANGE_MILES, settings.VEHICLE_MPG,
            )
        except fuel_optimizer.InfeasibleRouteError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        return Response(
            self._build_response(start_text, finish_text, start_coords, finish_coords, route, samples, stops, total_cost),
            status=status.HTTP_200_OK,
        )

    def _build_response(self, start_text, finish_text, start_coords, finish_coords, route, samples, stops, total_cost):
        map_points = samples[::MAP_POLYLINE_SAMPLE_STRIDE]
        if samples and (not map_points or map_points[-1] != samples[-1]):
            map_points = map_points + [samples[-1]]

        map_url = self._build_map_url(start_coords, finish_coords, stops)

        return {
            'start': {'query': start_text, 'latitude': start_coords[0], 'longitude': start_coords[1]},
            'finish': {'query': finish_text, 'latitude': finish_coords[0], 'longitude': finish_coords[1]},
            'distance_miles': round(route['distance_miles'], 1),
            'duration_hours': round(route['duration_hours'], 2),
            'route_geometry': [[lat, lon] for lat, lon, _ in map_points],
            'map_url': map_url,
            'fuel_stops': [
                {
                    'stop_order': i + 1,
                    'name': s['station'].name,
                    'address': s['station'].address,
                    'city': s['station'].city,
                    'state': s['station'].state,
                    'latitude': s['station'].latitude,
                    'longitude': s['station'].longitude,
                    'price_per_gallon': float(s['price_per_gallon']),
                    'gallons_purchased': s['gallons_purchased'],
                    'cost': s['cost'],
                    'distance_from_start_miles': s['distance_from_start_miles'],
                }
                for i, s in enumerate(stops)
            ],
            'total_fuel_cost': total_cost,
            'assumptions': {
                'vehicle_range_miles': settings.VEHICLE_RANGE_MILES,
                'vehicle_mpg': settings.VEHICLE_MPG,
                'starts_with_a_full_tank': True,
                'fuel_station_corridor_miles': settings.ROUTE_CORRIDOR_MILES,
                'note': (
                    'total_fuel_cost covers fuel purchased during the trip; the initial full '
                    'tank at departure is treated as a sunk cost incurred before this route.'
                ),
            },
        }

    @staticmethod
    def _build_map_url(start_coords, finish_coords, stops):
        url = (
            'https://www.google.com/maps/dir/?api=1'
            f'&origin={start_coords[0]},{start_coords[1]}'
            f'&destination={finish_coords[0]},{finish_coords[1]}'
            '&travelmode=driving'
        )
        waypoints = '|'.join(
            f"{s['station'].latitude},{s['station'].longitude}" for s in stops[:MAX_MAP_WAYPOINTS]
        )
        if waypoints:
            url += f'&waypoints={waypoints}'
        return url
