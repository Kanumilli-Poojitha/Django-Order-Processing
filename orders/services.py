"""
Stage B service layer — the single authoritative entry point for all
order-creation business logic (REQ-016).

ARCHITECTURAL RATIONALE
-----------------------
Stage A kept UserStats in sync via a post_save signal receiver.  That
approach has three critical failure modes:

1. Bulk bypass
   QuerySet.update() and bulk_create() issue raw SQL and never call
   .save(), so post_save never fires and UserStats silently drifts.

2. No transactional atomicity
   The signal fires after the Order INSERT commits.  If the receiver
   raises an exception, the Order row persists but UserStats is not
   updated — the two tables are inconsistent with no automatic recovery.

3. Hidden control flow
   A caller who reads Order.objects.create(user=u, total=t) has no
   indication that a second table is being modified as a side-effect.
   This "action at a distance" makes the codebase hard to reason about,
   test, and refactor.

The service layer resolves all three problems:

Explicitness   — create_order() clearly does two things; the function
                 name and module location make the business process
                 self-documenting.

Atomicity      — transaction.atomic() (REQ-017) ensures the Order INSERT
                 and the UserStats UPDATE either both commit or both roll
                 back.  No partial-write inconsistency is possible.

Testability    — there is no global signal state to manage; tests call
                 create_order() and assert outcomes directly.

Bulk-safety    — bulk_create() and QuerySet.update() can be used freely
                 elsewhere without accidentally skipping accounting logic,
                 because the only way to trigger the business process is
                 to call this function.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction

from .models import Order, UserStats


def create_order(user: User, total: float) -> Order:
    """
    Create an Order and update the user's aggregate statistics atomically.

    Both the Order INSERT and the UserStats UPDATE are wrapped in a single
    database transaction via transaction.atomic() (REQ-017).  If the
    UserStats save raises any exception the Order INSERT is rolled back,
    preventing the data inconsistency that the Stage A signal approach
    could not guarantee.

    Args:
        user:  The User instance placing the order.
        total: The monetary total for the order.  Accepts int, float, or
               str and coerces to Decimal internally to avoid the
               floating-point representation errors that arise when
               passing a Python float directly to a DecimalField.

    Returns:
        The newly created and persisted Order instance (REQ-024).

    Raises:
        Any database or validation exception propagates to the caller
        after the transaction has been fully rolled back.  The caller
        receives a clean failure with no partial writes surviving.
    """
    with transaction.atomic():
        order = Order.objects.create(user=user, total=Decimal(str(total)))

        stats, _ = UserStats.objects.get_or_create(user=user)
        stats.order_count += 1
        stats.total_spent += order.total
        stats.save()

    return order