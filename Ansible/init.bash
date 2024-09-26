# SSH to Ansible controller (pi-star)
rm ~/.ssh/known_hosts
cd ~/source/Home_Automation/Ansible/
# MAKE SURE SECRETS FILES ARE UPDATED !!!!!!!
git pull && git pull && clear && ansible-playbook core_setup.yml
