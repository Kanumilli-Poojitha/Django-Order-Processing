"""
Stage A Tests — Signal-based behaviour.

PURPOSE
-------
This module is the complete test suite for the Stage A signal-based
implementation.  It demonstrates and proves:

  1. Creating a single Order via Order.objects.create() triggers the
     post_save receiver and creates a UserStats row with correct values.

  2. Creating multiple Orders correctly accumulates order_count and
     total_spent in UserStats.

  3. Calling .save() on an existing Order (created=False) does NOT
     re-trigger the stats update, because the receiver guards with
     ``if created``.

  4. QuerySet.update() issues a raw SQL UPDATE statement, never calls
     .save() on any model instance, and therefore never fires post_save.
     UserStats remains unchanged — the "bulk operation bypass" trap.

  5. Test isolation: every TestCase class that interacts with the signal
     explicitly connects the receiver in setUp() and disconnects it in
     tearDown(), preventing signal state from leaking between test cases.

DESIGN NOTES
------------
apps.py registers the signal by importing orders.signals in ready().
That registration happens once at application startup and persists for
the lifetime of the process.

Django's TestCase wraps each test method in a transaction that is rolled
back after the test, giving a clean database slate.  However, signal
*connections* are NOT rolled back by transactions — they are in-memory
state.  A test that calls post_save.disconnect() in tearDown() genuinely
removes the connection until the next setUp() call reinstates it.

By explicitly calling post_save.connect() in setUp() we also make these
tests self-contained: they work correctly whether apps.py registered the
signal at startup or not, which means the entire test file can be run
in isolation with `python manage.py test orders.tests.test_signals`.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import TestCase

from orders.models import Order, UserStats
from orders.signals import update_user_stats_on_order_save


class OrderSignalTests(TestCase):
    """
    Core signal behaviour tests.

    setUp()    — connects the Stage A receiver so every test in this
                 class starts with the signal active.
    tearDown() — disconnects the receiver after every test method.
                 This is the required isolation pattern (REQ-015):
                 post_save.disconnect(receiver=..., sender=Order).
    """

    def setUp(self):
        """
        Connect the post_save receiver and create a fresh test user.

        The explicit connect() call makes this class self-contained.
        Django's signal system is idempotent for the same receiver/sender
        pair: connecting a receiver that is already connected does not
        register it twice.
        """
        post_save.connect(
            receiver=update_user_stats_on_order_save,
            sender=Order,
        )
        self.user = User.objects.create_user(
            username='signaluser',
            email='signal@example.com',
            password='testpassword123',
        )

    def tearDown(self):
        """
        Disconnect the post_save receiver after every test method.

        This is the critical test-isolation step.  Without this call,
        the receiver would remain connected for every subsequent test in
        the entire test suite — including Stage B tests that assert that
        Order.objects.create() does NOT update UserStats.  Leaving the
        receiver connected would cause those Stage B tests to fail with
        false positives.

        The call matches the exact signature required by the spec:
            post_save.disconnect(receiver=<function>, sender=Order)
        """
        post_save.disconnect(
            receiver=update_user_stats_on_order_save,
            sender=Order,
        )

    # ------------------------------------------------------------------
    # Test 1 — Single order creation creates UserStats
    # ------------------------------------------------------------------

    def test_signal_creates_userstats_on_first_order(self):
        """
        Creating a single Order via Order.objects.create() must trigger
        the post_save receiver, which must:
          - create a UserStats row for the user (get_or_create path)
          - set order_count to 1
          - set total_spent to the order's total
        """
        order = Order.objects.create(
            user=self.user,
            total=Decimal('75.00'),
        )

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(
            stats.order_count,
            1,
            msg="order_count must be 1 after a single Order.objects.create().",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('75.00'),
            msg="total_spent must equal the first order's total: 75.00.",
        )
        self.assertEqual(
            stats.total_spent,
            order.total,
            msg="total_spent must exactly match the order.total field value.",
        )

    # ------------------------------------------------------------------
    # Test 2 — Multiple orders accumulate correctly
    # ------------------------------------------------------------------

    def test_signal_accumulates_multiple_orders(self):
        """
        Creating three Orders must result in:
          - order_count == 3
          - total_spent == sum of all three totals

        Each Order.objects.create() call fires post_save, which
        increments both counters.
        """
        Order.objects.create(user=self.user, total=Decimal('100.00'))
        Order.objects.create(user=self.user, total=Decimal('50.50'))
        Order.objects.create(user=self.user, total=Decimal('25.25'))

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(
            stats.order_count,
            3,
            msg="order_count must be 3 after creating three orders.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('175.75'),
            msg=(
                "total_spent must be 100.00 + 50.50 + 25.25 = 175.75 "
                "after three orders."
            ),
        )

    # ------------------------------------------------------------------
    # Test 3 — Signal only fires on created=True
    # ------------------------------------------------------------------

    def test_signal_only_fires_on_creation_not_update(self):
        """
        Updating a field on an existing Order and calling .save() triggers
        post_save with created=False.  The receiver's ``if created`` guard
        must prevent any change to UserStats.

        This proves that repeated .save() calls on the same Order do not
        inflate order_count or total_spent.
        """
        order = Order.objects.create(
            user=self.user,
            total=Decimal('100.00'),
        )

        stats = UserStats.objects.get(user=self.user)
        self.assertEqual(stats.order_count, 1)
        self.assertEqual(stats.total_spent, Decimal('100.00'))

        # Trigger post_save with created=False
        order.status = 'shipped'
        order.save()

        stats.refresh_from_db()

        self.assertEqual(
            stats.order_count,
            1,
            msg=(
                "order_count must remain 1 after an update-save. "
                "The receiver must not run when created=False."
            ),
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('100.00'),
            msg=(
                "total_spent must remain 100.00 after an update-save. "
                "The receiver must not run when created=False."
            ),
        )

    # ------------------------------------------------------------------
    # Test 4 — QuerySet.update() bypasses post_save  (REQ-014)
    # ------------------------------------------------------------------

    def test_bulk_update_bypasses_signal(self):
        """
        Prove that QuerySet.update() does NOT trigger the post_save signal.

        This test demonstrates the critical "bulk operation bypass" trap
        described in the project specification (REQ-014).

        Procedure (required by spec):

        Step 1 — Create two Orders for self.user via Order.objects.create().
                 Assert UserStats reflects order_count=2 and the correct
                 total_spent.

        Step 2 — Create a third Order for self.user.
                 Assert UserStats updates to order_count=3.

        Step 3 — Create several Orders for a different user (other_user)
                 using Order.objects.bulk_create().
                 bulk_create() does NOT fire post_save, so other_user gets
                 no UserStats row and self.user's stats are unaffected.

        Step 4 — Reassign those bulk-created Orders to self.user using
                 QuerySet.update().
                 QuerySet.update() issues a single raw SQL UPDATE statement.
                 Django does NOT instantiate model objects.
                 Django does NOT call .save() on any instance.
                 Django does NOT fire the pre_save or post_save signals.

        Step 5 — Fetch UserStats for self.user again.
                 Assert order_count is still 3 (not 6).
                 Assert total_spent is still 55.00 (not 70.00).
                 This proves the signal logic did not run.
        """
        # ------------------------------------------------------------------
        # Step 1: two orders via create(), verify initial stats
        # ------------------------------------------------------------------
        Order.objects.create(user=self.user, total=Decimal('10.00'))
        Order.objects.create(user=self.user, total=Decimal('20.00'))

        stats = UserStats.objects.get(user=self.user)
        self.assertEqual(
            stats.order_count,
            2,
            msg="Step 1: order_count must be 2 after two Order.objects.create() calls.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('30.00'),
            msg="Step 1: total_spent must be 10.00 + 20.00 = 30.00.",
        )

        # ------------------------------------------------------------------
        # Step 2: third order, stats must update to 3 / 55.00
        # ------------------------------------------------------------------
        Order.objects.create(user=self.user, total=Decimal('25.00'))

        stats.refresh_from_db()
        self.assertEqual(
            stats.order_count,
            3,
            msg="Step 2: order_count must be 3 after the third Order.objects.create().",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('55.00'),
            msg="Step 2: total_spent must be 10.00 + 20.00 + 25.00 = 55.00.",
        )

        # ------------------------------------------------------------------
        # Step 3: create orders for other_user via bulk_create
        # ------------------------------------------------------------------
        other_user = User.objects.create_user(
            username='otheruser',
            email='other@example.com',
            password='testpassword123',
        )
        bulk_orders = [
            Order(user=other_user, total=Decimal('5.00')),
            Order(user=other_user, total=Decimal('5.00')),
            Order(user=other_user, total=Decimal('5.00')),
        ]
        Order.objects.bulk_create(bulk_orders)

        # Confirm the rows were inserted
        self.assertEqual(
            Order.objects.filter(user=other_user).count(),
            3,
            msg="Step 3: three bulk-created orders must exist for other_user.",
        )

        # ------------------------------------------------------------------
        # Step 4: reassign other_user's orders to self.user via update()
        # This is a raw SQL UPDATE — .save() is never called — post_save
        # is never dispatched — the signal receiver never runs.
        # ------------------------------------------------------------------
        rows_updated = Order.objects.filter(user=other_user).update(
            user=self.user
        )
        self.assertEqual(
            rows_updated,
            3,
            msg="Step 4: QuerySet.update() must report 3 rows updated.",
        )

        # ------------------------------------------------------------------
        # Step 5: UserStats for self.user must be UNCHANGED at 3 / 55.00
        # ------------------------------------------------------------------
        stats.refresh_from_db()

        self.assertEqual(
            stats.order_count,
            3,
            msg=(
                "Step 5: order_count must still be 3 after QuerySet.update(). "
                "The post_save signal is NOT fired by a bulk SQL UPDATE "
                "operation, so the receiver never ran and the counter was "
                "never incremented for the reassigned orders."
            ),
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('55.00'),
            msg=(
                "Step 5: total_spent must still be 55.00 after QuerySet.update(). "
                "The post_save signal is NOT fired by a bulk SQL UPDATE "
                "operation, so the receiver never ran and the total was "
                "never incremented for the reassigned orders."
            ),
        )

        # Sanity check: the DB correctly shows 6 orders owned by self.user
        self.assertEqual(
            Order.objects.filter(user=self.user).count(),
            6,
            msg=(
                "Sanity: self.user must own 6 orders in the database after "
                "the 3 original creates plus the 3 reassigned via update()."
            ),
        )

    # ------------------------------------------------------------------
    # Test 5 — Clean database state at the start of each test
    # ------------------------------------------------------------------

    def test_no_stats_exist_before_any_order(self):
        """
        Before any Order is created for self.user, no UserStats row must
        exist.  This verifies that:

        (a) Django TestCase's transaction rollback provides a clean
            database slate for each test method.
        (b) The signal receiver does not create a UserStats row unless
            an Order is actually created.
        """
        with self.assertRaises(UserStats.DoesNotExist):
            UserStats.objects.get(user=self.user)

    # ------------------------------------------------------------------
    # Test 6 — get_or_create path: existing UserStats row is updated
    # ------------------------------------------------------------------

    def test_signal_updates_existing_userstats_row(self):
        """
        If a UserStats row already exists for the user (created by a
        previous order), the receiver must update it rather than creating
        a duplicate, and the totals must accumulate correctly.
        """
        # First order — creates the UserStats row
        Order.objects.create(user=self.user, total=Decimal('40.00'))
        stats = UserStats.objects.get(user=self.user)
        self.assertEqual(stats.order_count, 1)
        self.assertEqual(stats.total_spent, Decimal('40.00'))

        # Second order — must UPDATE the existing UserStats row
        Order.objects.create(user=self.user, total=Decimal('60.00'))
        stats.refresh_from_db()

        self.assertEqual(
            stats.order_count,
            2,
            msg="order_count must be 2 after the second order.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('100.00'),
            msg="total_spent must be 40.00 + 60.00 = 100.00 after the second order.",
        )
        # Confirm only one UserStats row exists (get_or_create, not create)
        self.assertEqual(
            UserStats.objects.filter(user=self.user).count(),
            1,
            msg="Only one UserStats row must exist; get_or_create must not "
                "have created a duplicate.",
        )


class SignalIsolationDemonstrationTests(TestCase):
    """
    A second independent TestCase class.

    Proves that the setUp/tearDown connect/disconnect pattern keeps signal
    state correctly isolated between different test classes.  At the moment
    setUp() runs here, the receiver may or may not be connected (depending
    on test execution order).  Calling connect() unconditionally in setUp()
    and disconnect() in tearDown() guarantees a predictable state for every
    single test method regardless of what other test classes have done.
    """

    def setUp(self):
        """Connect the receiver and create a test user."""
        post_save.connect(
            receiver=update_user_stats_on_order_save,
            sender=Order,
        )
        self.user = User.objects.create_user(
            username='isolationuser',
            email='isolation@example.com',
            password='testpassword123',
        )

    def tearDown(self):
        """Disconnect the receiver after every test in this class."""
        post_save.disconnect(
            receiver=update_user_stats_on_order_save,
            sender=Order,
        )

    def test_signal_works_in_second_test_class(self):
        """
        The signal must function correctly inside this class even though
        OrderSignalTests.tearDown() disconnected it for every test in that
        class.  The connect() call in this setUp() re-establishes the
        connection, proving that isolation is symmetric and complete.
        """
        Order.objects.create(user=self.user, total=Decimal('99.99'))

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(
            stats.order_count,
            1,
            msg="order_count must be 1 after one order in the second test class.",
        )
        self.assertEqual(
            stats.total_spent,
            Decimal('99.99'),
            msg="total_spent must be 99.99 in the second test class.",
        )

    def test_multiple_orders_in_second_test_class(self):
        """
        Accumulation must work correctly in this class too, confirming
        the receiver is properly active after each setUp() connect() call.
        """
        Order.objects.create(user=self.user, total=Decimal('10.00'))
        Order.objects.create(user=self.user, total=Decimal('20.00'))
        Order.objects.create(user=self.user, total=Decimal('30.00'))

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(stats.order_count, 3)
        self.assertEqual(stats.total_spent, Decimal('60.00'))