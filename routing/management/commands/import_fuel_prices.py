import csv
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import FuelStation

DATA_DIR = Path(settings.BASE_DIR) / 'routing' / 'data'
FUEL_CSV = DATA_DIR / 'fuel-prices-for-be-assessment.csv'
CITY_COORDS_CSV = DATA_DIR / 'us_city_coords.csv'

US_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA',
    'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT',
    'VA', 'WA', 'WV', 'WI', 'WY', 'DC',
}


def normalize_city(name):
    return re.sub(r'[^A-Z0-9]', '', name.upper())


class Command(BaseCommand):
    help = 'Imports fuel-prices-for-be-assessment.csv into FuelStation, geocoded by city/state centroid.'

    def load_city_coords(self):
        exact = {}
        normalized = {}
        with open(CITY_COORDS_CSV, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                key = (row['city'], row['state'])
                coords = (float(row['latitude']), float(row['longitude']))
                exact[key] = coords
                normalized[(normalize_city(row['city']), row['state'])] = coords
        return exact, normalized

    def handle(self, *args, **options):
        exact_coords, normalized_coords = self.load_city_coords()

        # OPIS Truckstop ID -> accumulated data (duplicate rows are repeated
        # price readings for the same physical station; average them).
        stations = {}
        skipped_non_us = 0
        unmatched_cities = set()

        with open(FUEL_CSV, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                state = row['State'].strip()
                if state not in US_STATES:
                    skipped_non_us += 1
                    continue

                opis_id = int(row['OPIS Truckstop ID'])
                city = row['City'].strip()
                price = float(row['Retail Price'])

                entry = stations.setdefault(opis_id, {
                    'name': row['Truckstop Name'].strip(),
                    'address': row['Address'].strip(),
                    'city': city,
                    'state': state,
                    'prices': [],
                })
                entry['prices'].append(price)

        to_create = []
        for opis_id, data in stations.items():
            city_upper = data['city'].upper()
            state = data['state']
            coords = exact_coords.get((city_upper, state))
            if coords is None:
                coords = normalized_coords.get((normalize_city(data['city']), state))
            if coords is None:
                unmatched_cities.add((data['city'], state))
                continue

            avg_price = sum(data['prices']) / len(data['prices'])
            to_create.append(FuelStation(
                opis_id=opis_id,
                name=data['name'],
                address=data['address'],
                city=data['city'],
                state=state,
                price_per_gallon=round(avg_price, 3),
                latitude=coords[0],
                longitude=coords[1],
            ))

        FuelStation.objects.all().delete()
        FuelStation.objects.bulk_create(to_create, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f'Imported {len(to_create)} fuel stations.'
        ))
        self.stdout.write(f'Skipped {skipped_non_us} non-US rows.')
        if unmatched_cities:
            self.stdout.write(self.style.WARNING(
                f'Could not geocode {len(unmatched_cities)} city/state pairs, stations skipped: '
                f'{sorted(unmatched_cities)[:20]}'
            ))
