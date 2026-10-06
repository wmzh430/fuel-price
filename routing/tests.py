from types import SimpleNamespace

from django.test import TestCase

from routing.services.fuel_optimizer import InfeasibleRouteError, optimize_fuel_stops


def _station(mile, price):
    return {'mile': mile, 'price': price, 'station': SimpleNamespace(name=f'Station@{mile}')}


class OptimizeFuelStopsTests(TestCase):
    def test_short_trip_needs_no_fuel(self):
        stops, cost = optimize_fuel_stops(300, [_station(100, 3.0)], vehicle_range_miles=500, mpg=10)
        self.assertEqual(stops, [])
        self.assertEqual(cost, 0.0)

    def test_buys_minimum_to_reach_a_cheaper_station(self):
        # Station A (expensive) at 100mi, Station B (cheap) at 450mi, destination at 600mi.
        # The free initial tank carries 400mi of leftover range past A, then exactly
        # enough is bought at B (the only real purchase) to finish the trip.
        candidates = [_station(100, 4.0), _station(450, 3.0)]
        stops, cost = optimize_fuel_stops(600, candidates, vehicle_range_miles=500, mpg=10)

        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]['station'].name, 'Station@450')
        self.assertAlmostEqual(stops[0]['gallons_purchased'], 10.0)  # 100mi shortfall / 10mpg
        self.assertAlmostEqual(cost, 30.0)

    def test_fills_completely_and_skips_pricier_stations_in_between(self):
        # A(400, $5) is the cheapest reachable from the origin; C(440, $7) sits between
        # A and B(480, $6) but is never cheaper than A, so it should be skipped
        # entirely (never bought from, never even visited) while A is filled to a full
        # tank and the vehicle jumps straight to the farthest reachable station, B.
        candidates = [_station(400, 5.0), _station(440, 7.0), _station(480, 6.0)]
        stops, cost = optimize_fuel_stops(980, candidates, vehicle_range_miles=500, mpg=10)

        self.assertEqual([s['station'].name for s in stops], ['Station@400', 'Station@480'])
        self.assertAlmostEqual(stops[0]['gallons_purchased'], 40.0)  # fill tank: 500-100mi left / 10mpg
        self.assertAlmostEqual(stops[1]['gallons_purchased'], 8.0)   # 80mi shortfall / 10mpg
        self.assertAlmostEqual(cost, 40.0 * 5.0 + 8.0 * 6.0)

    def test_raises_when_gap_exceeds_range(self):
        candidates = [_station(100, 3.0)]
        with self.assertRaises(InfeasibleRouteError):
            optimize_fuel_stops(900, candidates, vehicle_range_miles=500, mpg=10)

    def test_total_gallons_matches_distance_beyond_initial_tank(self):
        candidates = [_station(m, 3.00 + (m % 7) * 0.01) for m in range(400, 1600, 150)]
        route_distance = 1600
        stops, _ = optimize_fuel_stops(route_distance, candidates, vehicle_range_miles=500, mpg=10)
        total_gallons = sum(s['gallons_purchased'] for s in stops)
        expected_gallons = (route_distance - 500) / 10
        self.assertAlmostEqual(total_gallons, expected_gallons, places=1)
