"""
Stage B Tests — Service layer (REQ-020 through REQ-026).

PURPOSE
-------
This module is the complete verification suite for orders/services.py.
It proves every requirement introduced by Stage B:

REQ-020  Direct Order.objects.create() does NOT update UserStats.
         The post_save signal has been removed from apps.py; calling the
         ORM directly must have no side-effects on UserStats.

REQ-021  services.create_order() persists a new Order row with the
         correct user, total, and default status of 'pending'.

REQ-022  services.create_order() creates or updates UserStats with
         order_count=1 and total_spent equal to the order total on the
         first call.

REQ-023  Calling services.create_order() multiple times accumulates
         order_count and total_spent correctly.

REQ-024  services.create_order() returns the Order instance.

REQ-025  If UserStats.save() raises an exception inside create_order(),
         transaction.atomic() rolls back the entire transaction — the
         Order INSERT does not persist.

REQ-026  A failed second create_order() call does not corrupt the
         UserStats written by a preceding successful call.

DESIGN NOTES
------------
These tests do NOT connect or disconnect any signal.  In Stage B apps.py
no longer imports orders.signals, so Order.objects.create() has no
automatic side-effects.  The tests verify this fact directly in
test_direct_create_does_not_update_stats (REQ-020).

Django's TestCase wraps each test method in a transaction that is rolled
back after the test completes, providing a clean database slate without
any manual teardown.

The rollback tests (REQ-025, REQ-026) use unittest.mock.patch to force
UserStats.save() to raise django.db.IntegrityError, simulating a
database-level constraint violation inside the atomic block.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from orders import services
from orders.models import Order, UserStats


class ServiceLayerTests(TestCase):
    """
    Core tests for orders.services.create_order().

    A single user (self.user) is created in setUp() and is reused across
    all test methods in this class.  Django's TestCase transaction rollback
    resets the database after each method, so self.user is always the only
    user and starts every test with no Orders and no UserStats.
    """

    def setUp(self):
        """Create a fresh user for each test method."""
        self.user = User.objects.create_user(
            username='serviceuser',
            email='service@example.com',
            password='testpassword123',
        )

    # ------------------------------------------------------------------
    # REQ-020 — Direct Order.objects.create() does NOT update UserStats
    # ------------------------------------------------------------------

    def test_direct_create_does_not_update_stats(self):
        """
        REQ-020: After Stage B, the post_save signal has been removed from
        apps.py.  Calling Order.objects.create() directly must produce no
        side-effects on UserStats.

        This is the definitive proof that:
          (a) The signal is disconnected in the Stage B runtime.
          (b) All accounting logic is gated exclusively behind
              services.create_order().

        Procedure:
          1. Call Order.objects.create() directly, bypassing the service.
          2. Assert that no UserStats row exists for the user.
        """
        Order.objects.create(user=self.user, total=Decimal('200.00'))

        stats_exists = UserStats.objects.filter(user=self.user).exists()

        self.assertFalse(
            stats_exists,
            msg=(
                "UserStats must NOT be created when Order.objects.create() "
                "is called directly. The post_save signal has been removed "
                "in Stage B; business logic lives only in orders/services.py."
            ),
        )

    # ------------------------------------------------------------------
    # REQ-021 — create_order persists an Order row
    # ------------------------------------------------------------------

    def test_create_order_creates_order_object(self):
        """
        REQ-021: services.create_order() must INSERT a new Order row into
        the database with the correct user, total, and default status.
        """
        order = services.create_order(user=self.user, total=100.00)

        self.assertIsNotNone(
            order.pk,
            msg="The returned Order must have a primary key (be persisted).",
        )

        db_order = Order.objects.get(pk=order.pk)
        self.assertEqual(
            db_order.user,
            self.user,
            msg="The persisted Order must belong to self.user.",
        )
        self.assertEqual(
            db_order.total,
            Decimal('100.00'),
            msg="The persisted Order.total must be 100.00.",
        )
        self.assertEqual(
            db_order.status,
            'pending',
            msg="Newly created orders must have the default status 'pending'.",
        )

    # ------------------------------------------------------------------
    # REQ-022 — create_order creates/updates UserStats correctly
    # ------------------------------------------------------------------

    def test_create_order_creates_userstats(self):
        """
        REQ-022: services.create_order() must create a UserStats row via
        get_or_create with order_count=1 and total_spent equal to the
        order total on the first call for a user.
        """
        services.create_order(user=self.user, total=75.50)

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(
            stats.order_count,
            1,
            msg="order_count must be 1 after the first create_order() call.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('75.50'),
            msg="total_spent must equal the first order's total: 75.50.",
        )

    def test_create_order_updates_existing_userstats(self):
        """
        REQ-022 (update path): If a UserStats row already exists for the
        user, services.create_order() must update it rather than creating
        a duplicate, and only one UserStats row must exist.
        """
        services.create_order(user=self.user, total=40.00)

        stats = UserStats.objects.get(user=self.user)
        self.assertEqual(stats.order_count, 1)
        self.assertEqual(stats.total_spent, Decimal('40.00'))

        services.create_order(user=self.user, total=60.00)
        stats.refresh_from_db()

        self.assertEqual(
            stats.order_count,
            2,
            msg="order_count must be 2 after the second create_order() call.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('100.00'),
            msg="total_spent must be 40.00 + 60.00 = 100.00.",
        )
        self.assertEqual(
            UserStats.objects.filter(user=self.user).count(),
            1,
            msg="Only one UserStats row must exist; get_or_create must not "
                "create a duplicate.",
        )

    # ------------------------------------------------------------------
    # REQ-023 — Accumulation across multiple calls
    # ------------------------------------------------------------------

    def test_create_order_accumulates_userstats(self):
        """
        REQ-023: Calling services.create_order() three times must result
        in order_count=3 and total_spent equal to the running sum of all
        three totals.
        """
        services.create_order(user=self.user, total=50.00)
        services.create_order(user=self.user, total=30.25)
        services.create_order(user=self.user, total=19.75)

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(
            stats.order_count,
            3,
            msg="order_count must be 3 after three create_order() calls.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('100.00'),
            msg="total_spent must be 50.00 + 30.25 + 19.75 = 100.00.",
        )

    # ------------------------------------------------------------------
    # REQ-024 — create_order returns the Order instance
    # ------------------------------------------------------------------

    def test_create_order_returns_order_instance(self):
        """
        REQ-024: services.create_order() must return the newly created
        Order object, not None or any other value.
        """
        result = services.create_order(user=self.user, total=42.00)

        self.assertIsInstance(
            result,
            Order,
            msg="create_order() must return an Order instance.",
        )
        self.assertEqual(
            result.user,
            self.user,
            msg="The returned Order must belong to self.user.",
        )
        self.assertEqual(
            result.total,
            Decimal('42.00'),
            msg="The returned Order.total must be 42.00.",
        )
        self.assertIsNotNone(
            result.pk,
            msg="The returned Order must be persisted (have a primary key).",
        )

    # ------------------------------------------------------------------
    # REQ-025 — Atomic rollback when UserStats.save() fails
    # ------------------------------------------------------------------

    def test_atomic_rollback_on_userstats_failure(self):
        """
        REQ-025: If UserStats.save() raises an IntegrityError inside
        create_order(), transaction.atomic() must roll back the entire
        transaction.

        Expected outcome:
          - The IntegrityError propagates to the caller.
          - No Order row is persisted (the INSERT is rolled back).
          - No UserStats row is persisted.

        We force the failure by patching UserStats.save to raise
        IntegrityError, simulating a database-level constraint violation.
        """
        initial_order_count = Order.objects.count()

        with patch.object(
            UserStats,
            'save',
            side_effect=IntegrityError("forced constraint violation"),
        ):
            with self.assertRaises(
                IntegrityError,
                msg="IntegrityError from UserStats.save() must propagate "
                    "to the caller.",
            ):
                services.create_order(user=self.user, total=500.00)

        self.assertEqual(
            Order.objects.count(),
            initial_order_count,
            msg=(
                "The Order INSERT must be rolled back when UserStats.save() "
                "raises an exception inside transaction.atomic(). "
                f"Expected {initial_order_count} orders; "
                f"found {Order.objects.count()}."
            ),
        )
        self.assertFalse(
            UserStats.objects.filter(user=self.user).exists(),
            msg="No UserStats row must survive a rolled-back transaction.",
        )

    # ------------------------------------------------------------------
    # REQ-026 — Rollback does not corrupt preceding successful call
    # ------------------------------------------------------------------

    def test_atomic_rollback_preserves_preceding_successful_call(self):
        """
        REQ-026: A failed second create_order() call must not corrupt the
        UserStats written by the first successful call.

        Procedure:
          1. First call — succeeds.  Assert order_count=1, total_spent=100.
          2. Second call — UserStats.save() is patched to raise
             IntegrityError.  The transaction rolls back.
          3. Assert that UserStats still reflects only the first call:
             order_count=1, total_spent=100.00.
          4. Assert that exactly one Order row exists (from the first call).
        """
        # Step 1: first call succeeds
        services.create_order(user=self.user, total=100.00)

        stats_after_first = UserStats.objects.get(user=self.user)
        self.assertEqual(stats_after_first.order_count, 1)
        self.assertEqual(stats_after_first.total_spent, Decimal('100.00'))
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)

        # Step 2: second call — forced failure inside the atomic block
        with patch.object(
            UserStats,
            'save',
            side_effect=IntegrityError("forced constraint violation"),
        ):
            with self.assertRaises(IntegrityError):
                services.create_order(user=self.user, total=200.00)

        # Step 3: UserStats must reflect only the first successful call
        stats_after_failure = UserStats.objects.get(user=self.user)
        self.assertEqual(
            stats_after_failure.order_count,
            1,
            msg=(
                "order_count must still be 1 after a failed second call. "
                "The rollback must not have touched the first call's data."
            ),
        )
        self.assertEqual(
            stats_after_failure.total_spent,
            Decimal('100.00'),
            msg=(
                "total_spent must still be 100.00 after a failed second call. "
                "The rollback must not have touched the first call's data."
            ),
        )

        # Step 4: exactly one Order row — the one from the first call
        self.assertEqual(
            Order.objects.filter(user=self.user).count(),
            1,
            msg=(
                "Exactly one Order must exist after a failed second call. "
                "The rolled-back second Order INSERT must not have persisted."
            ),
        )


class MultiUserServiceTests(TestCase):
    """
    Verify that UserStats are tracked independently per user and that
    one user's orders have no effect on another user's statistics.
    """

    def test_stats_are_independent_per_user(self):
        """
        Create two orders for user_a and one order for user_b.
        Assert each user's UserStats reflects only their own orders.
        """
        user_a = User.objects.create_user(
            username='usera',
            email='usera@example.com',
            password='testpassword123',
        )
        user_b = User.objects.create_user(
            username='userb',
            email='userb@example.com',
            password='testpassword123',
        )

        services.create_order(user=user_a, total=100.00)
        services.create_order(user=user_a, total=50.00)
        services.create_order(user=user_b, total=200.00)

        stats_a = UserStats.objects.get(user=user_a)
        stats_b = UserStats.objects.get(user=user_b)

        self.assertEqual(stats_a.order_count, 2)
        self.assertEqual(stats_a.total_spent, Decimal('150.00'))
        self.assertEqual(stats_b.order_count, 1)
        self.assertEqual(stats_b.total_spent, Decimal('200.00'))

    def test_create_order_for_user_with_no_prior_stats(self):
        """
        services.create_order() must create a new UserStats row via
        get_or_create when the user has never placed an order before,
        confirming the row-creation path works correctly in isolation.
        """
        user = User.objects.create_user(
            username='brandnewuser',
            email='new@example.com',
            password='testpassword123',
        )

        self.assertFalse(
            UserStats.objects.filter(user=user).exists(),
            msg="No UserStats must exist before the first create_order() call.",
        )

        services.create_order(user=user, total=33.33)

        stats = UserStats.objects.get(user=user)
        self.assertEqual(stats.order_count, 1)
        self.assertEqual(stats.total_spent, Decimal('33.33'))