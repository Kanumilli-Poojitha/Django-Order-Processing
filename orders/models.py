"""
Domain models for the orders application.

Order
-----
Represents a single purchase made by a User.  Holds the monetary total
and a lifecycle status string.

UserStats
---------
A denormalised, one-to-one aggregate record per User that caches
order_count and total_spent so that user-dashboard queries can be served
with a single indexed lookup rather than an aggregation over the full
orders table.

In Stage A these stats are kept in sync via a post_save signal receiver
defined in orders/signals.py.
"""
from django.db import models
from django.contrib.auth.models import User


class Order(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='orders',
    )
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='pending')

    def __str__(self):
        return f"Order {self.id} for {self.user.username} - {self.status}"


class UserStats(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='stats',
    )
    total_spent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    order_count = models.IntegerField(default=0)

    def __str__(self):
        return f"Stats for {self.user.username}"