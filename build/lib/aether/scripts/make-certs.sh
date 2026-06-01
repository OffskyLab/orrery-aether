#!/bin/sh
# Generate a self-signed CA + Redis server cert for cross-machine TLS.
#
#   Usage: aether/scripts/make-certs.sh [BUS_IP_OR_HOST]   (default: 127.0.0.1)
#
# Output → aether/certs/{ca.crt,ca.key,redis.crt,redis.key}. docker-compose mounts
# ./certs read-only into the redis container and auto-enables TLS on :6380 when
# redis.crt is present. Distribute ca.crt to client machines and point
# --redis-tls-ca / AETHER_REDIS_TLS_CA at it.
#
# IMPORTANT: TLS over an IP needs the IP in the cert SAN, or clients using
# `--redis-host <ip>` fail verification — this script puts it there.
set -e
HOST="${1:-127.0.0.1}"
DIR="$(cd "$(dirname "$0")/.." && pwd)/certs"
mkdir -p "$DIR"
cd "$DIR"

# SAN: numeric → IP:, else DNS:. Always also allow localhost + 127.0.0.1.
case "$HOST" in
  *[!0-9.]*) SAN="DNS:$HOST,DNS:localhost,IP:127.0.0.1" ;;
  *)         SAN="IP:$HOST,DNS:localhost,IP:127.0.0.1" ;;
esac

# CA — long-lived (rotating it re-keys the whole fleet).
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/CN=Aether-CA" -out ca.crt

# Server cert — shorter-lived, with SAN, signed by the CA.
openssl genrsa -out redis.key 4096 2>/dev/null
openssl req -new -key redis.key -subj "/CN=$HOST" -out redis.csr
openssl x509 -req -in redis.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -sha256 -extfile /dev/stdin -out redis.crt <<EOF
subjectAltName=$SAN
EOF
rm -f redis.csr ca.srl
chmod 600 ./*.key

echo "✓ certs written to $DIR (SAN=$SAN)"
echo "  CA   expires: $(openssl x509 -enddate -noout -in ca.crt | cut -d= -f2)"
echo "  cert expires: $(openssl x509 -enddate -noout -in redis.crt | cut -d= -f2)"
echo "  ⚠ when the server cert expires, ALL clients fail at once — rotate before then."
echo "  Next: docker compose -f aether/docker-compose.yml up -d  (TLS auto-enables on :6380)"
