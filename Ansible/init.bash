# SSH to Ansible controller (pi-star)
rm ~/.ssh/known_hosts
ssh-copy-id avelis@embyone
cd ~/source/Home_Automation/Ansible/
# MAKE SURE SECRETS FILES ARE UPDATED !!!!!!!
ansible-playbook core_setup.yml


    # VISUDO
    - name: Update VISUDO
      lineinfile:
        path: /etc/sudoers.d/avelis
        line: "avelis ALL=(ALL) NOPASSWD: ALL"
        create: yes
        mode: '0440'