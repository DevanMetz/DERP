#!/bin/bash
set -e
python manage.py migrate --noinput
python manage.py seed_chart_of_accounts
python manage.py collectstatic --noinput
exec gunicorn config.wsgi --log-file -
