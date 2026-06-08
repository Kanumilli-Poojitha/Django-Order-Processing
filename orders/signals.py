"""
Stage A signal receiver — @receiver decorator removed in Stage B (REQ-019).

HISTORY
-------
Stage A attached this function to the post_save signal via the
@receiver(post_save, sender=Order) decorator and registered it by
importing this module inside OrdersConfig.ready().

Stage B removes the decorator (REQ-019) and removes the import from
apps.py (REQ-018) so the function is never connected automatically at
application startup.

WHY THIS FILE IS KEPT
---------------------
1. Historical documentation: preserves the exact Stage A implementation
   as a reference for the architectural comparison.

2. Test support: orders/tests/test_signals.py imports
   update_user_stats_on_order_save by name so it can call
   post_save.connect() / post_save.disconnect() within individual test
   cases, proving Stage A behaviour without affecting the Stage B runtime.

   Calling post_save.disconnect() for a receiver that is not connected
   is a safe no-op in Django, so the tearDown() calls in test_signals.py
   continue to work correctly without modification.

STAGE B RUNTIME STATE
---------------------
- The function exists in memory but is never connected to any signal.
- Order.objects.create() does NOT trigger this function.
- UserStats is only updated when orders/services.create_order() is called.
"""
from .models import Order, UserStats


def update_user_stats_on_order_save(sender, instance, created, **kwargs):
    """
    [STAGE A — DISCONNECTED IN STAGE B]

    Update UserStats whenever a new Order row is inserted.

    This function is no longer connected to the post_save signal.
    It is preserved so that test_signals.py can import it by name for
    explicit connect/disconnect calls within test setUp/tearDown without
    raising an ImportError.

    Original Stage A behaviour (when decorated and connected):
      - Fired only when created=True (new INSERT, not an UPDATE).
      - Called UserStats.objects.get_or_create(user=instance.user).
      - Incremented stats.order_count by 1.
      - Added instance.total to stats.total_spent.
      - Called stats.save().

    This logic now lives in orders/services.create_order() wrapped in
    transaction.atomic(), which guarantees consistency and is not bypassed
    by bulk database operations.

    Args:
        sender:   The model class that sent the signal (Order).
        instance: The Order instance that was just saved.
        created:  True for INSERT; False for UPDATE.
        **kwargs: Additional keyword arguments from the signal dispatcher.
    """
    if created:
        user = instance.user
        stats, _ = UserStats.objects.get_or_create(user=user)
        stats.order_count += 1
        stats.total_spent += instance.total
        stats.save()