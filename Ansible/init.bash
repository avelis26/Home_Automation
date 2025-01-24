# SSH to Ansible controller (pi-star)
cd ~/source/Home_Automation/Ansible/
# MAKE SURE SECRETS FILES ARE UPDATED !!!!!!!
cd ~/source/Home_Automation/Ansible/ && git status && git pull && git pull && rm ~/.ssh/known_hosts && clear && ansible-playbook embyone_setup.yml
cd ~/source/Home_Automation/Ansible/ && git status && git pull && git pull && rm ~/.ssh/known_hosts && clear && ansible-playbook embytwo_setup.yml
cd ~/source/Home_Automation/Ansible/ && git status && git pull && git pull && rm ~/.ssh/known_hosts && clear && ansible-playbook embyone_setup.yml && ansible-playbook embytwo_setup.yml
