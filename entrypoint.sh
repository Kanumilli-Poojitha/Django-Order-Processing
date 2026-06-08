#!/bin/sh
set -e

echo "==> Running database migrations..."
python manage.py migrate --noinput

echo "==> Migrations complete. Starting Django development server..."
exec "$@"