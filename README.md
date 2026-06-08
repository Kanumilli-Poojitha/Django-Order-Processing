# Mastering Django: Signals to Service Layer — Stage A

Stage A of a two-phase Django project that demonstrates signal-based
order management and exposes its inherent failure modes through a
comprehensive test suite.

---

## Table of Contents

1. [What Stage A Implements](#what-stage-a-implements)
2. [Prerequisites](#prerequisites)
3. [Quick Start with Docker](#quick-start-with-docker)
4. [Environment Variables](#environment-variables)
5. [Running Tests](#running-tests)
6. [Manual Shell Verification](#manual-shell-verification)
7. [Project Structure](#project-structure)
8. [Signal Architecture Explained](#signal-architecture-explained)
9. [Known Limitations Demonstrated by Tests](#known-limitations-demonstrated-by-tests)
10. [Verification Commands Reference](#verification-commands-reference)

---

## What Stage A Implements

Stage A is the signal-based implementation:

- `Order` and `UserStats` models in `orders/models.py`
- A `post_save` receiver in `orders/signals.py` that updates `UserStats`
  every time a new `Order` is created
- Signal registration via `OrdersConfig.ready()` in `orders/apps.py`
- A test suite in `orders/tests/test_signals.py` that proves both the
  correct behaviour and the two critical failure modes:
  - `QuerySet.update()` bypasses `post_save` entirely
  - Tests must explicitly disconnect the signal in `tearDown()` to
    prevent cross-test contamination

---

## Prerequisites

| Tool | Minimum Version |
|---|---|
| Docker | 24 |
| Docker Compose | 2.20 |
| git | any recent |

No local Python installation is required.

---

## Quick Start with Docker

### 1. Clone the repository

```bash
git clone <repository-url>
cd signal_project
```

### 2. Create your environment file

```bash
cp .env.example .env
```

The defaults in `.env.example` work for local development without
modification.

### 3. Build and start the containers

```bash
docker-compose up --build
```

Startup sequence:

1. `db` starts → PostgreSQL initialises → `pg_isready` healthcheck passes
   → `db` is marked **healthy**
2. `app` starts only after `db` is healthy (`depends_on: condition:
   service_healthy`)
3. `entrypoint.sh` executes `python manage.py migrate --noinput`
   automatically
4. Django development server starts on `0.0.0.0:8000`
5. App healthcheck (`curl -f http://localhost:8000/health/`) passes →
   `app` is marked **healthy**

Both containers reach **healthy** within approximately 30–60 seconds.

### 4. Verify both containers are healthy

```bash
docker-compose ps
```

Expected:

```
NAME                       STATUS
signal_project-db-1        running (healthy)
signal_project-app-1       running (healthy)
```

### 5. Verify migrations were applied

```bash
docker-compose exec app python manage.py showmigrations orders
```

Expected:

```
orders
 [X] 0001_initial
```

### 6. Stop the stack

```bash
docker-compose down
```

To also remove the PostgreSQL data volume:

```bash
docker-compose down -v
```

---

## Environment Variables

Documented in `.env.example`.  All seven variables required by the
specification:

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Django cryptographic secret key | `django-insecure-abc...` |
| `DEBUG` | Enable debug mode (`True` / `False`) | `True` |
| `POSTGRES_DB` | PostgreSQL database name | `signal_project_db` |
| `POSTGRES_USER` | PostgreSQL username | `postgres_user` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `strongpassword` |
| `DB_HOST` | Database host (service name in Docker) | `db` |
| `DB_PORT` | Database port | `5432` |

**Never commit your real `.env` file.** It is listed in `.gitignore`.

---

## Running Tests

All tests run inside the Docker container against PostgreSQL.

### Run the full Stage A test suite

```bash
docker-compose exec app python manage.py test orders.tests.test_signals
```

### Run a specific test class

```bash
docker-compose exec app python manage.py test \
  orders.tests.test_signals.OrderSignalTests
```

### Run a specific test method

```bash
docker-compose exec app python manage.py test \
  orders.tests.test_signals.OrderSignalTests.test_bulk_update_bypasses_signal
```

### Run all tests in the orders app

```bash
docker-compose exec app python manage.py test orders
```

### Expected output

```
Found 8 test(s).
........
----------------------------------------------------------------------
Ran 8 tests in X.XXXs

OK
```

---

## Manual Shell Verification

```bash
docker-compose exec app python manage.py shell
```

```python
from django.contrib.auth.models import User
from orders.models import Order, UserStats

# Create a test user
user = User.objects.create_user('testuser', 'test@example.com', 'password')

# Create the first order — signal fires, UserStats is created
Order.objects.create(user=user, total=100.00)
stats = UserStats.objects.get(user=user)
print(stats.order_count)   # 1
print(stats.total_spent)   # 100.00

# Create the second order — signal fires, UserStats accumulates
Order.objects.create(user=user, total=50.50)
stats.refresh_from_db()
print(stats.order_count)   # 2
print(stats.total_spent)   # 150.50

# Demonstrate the bulk bypass trap
Order.objects.filter(user=user).update(status='shipped')
stats.refresh_from_db()
print(stats.order_count)   # still 2 — signal NOT fired by QuerySet.update()
print(stats.total_spent)   # still 150.50
```

---

## Project Structure

```
signal_project/
├── .env.example                      # Environment variable template
├── .gitignore
├── docker-compose.yml                # db + app services
├── Dockerfile                        # Python 3.11-slim image
├── entrypoint.sh                     # Auto-migrate then start server
├── manage.py
├── requirements.txt                  # Django 4.2.13 + psycopg2 2.9.9
├── README.md
│
├── signal_project/                   # Django project package
│   ├── __init__.py
│   ├── settings.py                   # Config from env vars
│   ├── urls.py                       # Root URL + /health/
│   └── wsgi.py
│
└── orders/                           # Business-logic application
    ├── __init__.py                   # default_app_config (spec required)
    ├── apps.py                       # AppConfig.ready() registers signal
    ├── models.py                     # Order, UserStats
    ├── signals.py                    # @receiver(post_save, sender=Order)
    ├── views.py                      # /health/ endpoint
    ├── urls.py
    ├── admin.py
    │
    ├── migrations/
    │   ├── __init__.py
    │   └── 0001_initial.py
    │
    └── tests/
        ├── __init__.py
        └── test_signals.py           # 8 tests covering Stage A behaviour
```

---

## Signal Architecture Explained

```
Order.objects.create(user=user, total=99.99)
            │
            │  Django ORM performs SQL INSERT
            │  Commits the row to the database
            │
      [post_save signal dispatched]
            │
            │  sender=Order, instance=<Order>, created=True
            │
  update_user_stats_on_order_save()
      orders/signals.py
            │
            ├── UserStats.objects.get_or_create(user=user)
            │         SELECT + optional INSERT
            │
            ├── stats.order_count += 1
            ├── stats.total_spent += instance.total
            │
            └── stats.save()
                      UPDATE orders_userstats SET ...
```

### How the signal is connected

`orders/__init__.py` declares `default_app_config = 'orders.apps.OrdersConfig'`.

Django loads `OrdersConfig` and calls its `ready()` method after the full
app registry is populated.  `ready()` executes `import orders.signals`,
which causes Python to evaluate the `@receiver(post_save, sender=Order)`
decorator, registering `update_user_stats_on_order_save` as a listener.

---

## Known Limitations Demonstrated by Tests

### Limitation 1 — Bulk operation bypass

`QuerySet.update()`, `QuerySet.delete()`, and `bulk_create()` all operate
at the SQL level.  They do not instantiate model objects, do not call
`.save()`, and therefore do not fire `pre_save` or `post_save`.

**Demonstrated by:** `test_bulk_update_bypasses_signal`

The test creates 3 orders for `other_user` via `bulk_create()`, then
reassigns them to `self.user` via `QuerySet.update()`.  Despite 3 new
orders now belonging to `self.user`, `UserStats.order_count` remains
unchanged because the signal was never dispatched.

### Limitation 2 — Test isolation requirement

Signal receivers are registered in memory for the lifetime of the
process.  Django's `TestCase` rolls back database transactions between
tests but does **not** reset in-memory signal connections.

A receiver that is still connected during a test that expects no
side-effects will cause false failures.

**Demonstrated by:** every `tearDown()` method in `test_signals.py`

Each `tearDown()` calls:

```python
post_save.disconnect(
    receiver=update_user_stats_on_order_save,
    sender=Order,
)
```

This ensures the receiver is not active for any subsequent test.

---

## Verification Commands Reference

```bash
# Build and start all containers
docker-compose up --build

# Check container health status
docker-compose ps

# Run Django system check
docker-compose exec app python manage.py check

# Show applied migrations
docker-compose exec app python manage.py showmigrations orders

# Run Stage A signal tests
docker-compose exec app python manage.py test orders.tests.test_signals

# Run the bulk bypass test specifically
docker-compose exec app python manage.py test \
  orders.tests.test_signals.OrderSignalTests.test_bulk_update_bypasses_signal

# Verify health endpoint
curl http://localhost:8000/health/

# Connect to PostgreSQL
docker-compose exec db psql -U postgres_user -d signal_project_db

# Stop all containers
docker-compose down

# Stop and remove volumes
docker-compose down -v
```