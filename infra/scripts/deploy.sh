#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="$SCRIPT_DIR/../ansible"

export ANSIBLE_CONFIG="$ANSIBLE_DIR/ansible.cfg"

VERIFY_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --verify-only)
            VERIFY_ONLY=true
            shift
            ;;
        *)
            echo "Usage: $0 [--verify-only]"
            exit 1
            ;;
    esac
done

if [ "$VERIFY_ONLY" = true ]; then
    echo "=== Running verification only ==="
    ansible-playbook "$ANSIBLE_DIR/playbooks/02-verify.yml"
else
    echo "=== Deploying binance-trader ==="
    ansible-playbook "$ANSIBLE_DIR/playbooks/01-deploy.yml"

    echo ""
    echo "=== Verifying deployment ==="
    ansible-playbook "$ANSIBLE_DIR/playbooks/02-verify.yml"
fi
