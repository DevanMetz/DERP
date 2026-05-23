#!/bin/bash
set -e
python manage.py migrate_schemas --shared --noinput
python manage.py createcachetable
python manage.py create_public_tenant
python manage.py collectstatic --noinput
exec gunicorn config.wsgi --bind "0.0.0.0:${PORT:-8000}" --log-file -
