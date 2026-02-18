#!/bin/sh
set -e

# Start local SOCKS5 proxy on port 1082
microsocks -p 1082 &

# Reverse-forward server's port 1081 → this container's microsocks on 1082
exec autossh -M 0 -N \
    -R 0.0.0.0:1081:127.0.0.1:1082 \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/root/.ssh/known_hosts \
    -i /root/.ssh/id_rsa \
    "$@"
