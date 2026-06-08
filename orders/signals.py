"""
Stage A — Signal-based implementation.

This module defines the post_save receiver that keeps UserStats in sync
whenever a new Order is created.

HOW IT IS CONNECTED
-------------------
orders/apps.py imports this module inside OrdersConfig.ready().  That
import causes the @receiver decorator to be evaluated, which calls
post_save.connect(update_user_stats_on_order_save, sender=Order).
Django then calls the function automatically after every Order.save()
that results in a database INSERT (created=True).

KNOWN LIMITATIONS (demonstrated by the Stage A tests)
------------------------------------------------------
1. Bulk bypass
   QuerySet.update() and bulk_create() operate at the SQL level and never
   call .save() on individual instances.  The post_save signal therefore
   never fires, leaving UserStats silently out of sync.

2. No transactional atomicity
   The signal fires after the Order INSERT has already been committed.
   If update_user_stats_on_order_save raises an exception, the Order row
   persists in the database but UserStats is not updated — the two tables
   are now inconsistent.

3. Test isolation cost
   Because signal receivers are registered globally, tests that rely on
   signal behaviour must explicitly disconnect the receiver in tearDown()
   to prevent state from leaking into unrelated test cases.

These limitations are addressed in Stage B by moving the business logic
into a service function wrapped in transaction.atomic().
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Order, UserStats


@receiver(post_save, sender=Order)
def update_user_stats_on_order_save(sender, instance, created, **kwargs):
    """
    Update UserStats whenever a new Order row is inserted.

    The ``created`` guard ensures this function is a no-op when an
    existing Order is updated (e.g. status changed to 'shipped'), so the
    total_spent and order_count are not double-counted.

    Args:
        sender:   The model class that sent the signal (always Order here).
        instance: The Order instance that was just saved to the database.
        created:  True when the database operation was an INSERT (new row);
                  False when it was an UPDATE (existing row).
        **kwargs: Additional keyword arguments supplied by Django's signal
                  dispatcher (e.g. ``raw``, ``using``, ``update_fields``).
    """
    if created:
        user = instance.user

        # get_or_create handles the case where the user has never placed an
        # order before — it creates a zeroed-out UserStats row on demand.
        stats, _ = UserStats.objects.get_or_create(user=user)

        stats.order_count += 1
        stats.total_spent += instance.total
        stats.save()