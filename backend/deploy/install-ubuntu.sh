#!/usr/bin/env bash
# Ubuntu 24.04 dedicated VM installer. Run only after the approved VM, network
# rules and instance-principal bucket policy exist. This script adds no apt repo.
#
# First upload the source tree, /etc/research-backend.env and the two wallet
# directories under /etc/research-backend/wallets. Never upload an OCI owner key.
# Then: sudo bash deploy/install-ubuntu.sh --public-ip <VM IPv4>
# Optional: --source-dir /path/to/research-backend
# Ports 80/443 must reach this VM for ACME/HTTPS; 8787 stays loopback-only.
# Official IP/webroot instructions:
# https://letsencrypt.org/2026/03/11/shorter-certs-certbot
# Certbot renewal defaults and deploy hooks:
# https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates
set -Eeuo pipefail
umask 022
trap 'printf "Installation stopped at line %s. No secret values were printed by this script.\n" "$LINENO" >&2' ERR

PUBLIC_IP=""
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
APP_DIR=/opt/research-backend
ENV_FILE=/etc/research-backend.env
WALLET_ROOT=/etc/research-backend/wallets
CERT_DIR=/etc/letsencrypt/live/research-backend-ip
SITE=/etc/nginx/sites-available/research-backend

while (($#)); do
    case "$1" in
        --public-ip) PUBLIC_IP="${2:?--public-ip requires a value}"; shift 2 ;;
        --source-dir) SOURCE_DIR="${2:?--source-dir requires a value}"; shift 2 ;;
        --help)
            printf 'Usage: sudo bash deploy/install-ubuntu.sh --public-ip IPV4 [--source-dir DIRECTORY]\n'
            exit 0 ;;
        *) printf 'Unknown installer option. Use --help.\n' >&2; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { printf 'Run this installer as root.\n' >&2; exit 2; }
[[ -n "$PUBLIC_IP" ]] || { printf '--public-ip is required.\n' >&2; exit 2; }
# /etc/os-release is a trusted OS-owned file, never a secret configuration file.
# shellcheck disable=SC1091
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || {
    printf 'This installer requires Ubuntu 24.04.\n' >&2; exit 2;
}
python3 - "$PUBLIC_IP" <<'PY'
import ipaddress, sys
address = ipaddress.ip_address(sys.argv[1])
if address.version != 4 or not address.is_global:
    raise SystemExit('A globally routable IPv4 address is required.')
PY
SOURCE_DIR="$(realpath -- "$SOURCE_DIR")"
[[ -f "$SOURCE_DIR/pyproject.toml" && -d "$SOURCE_DIR/src/research_backend" ]]
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && -d "$WALLET_ROOT" && ! -L "$WALLET_ROOT" ]] || {
    printf 'Upload the app-only environment and both wallet directories first.\n' >&2; exit 2;
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --no-install-recommends -y ca-certificates curl nginx openssl python3.12-venv

if ! getent passwd research >/dev/null; then
    useradd --system --user-group --home-dir /var/lib/research-backend --shell /usr/sbin/nologin research
fi
install -d -o research -g research -m 0700 /var/lib/research-backend
install -d -o root -g research -m 0750 /etc/research-backend "$WALLET_ROOT"
chown root:research "$ENV_FILE"
chmod 0640 "$ENV_FILE"
# Do not follow wallet symlinks or grant access outside the dedicated directory.
if find "$WALLET_ROOT" -type l -print -quit | grep -q .; then
    printf 'Wallet symlinks are not accepted.\n' >&2; exit 2
fi
find "$WALLET_ROOT" -type d -exec chown root:research {} + -exec chmod 0750 {} +
find "$WALLET_ROOT" -type f -exec chown root:research {} + -exec chmod 0640 {} +

install -d -o root -g root -m 0755 "$APP_DIR" "$APP_DIR/src"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
    install -o root -g root -m 0644 "$SOURCE_DIR/pyproject.toml" "$APP_DIR/pyproject.toml"
    cp -a -- "$SOURCE_DIR/src/." "$APP_DIR/src/"
fi
chown -R root:root "$APP_DIR/src"
find "$APP_DIR/src" -type d -exec chmod 0755 {} +
find "$APP_DIR/src" -type f -exec chmod 0644 {} +
python3.12 -m venv "$APP_DIR/.venv"
python3.12 - "$APP_DIR" <<'PY'
from pathlib import Path
import shutil, sys
root = Path(sys.argv[1]).resolve()
generated = root / 'build'
if generated.exists():
    if generated.resolve() != generated or not generated.is_dir():
        raise SystemExit('Unexpected package build path; retained without changes.')
    # This is generated setuptools output, never application runtime data.
    shutil.rmtree(generated)
PY
"$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
    --index-url https://pypi.org/simple "$APP_DIR[oci]"
python3.12 -m venv /opt/certbot
/opt/certbot/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
    --index-url https://pypi.org/simple 'certbot>=5.4,<6'

# Inspect configuration inside the server process; never source or print it.
"$APP_DIR/.venv/bin/python" - "$ENV_FILE" "$WALLET_ROOT" <<'PY'
from pathlib import Path
import sys
from dotenv import dotenv_values

def stop():
    raise SystemExit('Server app/instance-principal configuration is incomplete; values hidden.')

config = dotenv_values(sys.argv[1], interpolate=False)
if (config.get('BACKEND_DATABASE') != 'oracle' or config.get('BACKEND_BLOB_STORE') != 'oci'
        or config.get('BACKEND_DATA_DIR') != '/var/lib/research-backend'
        or str(config.get('OCI_USE_INSTANCE_PRINCIPAL', '')).lower() != 'true'
        or not config.get('OCI_NAMESPACE') or not config.get('OCI_BUCKET')):
    stop()
if any(config.get(key) for key in ('OCI_CONFIG_FILE', 'OCI_KEY_FILE', 'OCI_PRIVATE_KEY',
                                  'OCI_PRIVATE_KEY_PATH', 'OCI_FINGERPRINT', 'OCI_USER')):
    stop()
root = Path(sys.argv[2]).resolve()
tokens = []
for project in ('ESTATE', 'INVESTMENT'):
    if config.get(project + '_ORACLE_USER') != project + '_APP':
        stop()
    if not all(config.get(project + '_ORACLE_' + key) for key in ('PASSWORD', 'DSN', 'WALLET_DIR', 'WALLET_PASSWORD')):
        stop()
    wallet = Path(config[project + '_ORACLE_WALLET_DIR']).resolve()
    if not wallet.is_relative_to(root) or not wallet.is_dir():
        stop()
    if not (wallet / 'tnsnames.ora').is_file() or not (wallet / 'ewallet.pem').is_file():
        stop()
    token = config.get(project + '_API_TOKEN', '')
    if len(token) < 32 or token.startswith('replace-'):
        stop()
    tokens.append(token)
if len(set(tokens)) != 2:
    stop()
print('App-only Oracle and instance-principal configuration validated; values hidden.')
PY

# Reuse the existing hardened service template and override deployment-specific
# user/env paths without changing the source template or exposing the API port.
install -m 0644 "$SOURCE_DIR/deploy/research-backend.service" /etc/systemd/system/research-backend.service
install -d -m 0755 /etc/systemd/system/research-backend.service.d
cat > /etc/systemd/system/research-backend.service.d/deployment.conf <<'UNIT'
[Service]
User=research
Group=research
EnvironmentFile=
EnvironmentFile=/etc/research-backend.env
UNIT
install -m 0644 "$SOURCE_DIR/deploy/research-certbot-renew.service" /etc/systemd/system/research-certbot-renew.service
install -m 0644 "$SOURCE_DIR/deploy/research-certbot-renew.timer" /etc/systemd/system/research-certbot-renew.timer

install -d -m 0755 /var/lib/letsencrypt/.well-known/acme-challenge
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/research-nginx <<'HOOK'
#!/usr/bin/env sh
set -eu
/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/research-nginx

# Initial port 80 serves only ACME files. This config contains no proxy_pass.
cat > "$SITE" <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }
    location / { return 404; }
}
NGINX
# Disable only Ubuntu's default welcome site on this dedicated backend VM.
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    unlink /etc/nginx/sites-enabled/default
fi
ln -sfn "$SITE" /etc/nginx/sites-enabled/research-backend
nginx -t
systemctl enable --now nginx
systemctl reload nginx

