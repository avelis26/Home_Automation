# SSH to Ansible controller (pi-star)
rm ~/.ssh/known_hosts
cd ~/source/Home_Automation/Ansible/
# MAKE SURE SECRETS FILES ARE UPDATED !!!!!!!
git pull && git pull && rm ~/.ssh/known_hosts && clear && ansible-playbook embyone_setup.yml
