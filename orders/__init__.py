# NOTE: default_app_config is deprecated as of Django 3.2 (the framework
# auto-discovers AppConfig subclasses), but it is explicitly required by the
# project specification and remains fully functional.  Django honours it while
# emitting a RemovedInDjango41Warning that does not affect runtime behaviour.
default_app_config = 'orders.apps.OrdersConfig'