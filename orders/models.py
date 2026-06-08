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