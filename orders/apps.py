"""
AppConfig for the orders application.

The ready() method is the correct place to connect signal receivers.
Django guarantees that ready() runs exactly once, after the full app
registry has been populated, which means all models are importable and
no circular-import errors can occur.
"""
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orders'

    def ready(self):
        # Importing orders.signals causes the @receiver decorators defined
        # inside that module to be evaluated, which connects the receiver
        # functions to their respective signals.  Without this import the
        # decorators would never run and no signal would ever fire.
        import orders.signals  # noqa: F401