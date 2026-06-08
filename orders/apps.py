"""
AppConfig for the orders application.

STAGE A → STAGE B change
-------------------------
Stage A had the following in ready():

    import orders.signals

That import connected the @receiver(post_save, sender=Order) decorator
in orders/signals.py, making update_user_stats_on_order_save fire
automatically after every Order.save() with created=True.

Stage B removes that import entirely (REQ-018).  Business logic for
order creation now lives exclusively in orders/services.create_order(),
wrapped in transaction.atomic().  The ready() method is retained as a
documented extension point for any future non-critical signals such as
cache invalidation or search-index updates.
"""
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orders'

    def ready(self):
        # Stage A registered the signal here:
        #
        #     import orders.signals
        #
        # Removed in Stage B (REQ-018).  All order-creation business logic
        # now lives in orders/services.create_order().
        pass