# SSH to Ansible controller (pi-star)
cd ~/source/Home_Automation/Ansible/
# MAKE SURE SECRETS FILES ARE UPDATED !!!!!!!
git status && git pull && git pull && rm ~/.ssh/known_hosts && clear && ansible-playbook embyone_setup.yml
git status && git pull && git pull && rm ~/.ssh/known_hosts && clear && ansible-playbook embytwo_setup.yml
