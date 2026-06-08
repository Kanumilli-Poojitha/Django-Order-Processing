"""Admin registrations for the orders application."""
from django.contrib import admin

from .models import Order, UserStats

admin.site.register(Order)
admin.site.register(UserStats)