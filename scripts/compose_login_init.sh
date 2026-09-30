#!/bin/sh
# Compose login init (.docker/docker-compose.yml, service login-init): the
# read-only RustFS user a person logs in to the web front as, and the key the
# results proxy seals login sessions with. The devstack chart's init hook
# (charts/htrflow-devstack/templates/rustfs.yaml) creates the same user with
# the same client.
#
# Env: S3_ENDPOINT, S3_BUCKET, AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (the
# RustFS root keys), LOGIN_USER, LOGIN_PASSWORD, SESSION_KEY_OWNER (uid:gid
# of the web image, the only reader of the key). Idempotent: `rc admin user
# add` on an existing user sets its secret key, and an existing session key
# is kept, so a restart does not log everyone out.
set -eu

rc alias set local "$S3_ENDPOINT" "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" >/dev/null

# GetObject on the results bucket and nothing else: no listing, no write, no
# other bucket.
printf '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject"],"Resource":["arn:aws:s3:::%s/*"]}]}' \
  "$S3_BUCKET" >/tmp/read-policy.json
rc admin policy create local htr-read /tmp/read-policy.json
rc admin user add local "$LOGIN_USER" "$LOGIN_PASSWORD"
rc admin policy attach local htr-read --user "$LOGIN_USER"

key=/session/key
if [ ! -s "$key" ]; then
  umask 077
  head -c 32 /dev/urandom | base64 >"$key.tmp"
  mv "$key.tmp" "$key"
fi
chown "$SESSION_KEY_OWNER" "$key"
chmod 0400 "$key"
echo "login init complete: log in as $LOGIN_USER"
