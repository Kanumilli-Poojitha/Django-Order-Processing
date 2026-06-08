from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction

from .models import Order, UserStats


def create_order(user: User, total: float) -> Order:
    with transaction.atomic():
        order = Order.objects.create(user=user, total=Decimal(str(total)))

        stats, _ = UserStats.objects.get_or_create(user=user)
        stats.order_count += 1
        stats.total_spent += order.total
        stats.save()

    return order