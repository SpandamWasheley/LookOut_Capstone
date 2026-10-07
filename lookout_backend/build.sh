#!/usr/bin/env bash
#
# Render BUILD COMMAND. Everything that must happen before the web process
# starts serving, in the order it has to happen.
#
# This is the build command, not a Procfile release phase: Render does not run
# Heroku's `release:` line at all. A Procfile sitting in the repo with a
# `release: python manage.py migrate` in it looks like it covers migrations and
# silently never runs -- which is exactly how a deploy reaches its first login
# and answers "no such table: core_user".
#
# Render's root directory for this service is lookout_backend/ (see
# render.yaml), so manage.py is beside this script.

set -o errexit    # stop at the first failure: a half-built deploy that still
                  # starts is worse than one that visibly fails, because Render
                  # keeps serving the PREVIOUS deploy when a build fails
set -o nounset    # an unset variable is a bug, not an empty string
set -o pipefail   # a failure anywhere in a pipeline fails the pipeline

echo "--> Installing dependencies"
pip install -r requirements.txt

# The CV stack, because this deployment runs the detectors itself (see
# DETECTION_ENABLED in render.yaml) rather than leaving them to a PC on site.
# torch alone is several hundred MB, so this dominates build time; Render
# caches it between deploys as long as the file does not change.
#
# Skipped when the service is API-only, which is the cheaper topology: set
# INSTALL_DETECTION=false and DETECTION_ENABLED=False together.
if [ "${INSTALL_DETECTION:-true}" = "true" ]; then
  echo "--> Installing the detection stack (torch, ultralytics)"
  # The -server file, NOT requirements-detection.txt: that one pulls
  # opencv-python, which needs libGL.so.1 (absent here) and collides with the
  # headless build requirements.txt already installed.
  pip install -r requirements-detection-server.txt
fi

echo "--> Collecting static files"
# WhiteNoise serves from STATIC_ROOT; without this the Django admin and the
# browsable API render unstyled.
python manage.py collectstatic --noinput

echo "--> Applying migrations"
python manage.py migrate --noinput

echo "--> Seeding the rows the system cannot run without"
# The four violation types, the camera and the settings row. Every one of them
# is otherwise created by a get_or_create inside a watch_* command, and those
# run only on the PC beside the camera -- so a hosted database never sees them.
# The failure is quiet: the dashboard loads with empty violation filters and an
# incoming alert has no type to attach to. Idempotent.
python manage.py seed_core

echo "--> Creating the first admin, if ADMIN_USERNAME/ADMIN_PASSWORD are set"
# Reads ADMIN_USERNAME, ADMIN_PASSWORD and ADMIN_EMAIL from the environment and
# creates a superuser (is_staff + is_superuser + role=admin) with
# must_change_password set, so the bootstrap password cannot survive as the
# real one. Render's free tier has no shell, so this is the only way in on an
# empty database.
#
# Safe on every deploy: it skips when the variables are unset, and skips again
# when the user already exists. It DOES fail the build if an admin exists under
# a different name -- deliberate, so a stale variable cannot quietly add a
# second way in. Clear the three variables once you have logged in.
python manage.py bootstrap_admin

echo "--> Build complete"
