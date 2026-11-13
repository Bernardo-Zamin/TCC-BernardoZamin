#!/usr/bin/env bash
# Egress allowlist, applied on the host. The agent's tools are simulated, so this is
# an extra layer: traffic can only go to the DeepInfra and Anthropic APIs. Anything
# else is dropped and logged with the prefix TCC-BLOCK.
#
# Usage (as root, after the container has run once to create the network):
#   sudo CONTAINER_SUBNET=<subnet> ./nftables_allowlist.sh up
#   sudo ./nftables_allowlist.sh down
#   sudo ./nftables_allowlist.sh status

set -euo pipefail

TABLE="tcc_egress"
# Always pass the real subnet: the default covers every Docker network on the
# machine, including those of other projects. To find it:
#   docker network inspect docker_isolated -f '{{(index .IPAM.Config 0).Subnet}}'
CONTAINER_SUBNET="${CONTAINER_SUBNET:-172.16.0.0/12}"

# Fixed block published by Anthropic: https://platform.claude.com/docs/en/api/ip-addresses
ANTHROPIC_CIDR="160.79.104.0/23"

resolve_deepinfra() {
  getent ahostsv4 api.deepinfra.com | awk '{print $1}' | sort -u
}

up() {
  local ips
  ips="$(resolve_deepinfra)"
  if [ -z "$ips" ]; then
    echo "Could not resolve api.deepinfra.com. Check DNS." >&2
    exit 1
  fi

  nft delete table inet "$TABLE" 2>/dev/null || true
  nft add table inet "$TABLE"
  nft add chain inet "$TABLE" forward '{ type filter hook forward priority 0; policy accept; }'

  # Allowed destinations. `interval` accepts the /23 block with single addresses.
  nft add set inet "$TABLE" allowed_dst '{ type ipv4_addr; flags interval; }'
  for ip in $ips; do
    nft add element inet "$TABLE" allowed_dst "{ $ip }"
    echo "allowed: $ip (api.deepinfra.com)"
  done
  nft add element inet "$TABLE" allowed_dst "{ $ANTHROPIC_CIDR }"
  echo "allowed: $ANTHROPIC_CIDR (api.anthropic.com)"

  # DNS.
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" udp dport 53 accept
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" tcp dport 53 accept
  # HTTPS only to the allowed destinations.
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" ip daddr @allowed_dst tcp dport 443 accept
  # Private networks and the metadata address.
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" ip daddr 10.0.0.0/8 log prefix \"TCC-BLOCK-LAN \" drop
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" ip daddr 192.168.0.0/16 log prefix \"TCC-BLOCK-LAN \" drop
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" ip daddr 169.254.169.254 log prefix \"TCC-BLOCK-META \" drop
  # Everything else.
  nft add rule inet "$TABLE" forward ip saddr "$CONTAINER_SUBNET" log prefix \"TCC-BLOCK-EGRESS \" drop

  echo "Allowlist applied. Container subnet: $CONTAINER_SUBNET"
  echo "See blocked traffic with: journalctl -k | grep TCC-BLOCK"
}

down() {
  nft delete table inet "$TABLE" 2>/dev/null || true
  echo "Allowlist removed."
}

status() {
  nft list table inet "$TABLE" 2>/dev/null || echo "Table $TABLE is not active."
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) status ;;
  *) echo "usage: $0 {up|down|status}"; exit 1 ;;
esac
