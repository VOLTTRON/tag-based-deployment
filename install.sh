#!/bin/bash
SCRIPT_DIR=$( dirname -- "$0"; )

if ! [[ -x $(command -v ansible-galaxy) ]]; then
  python3 -m pip install ansible
fi

ansible-galaxy collection install git+https://github.com/volttron/volttron-ansible.git,service_and_logging
ansible-galaxy collection install "$SCRIPT_DIR"/ansible_deployment/volttron-tag-based-deployment-collection

