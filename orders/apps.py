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