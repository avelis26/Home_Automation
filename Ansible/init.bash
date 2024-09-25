sudo apt update && sudo apt upgrade -y
sudo apt install -y git software-properties-common
sudo add-apt-repository --yes --update ppa:ansible/ansible
sudo apt install -y ansible
mkdir ~/source
cd ~/source
git clone git@github.com:avelis26/Home_Automation.git
#ansible-playbook core_setup.yml
#sudo reboot