# No email is supplied; this supported compatibility flag avoids an interactive
# email prompt. Certbot manages the ACME account key locally on the VM.
/opt/certbot/bin/certbot certonly --non-interactive --agree-tos \
    --register-unsafely-without-email --cert-name research-backend-ip \
    --preferred-profile shortlived --webroot --webroot-path /var/lib/letsencrypt \
    --ip-address "$PUBLIC_IP" --keep-until-expiring

# A staging/self-signed/wrong-IP/expired certificate never activates the proxy.
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt \
    -untrusted "$CERT_DIR/chain.pem" "$CERT_DIR/cert.pem"
openssl x509 -in "$CERT_DIR/cert.pem" -noout -checkip "$PUBLIC_IP"
openssl x509 -in "$CERT_DIR/cert.pem" -noout -checkend 7200

systemctl daemon-reload
systemctl enable --now research-backend.service
systemctl restart research-backend.service
curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 2 \
    --max-time 5 http://127.0.0.1:8787/healthz >/dev/null
runuser -u research -- "$APP_DIR/.venv/bin/python" - "$ENV_FILE" <<'PY'
import json, sys, urllib.request
from dotenv import dotenv_values
config = dotenv_values(sys.argv[1], interpolate=False)
for project in ('estate', 'investment'):
    request = urllib.request.Request('http://127.0.0.1:8787/v1/' + project + '/ready',
        headers={'Authorization': 'Bearer ' + config[project.upper() + '_API_TOKEN']})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = json.load(response)
        if status.get('database') != 'oracle' or status.get('blob_store') != 'oci':
            raise ValueError('unexpected storage')
    except Exception as error:
        print(project + ': readiness failed (' + type(error).__name__ + '); details hidden.', file=sys.stderr)
        raise SystemExit(1) from None
    print(project + ': Oracle/OCI API ready.')
PY
sed "s/@@PUBLIC_IP@@/$PUBLIC_IP/g" "$SOURCE_DIR/deploy/nginx-ip.conf.template" > "$SITE"
nginx -t
systemctl reload nginx
systemctl enable --now research-certbot-renew.timer

# Local TCP connection still validates the real public-IP certificate/SAN.
curl --fail --silent --show-error --connect-to "$PUBLIC_IP:443:127.0.0.1:443" \
    --max-time 15 "https://$PUBLIC_IP/healthz" >/dev/null
[[ "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1/healthz)" == 404 ]]
printf 'HTTPS backend installed at https://%s. HTTP serves ACME only; renewal checks run twice daily.\n' "$PUBLIC_IP"
