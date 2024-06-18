# VOLTTRON Tag Based Deployment
This repository contains two components:

#### config_generators
This directory contains a set of tools for generating VOLTTRON configurations from Haystack tags.
For further information on the config generation tools,
see the [README.md](config_generators/README.md) in the config_generators directory.

#### ansible_deployment
This directory contains a set of Ansible scripts for deploying VOLTTRON instances using the generation tools.
A playbook (deploy-tags.yml) is provided which will do the following for each managed node:
1. Install dependencies for VOLTTRON.
2. Download the repositories for VOLTTRON, the volttron-interface for the Normal Framework,
and voltron-pnnl-applications.
3. Clone the config generators from this repository to the managed node.
4. Install and bootstrap VOLTTRON.
5. Install dependencies for the config generators.
6. Install Normal Frameworks VOLTTRON interface and VOLTTRON driver.
7. Set up rotating logs for VOLTTRON
8. Make VOLTTRON a system service.
9. Start VOLTTRON.
10. Build and install configurations for drivers using a tagging database or JSON file.
11. Build and install configurations for AirsideRCx agents using a tagging database or JSON file.
12. Install and set up autossh to provide a tunnel for the historian to reach its database.
13. Install, enable, and start driver, AirsideRCx, and historian agents.

The remainder of this file will detail how to install and use the Ansible scripts. In addition to the deploy-tags.yml
playbook, the ansible_deployment directory contains:
* An Ansible collection with playbooks and roles for tasks specific to deployment of VOLTTRON agents using tags. These are used by the
deploy-tags.yml playbook and should not need to be used separately if that playbook is being used.
  * Note that this collection expands on the separately distributed VOLTTRON Ansible collection (see documentation at [VOLTTRON-Ansible](https://volttron.readthedocs.io/projects/volttron-ansible/en/develop/)).
* An inventory file (hosts.yml).
* Several additional files for defining the variables which Ansible will use to set up each deployment.
* An installation script (install.sh) which can be used to set up the environment on a control node from which the
deployment scripts will be run.

## Installation
To set up Ansbile on the control node:
1. Clone this repository.
2. Run the install.sh file:
   ```shell
   sh install.sh
   ```
   This will install:
   1. Ansible
   2. The volttron-ansible collection.
   3. The tag-based deployment collection contained in ansible_deployment/volttron-tag-based-deployment-collection.

## Configurations
Before running the playbook, inventory files should be configured to define variables for the managed nodes which
will receive VOLTTRON deployments.  

The main inventory file is [hosts.yml](ansible_deployment/hosts.yml).
This contains information required to connect to each managed node.
One node (brookland) is configured in the default file, but more may be configured by adding an additional YAML
dictionary for each managed node. At the very least, these should each contain:
* ansible_host: the ip or hostname of the managed node>
* ansible_user: the username of the unix user to which ansible will log in, and from which VOLTTRON will be run.

Additionally, if keyed authentication is required, the public key should already be installed on the managed node and
the following item should be added to the dictionary:
* ansible_ssh_private_key_file: The path to a private key file which can be used to access the node.

Ansible will also require that variables be defined to define its behavior when setting up each deployment.
* Variables which will apply to all nodes may be defined in the vars section of hosts.yml.
* Variables shared by all nodes in a group (a collectors group is configured in the default hosts.yml file) may
  be defined in the group_vars directory in a YAML file with the same name as the group (e.g., collectors.yml).
* Variables which are specific to a single host may be defined in the host_vars directory in a YAML file with the same
  name as the host (e.g., brookland.yml).
If the same variable is defined in multiple of these files, the version in the most specific file will override any
alue in more general files (i.e., host_vars will override group_vars, and group_vars will override hosts.yml).

## Using the deploy-tags.yml playbook
Once configurations have been made, the deploy-tags.yml playbook can be run using the ansible-playbook command:
```shell
ansible-playbook -i hosts.yml deploy-tags.yml -K
```
The -i option tells Ansbile to use the inventory defined in hosts.yml. The -K option tells Ansible to ask for a
password which will be used to invoke sudo privilege on managed hosts.  If different sudo passwords are required,
these can be defined in the inventory instead.
