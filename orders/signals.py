from .models import Order, UserStats


def update_user_stats_on_order_save(sender, instance, created, **kwargs):
    if created:
        user = instance.user
        stats, _ = UserStats.objects.get_or_create(user=user)
        stats.order_count += 1
        stats.total_spent += instance.total
        stats.save()