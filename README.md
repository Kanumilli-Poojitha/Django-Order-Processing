# Mastering Django: Signals to Service Layer

A Django application that demonstrates the complete architectural journey
from a signal-based order management system to a production-grade service
layer. The project exists in its **final Stage B state**: the service layer
is the live implementation, signals have been removed from the application
startup path, and both the historical signal proof and the current service
layer are covered by a full test suite.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Current Architecture — Stage B Service Layer](#current-architecture--stage-b-service-layer)
3. [Why Signals Were Removed](#why-signals-were-removed)
4. [How the Service Layer Works](#how-the-service-layer-works)
5. [Prerequisites](#prerequisites)
6. [Quick Start with Docker](#quick-start-with-docker)
7. [Environment Variables](#environment-variables)
8. [Running Tests](#running-tests)
9. [Running the Benchmark](#running-the-benchmark)
10. [Manual Shell Verification](#manual-shell-verification)
11. [Project Structure](#project-structure)
12. [Data Models](#data-models)
13. [Key Concepts](#key-concepts)
14. [Verification Commands Reference](#verification-commands-reference)

---

## Project Overview

This project manages `Order` records and keeps per-user aggregate statistics
(`UserStats`) in sync. It was built in two phases to teach the trade-offs
between Django's signal system and an explicit service layer.

**Stage A (historical — preserved as evidence)**
A `post_save` receiver in `orders/signals.py` was connected via
`OrdersConfig.ready()` and fired automatically after every
`Order.objects.create()` call. Tests in `orders/tests/test_signals.py`
prove both the correct behaviour and the two critical failure modes:

- `QuerySet.update()` issues raw SQL and never calls `.save()`, so
  `post_save` never fires — `UserStats` silently drifts out of sync.
- Signal receivers are global in-memory state; tests must explicitly
  disconnect them in `tearDown()` to prevent cross-test contamination.

**Stage B (current — the running application)**
All order-creation business logic lives in `orders/services.create_order()`,
wrapped in `transaction.atomic()`. The `@receiver` decorator has been
removed from `signals.py` and `apps.py` no longer imports the signals
module. `Order.objects.create()` called directly has no side-effects on
`UserStats`; the only way to create an order and update stats is to call
the service function.

---

## Current Architecture — Stage B Service Layer

```
Caller
  │
  ▼
services.create_order(user, total)          ← only authorised entry point
  │
  ├── transaction.atomic()
  │     │
  │     ├── Order.objects.create(user=user, total=Decimal(str(total)))
  │     │         INSERT INTO orders_order ...
  │     │
  │     ├── UserStats.objects.get_or_create(user=user)
  │     │         SELECT / INSERT INTO orders_userstats ...
  │     │
  │     ├── stats.order_count += 1
  │     ├── stats.total_spent += order.total
  │     └── stats.save()
  │               UPDATE orders_userstats SET ...
  │
  │   ── if any step raises → full rollback, no partial writes ──
  │
  └── return order
```

### What `apps.py` does now

`OrdersConfig.ready()` contains only `pass`. The signal import that was
present in Stage A has been removed. Django's app registry calls `ready()`
at startup but no receivers are connected as a result.

```python
# orders/apps.py — current state
class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'orders'

    def ready(self):
        pass   # No signal registration — business logic lives in services.py
```

### What `signals.py` contains now

The file is retained as historical documentation and to support
`test_signals.py`. The function `update_user_stats_on_order_save` exists
but carries **no `@receiver` decorator** and is **never imported at
application startup**. It is effectively dead code from the runtime's
perspective — present only so that signal tests can import it by name for
manual `post_save.connect()` / `post_save.disconnect()` calls within their
`setUp()` and `tearDown()` methods.

---

## Why Signals Were Removed

The Stage A signal implementation had three fundamental problems that made
it unsuitable for production business logic:

### 1 — Bulk operation bypass

`QuerySet.update()`, `QuerySet.delete()`, and `bulk_create()` all translate
to single SQL statements. Django never instantiates individual model objects
for these operations, never calls `.save()`, and therefore never dispatches
`pre_save` or `post_save`. Any code that depends on those signals is silently
skipped whenever a bulk operation is used anywhere in the codebase.

This is demonstrated conclusively by `test_bulk_update_bypasses_signal` in
`orders/tests/test_signals.py`: after reassigning orders between users via
`QuerySet.update()`, `UserStats` remains unchanged despite the database now
reflecting the new ownership.

### 2 — No transactional atomicity

The `post_save` signal fires **after** the `Order` INSERT has already
committed to the database. If the receiver raises an exception — a database
constraint violation, a network timeout, any unhandled error — the `Order`
row persists but `UserStats` is never updated. The two tables are now
inconsistent, and there is no automatic recovery path.

### 3 — Hidden control flow

A developer reading `Order.objects.create(user=u, total=t)` sees a single
model insert. They have no indication that a second table (`UserStats`) is
being modified as a side-effect triggered by a receiver defined in a
completely separate file. This "action at a distance" makes the codebase
hard to understand, debug, and safely refactor.

---

## How the Service Layer Works

### `create_order(user, total)` — `orders/services.py`

```python
def create_order(user: User, total: float) -> Order:
    with transaction.atomic():
        order = Order.objects.create(user=user, total=Decimal(str(total)))

        stats, _ = UserStats.objects.get_or_create(user=user)
        stats.order_count += 1
        stats.total_spent += order.total
        stats.save()

    return order
```

**`transaction.atomic()`**
Both the `Order` INSERT and the `UserStats` UPDATE are wrapped in a single
database transaction. If `stats.save()` raises any exception, the entire
transaction is rolled back — the `Order` INSERT is undone and no partial
write survives. The database is always left in a consistent state.

**`Decimal(str(total))`**
The `total` argument accepts `int`, `float`, or `str`. Converting via
`str()` before `Decimal()` avoids the floating-point representation errors
that arise when passing a Python `float` directly to `Decimal()`.
For example: `Decimal(0.1)` yields `0.1000000000000000055511151231257827021181583404541015625`,
whereas `Decimal('0.1')` yields exactly `0.1`.

**`get_or_create`**
On a user's first order, `get_or_create` inserts a zeroed-out `UserStats`
row. On every subsequent order it retrieves the existing row. This means
`create_order` is always safe to call regardless of whether the user has
placed an order before.

**Return value**
The function returns the newly created `Order` instance so callers can
inspect its `pk`, `status`, or any other field without an extra query.

---

## Prerequisites

|      Tool      |  Minimum Version   |
|----------------|--------------------|
| Docker         |        24          |
| Docker Compose |      2.20          |
| git            | any recent version |

No local Python installation is required to run or test the project —
everything executes inside Docker against a PostgreSQL database.

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

The default values in `.env.example` work for local development without
modification. Edit `.env` only if you need non-default credentials:

```
SECRET_KEY=replace-with-a-real-secret-key
DEBUG=True
POSTGRES_DB=signal_project_db
POSTGRES_USER=postgres_user
POSTGRES_PASSWORD=strongpassword
DB_HOST=db
DB_PORT=5432
```

### 3. Build and start the containers

```bash
docker-compose up --build
```

The startup sequence is orchestrated as follows:

1. The `db` container starts. PostgreSQL initialises and `pg_isready`
   is polled every 5 seconds until it returns success. The `db` service
   is then marked **healthy**.
2. The `app` container starts only after `db` is healthy (`depends_on:
   condition: service_healthy`).
3. `entrypoint.sh` runs. It polls `python manage.py check --database
   default` in a loop until Django can reach the database, then executes
   `python manage.py migrate --noinput` automatically.
4. The Django development server starts on `0.0.0.0:8000`.
5. The `app` healthcheck (`curl -f http://localhost:8000/health/`) polls
   every 10 seconds. Once it returns HTTP 200, the `app` service is marked
   **healthy**.

Both containers reach **healthy** status within approximately 30–60 seconds.

### 4. Verify both containers are healthy

```bash
docker-compose ps
```

Expected output:

```
NAME                       STATUS
signal_project-db-1        running (healthy)
signal_project-app-1       running (healthy)
```

### 5. Verify migrations were applied

```bash
docker-compose exec app python manage.py showmigrations orders
```

Expected output:

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

All variables are documented in `.env.example`. None contain real secrets.

| Variable | Description | Default / Example |
|----------|-------------|-------------------|
| `SECRET_KEY` | Django cryptographic secret key | `your-secret-key-here` |
| `DEBUG` | Enable Django debug mode (`True`/`False`) | `True` |
| `POSTGRES_DB` | PostgreSQL database name | `signal_project_db` |
| `POSTGRES_USER` | PostgreSQL username | `postgres_user` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `your-strong-password-here` |
| `DB_HOST` | Database host — use the Docker Compose service name in containers | `db` |
| `DB_PORT` | PostgreSQL port | `5432` |

**Never commit your real `.env` file.** It is listed in `.gitignore`.

---

## Running Tests

All tests run inside the Docker container against the live PostgreSQL
database. There is no SQLite fallback.

### Run the full test suite (both Stage A and Stage B)

```bash
docker-compose exec app python manage.py test orders
```

### Run only the service layer tests (Stage B — current implementation)

```bash
docker-compose exec app python manage.py test orders.tests.test_services
```

### Run only the historical signal tests (Stage A — preserved as evidence)

```bash
docker-compose exec app python manage.py test orders.tests.test_signals
```

> **Note on signal tests:** Because `apps.py` no longer registers the
> signal receiver, `test_signals.py` manually calls
> `post_save.connect(receiver=update_user_stats_on_order_save, sender=Order)`
> in each test class's `setUp()` method. This makes the signal tests
> self-contained and runnable alongside the service tests in a single
> `manage.py test orders` invocation. Each class's `tearDown()` calls
> `post_save.disconnect(receiver=update_user_stats_on_order_save, sender=Order)`
> to restore the disconnected state before the next test class runs.

### Run a single test method

```bash
# Prove that QuerySet.update() bypasses post_save (Stage A evidence)
docker-compose exec app python manage.py test \
  orders.tests.test_signals.OrderSignalTests.test_bulk_update_bypasses_signal

# Prove that direct Order.objects.create() does not update UserStats (Stage B proof)
docker-compose exec app python manage.py test \
  orders.tests.test_services.ServiceLayerTests.test_direct_create_does_not_update_stats

# Prove transaction.atomic() rolls back the Order INSERT on failure
docker-compose exec app python manage.py test \
  orders.tests.test_services.ServiceLayerTests.test_atomic_rollback_on_userstats_failure
```

### Test coverage summary

| Test file | Class | What it proves |
|-----------|-------|----------------|
| `test_signals.py` | `OrderSignalTests` | Signal creates `UserStats` on `Order.objects.create()` when manually connected; `QuerySet.update()` bypasses it; `tearDown()` disconnects cleanly |
| `test_signals.py` | `SignalIsolationDemonstrationTests` | Connect/disconnect pattern is idempotent across test classes |
| `test_services.py` | `ServiceLayerTests` | `create_order()` creates `Order`; creates/updates `UserStats`; returns `Order`; `transaction.atomic()` rolls back on failure; prior successful calls are unaffected by a later rollback; direct `Order.objects.create()` does NOT touch `UserStats` |
| `test_services.py` | `MultiUserServiceTests` | Stats are tracked independently per user |

### Expected output

```
Found 14 test(s).
..............
----------------------------------------------------------------------
Ran 14 tests in X.XXXs

OK
```

---

## Running the Benchmark

The `benchmark_updates` management command quantifies the performance
difference between the N+1 query pattern of the old signal approach and
the optimised bulk approach that the service layer enables.

### What it measures

**Signal simulation** (`_run_signal_simulation`):
Reproduces the implicit behaviour of the Stage A `post_save` receiver.
For each of 1 000 iterations it calls `Order.objects.create()`, then
calls `stats.refresh_from_db()`, increments both counters, and calls
`stats.save()`. Total database round-trips: approximately 3 × 1 000 = 3 000.

**Optimised service** (`_run_optimised_service`):
Uses `Order.objects.bulk_create()` to insert all 1 000 rows in one SQL
statement, then applies the aggregate delta with a single
`UserStats.objects.filter(...).update(order_count=F('order_count') + 1000, ...)`
using `F()` expressions. Total database round-trips: **2**, regardless of
how many orders are created.

### Run the benchmark

```bash
docker-compose exec app python manage.py benchmark_updates
```

### Expected output format

```
Starting benchmark: 1000 orders per approach.
Signal approach time: 3.142s
Optimized service time: 0.031s
Speedup factor: 101.355x
```

The exact figures vary by hardware. The optimised approach is typically
**50–200× faster** because it collapses ~3 000 sequential database
round-trips into 2.

### View command help

```bash
docker-compose exec app python manage.py benchmark_updates --help
```

---

## Manual Shell Verification

Open a Django shell inside the running container:

```bash
docker-compose exec app python manage.py shell
```

### Verify the service layer creates Order and UserStats atomically

```python
from django.contrib.auth.models import User
from orders.models import Order, UserStats
from orders import services

# Create a user
user = User.objects.create_user('demo', 'demo@example.com', 'demopass')

# Use the service layer — the only correct way to create an order
order = services.create_order(user=user, total=99.99)

print(order.pk)        # e.g. 1
print(order.status)    # pending
print(order.total)     # 99.99

stats = UserStats.objects.get(user=user)
print(stats.order_count)   # 1
print(stats.total_spent)   # 99.99

# A second order accumulates correctly
services.create_order(user=user, total=50.01)
stats.refresh_from_db()
print(stats.order_count)   # 2
print(stats.total_spent)   # 150.00
```

### Verify that direct Order.objects.create() does NOT update UserStats

```python
# This proves the signal is disconnected — no automatic side-effects
Order.objects.create(user=user, total=25.00)
stats.refresh_from_db()
print(stats.order_count)   # still 2 — UserStats unchanged
print(stats.total_spent)   # still 150.00
```

### Verify the database tables directly

```bash
docker-compose exec db psql -U postgres_user -d signal_project_db \
  -c "\dt orders_*"
```

Expected:

```
            List of relations
 Schema |       Name        | Type  |    Owner
--------+-------------------+-------+--------------
 public | orders_order      | table | postgres_user
 public | orders_userstats  | table | postgres_user
```

---

## Project Structure

```
signal_project/
├── .env.example                            # Environment variable template (no real secrets)
├── .gitignore                              # Excludes .env, __pycache__, etc.
├── docker-compose.yml                      # Orchestrates db (PostgreSQL) + app (Django)
├── Dockerfile                              # python:3.11-slim + curl + psycopg2
├── entrypoint.sh                           # DB readiness loop → migrate → exec server
├── manage.py
├── requirements.txt                        # Django==4.2.13, psycopg2==2.9.9
├── README.md
│
├── signal_project/                         # Django project package
│   ├── __init__.py
│   ├── settings.py                         # All config read from environment variables
│   ├── urls.py                             # Mounts orders.urls (includes /health/)
│   └── wsgi.py
│
└── orders/                                 # Business-logic application
    ├── __init__.py                         # default_app_config = 'orders.apps.OrdersConfig'
    ├── apps.py                             # OrdersConfig.ready() — contains only pass
    ├── models.py                           # Order, UserStats
    ├── signals.py                          # Historical Stage A receiver (disconnected, no @receiver)
    ├── services.py                         # ★ create_order(user, total) — live business logic
    ├── views.py                            # health_check view → GET /health/ returns HTTP 200
    ├── urls.py                             # path('health/', views.health_check)
    ├── admin.py                            # Registers Order and UserStats in Django admin
    │
    ├── migrations/
    │   ├── __init__.py
    │   └── 0001_initial.py                 # Creates orders_order and orders_userstats tables
    │
    ├── management/
    │   └── commands/
    │       └── benchmark_updates.py        # python manage.py benchmark_updates
    │
    └── tests/
        ├── __init__.py
        ├── test_signals.py                 # Stage A historical proof (manually connects signal)
        └── test_services.py                # Stage B live proof (no signal involvement)
```

---

## Data Models

### `Order`

| Field    | Type               | Details                     |
|----------|--------------------|-----------------------------|
| `id`     | `BigAutoField`     | Primary key, auto-generated |
| `user`   | `ForeignKey(User)` | `on_delete=CASCADE`, `related_name='orders'` |

| `total`  | `DecimalField`     | `max_digits=10`, `decimal_places=2` |

| `status` | `CharField`        | `max_length=20`, `default='pending'` |

### `UserStats`

| Field | Type | Details |
|-------|------|---------|
| `id` | `BigAutoField` | Primary key, auto-generated |
| `user` | `OneToOneField(User)` | `on_delete=CASCADE`, `related_name='stats'` |
| `total_spent` | `DecimalField` | `max_digits=12`, `decimal_places=2`, `default=0` |
| `order_count` | `IntegerField` | `default=0` |

`UserStats` is a denormalised aggregate record. Keeping these counters on
a dedicated row means a user dashboard query is a single indexed lookup
(`SELECT * FROM orders_userstats WHERE user_id = ?`) rather than a
potentially expensive `COUNT` + `SUM` aggregation over the full orders
table.

---

## Key Concepts

### Why signals fail for core business logic

| Failure mode | Explanation |
|--------------|-------------|
| **Bulk bypass** | `QuerySet.update()`, `bulk_create()`, and `QuerySet.delete()` issue raw SQL. Django never calls `.save()` per instance, so `pre_save` and `post_save` never fire. Stats drift silently. |
| **No atomicity** | `post_save` fires after the INSERT commits. If the receiver fails, the `Order` row exists but `UserStats` is not updated — two tables now permanently inconsistent. |
| **Hidden control flow** | `Order.objects.create()` appears to do one thing. The stats update is an invisible side-effect in a separate file, making the codebase hard to reason about, debug, and refactor safely. |
| **Test isolation cost** | Signal receivers are global in-memory state. Tests that rely on them must explicitly call `post_save.disconnect()` in `tearDown()` or risk contaminating unrelated test cases. |

### Why the service layer solves all four problems

| Benefit | How `create_order()` delivers it |
|---------|----------------------------------|
| **No bulk bypass** | The only way to trigger the business process is to call `create_order()`. `bulk_create()` and `QuerySet.update()` can be used freely elsewhere with no risk of skipping accounting logic. |
| **Full atomicity** | `transaction.atomic()` wraps both the `Order` INSERT and the `UserStats` UPDATE. Any exception causes a complete rollback — no partial writes survive. |
| **Explicit control flow** | A developer reading a call to `create_order()` immediately understands that an order and its stats are created together. No hidden receivers, no action at a distance. |
| **Simple testing** | No signal state to manage. Call the function, assert the outcomes. No `connect()`/`disconnect()` ceremony in `setUp()`/`tearDown()`. |

### When signals ARE the right tool

Signals are appropriate for **non-critical, decoupled side-effects** where
a failure is recoverable and does not corrupt core business data:

- Invalidating a cache entry after a model save
- Sending a notification to an external service
- Updating a search index (Elasticsearch, Solr, Typesense)
- Logging audit events to a secondary store

The guiding principle: **if a signal failure would leave your primary
database in an inconsistent state, use a service layer instead.**

---

## Verification Commands Reference

```bash
# ── Docker ────────────────────────────────────────────────────────────
# Build and start all containers
docker-compose up --build

# Start without rebuilding
docker-compose up

# Check container health status
docker-compose ps

# Stop containers (preserve volumes)
docker-compose down

# Stop containers and remove the PostgreSQL data volume
docker-compose down -v

# ── Django checks ────────────────────────────────────────────────────
# System check (no database connection required)
docker-compose exec app python manage.py check

# Show applied migrations
docker-compose exec app python manage.py showmigrations orders

# Run migrations manually (also runs automatically on startup)
docker-compose exec app python manage.py migrate

# ── Tests ─────────────────────────────────────────────────────────────
# Full test suite
docker-compose exec app python manage.py test orders

# Service layer tests only (Stage B)
docker-compose exec app python manage.py test orders.tests.test_services

# Signal tests only (Stage A historical evidence)
docker-compose exec app python manage.py test orders.tests.test_signals

# ── Benchmark ────────────────────────────────────────────────────────
# Run performance comparison
docker-compose exec app python manage.py benchmark_updates

# View benchmark command help
docker-compose exec app python manage.py benchmark_updates --help

# ── Database ──────────────────────────────────────────────────────────
# Open a psql shell
docker-compose exec db psql -U postgres_user -d signal_project_db

# List orders tables
docker-compose exec db psql -U postgres_user -d signal_project_db \
  -c "\dt orders_*"

# ── Health endpoint ───────────────────────────────────────────────────
curl http://localhost:8000/health/
# Expected: {"status": "ok"}
```