from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import TestCase

from orders.models import Order, UserStats
from orders.signals import update_user_stats_on_order_save


class OrderSignalTests(TestCase):

    def setUp(self):
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
        post_save.disconnect(
            receiver=update_user_stats_on_order_save,
            sender=Order,
        )

    # ------------------------------------------------------------------
    # Test 1 — Single order creation creates UserStats
    # ------------------------------------------------------------------

    def test_signal_creates_userstats_on_first_order(self):
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

    def setUp(self):
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
        Order.objects.create(user=self.user, total=Decimal('10.00'))
        Order.objects.create(user=self.user, total=Decimal('20.00'))
        Order.objects.create(user=self.user, total=Decimal('30.00'))

        stats = UserStats.objects.get(user=self.user)

        self.assertEqual(stats.order_count, 3)
        self.assertEqual(stats.total_spent, Decimal('60.00'))