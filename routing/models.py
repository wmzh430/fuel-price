from django.db import models


class FuelStation(models.Model):
    """A truck stop with a diesel retail price, pre-geocoded at import time.

    Prices in the source CSV are duplicated per station across multiple
    unquoted-date readings; import_fuel_prices averages them into a single
    representative price per OPIS Truckstop ID.
    """

    opis_id = models.PositiveIntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    price_per_gallon = models.DecimalField(max_digits=6, decimal_places=3)
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        indexes = [
            models.Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        return f'{self.name} ({self.city}, {self.state}) - ${self.price_per_gallon}'
