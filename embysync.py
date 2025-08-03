#!/usr/bin/env python3

import os
import sys
import time
import subprocess
import logging
import psutil
from datetime import datetime
from pathlib import Path

class EmbySync:
    def __init__(self):
        # Configuration
        self.source_path = "/mnt/data/Media/tmp"
        self.dest_user = "grace"
        self.dest_host = "embytwo"
        self.dest_path = "/mnt/data/Media/tmp"
        self.log_file = "/var/log/emby-sync.log"
        self.lock_file = "/tmp/emby-sync.lock"
        self.bandwidth_limit = "128" # KB/s
        self.max_retries = 3
        
        # Sleep hours (24-hour format)
        self.sleep_start = 1 # 1 AM
        self.sleep_end = 6 # 6 AM
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def is_sleep_time(self):
        """Check if current time is within sleep hours"""
        current_hour = datetime.now().hour
        
        if self.sleep_start > self.sleep_end:
            # Sleep time crosses midnight (e.g., 01:00 to 06:00)
            return current_hour >= self.sleep_start or current_hour < self.sleep_end
        else:
            # Sleep time within same day
            return self.sleep_start <= current_hour < self.sleep_end

    def check_lock(self):
        """Check for existing lock file and running process"""
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, 'r') as f:
                    pid = int(f.read().strip())
                
                if psutil.pid_exists(pid):
                    self.logger.info(f"Sync already running (PID: {pid}). Exiting.")
                    sys.exit(1)
                else:
                    self.logger.info("Stale lock file found. Removing.")
                    os.remove(self.lock_file)
            except (ValueError, IOError) as e:
                self.logger.warning(f"Error reading lock file: {e}. Removing.")
                os.remove(self.lock_file)

    def create_lock(self):
        """Create lock file with current PID"""
        with open(self.lock_file, 'w') as f:
            f.write(str(os.getpid()))

    def remove_lock(self):
        """Remove lock file"""
        try:
            os.remove(self.lock_file)
        except FileNotFoundError:
            pass

    def test_ssh_connection(self):
        """Test SSH connection to destination host"""
        self.logger.info(f"Testing SSH connection to {self.dest_host}...")
        
        cmd = [
            'ssh', '-o', 'ConnectTimeout=10',
            f'{self.dest_user}@{self.dest_host}',
            'echo "SSH connection successful"'
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode == 0:
                self.logger.info("SSH connection successful")
                return True
            else:
                self.logger.error(f"SSH connection failed: {result.stderr.decode()}")
                return False
        except subprocess.TimeoutExpired:
            self.logger.error("SSH connection timed out")
            return False
        except Exception as e:
            self.logger.error(f"SSH test failed: {e}")
            return False

    def sync_files(self):
        """Perform rsync with retry logic"""
        destination = f"{self.dest_user}@{self.dest_host}:{self.dest_path}/"
        
        cmd = [
            'rsync',
            '-avzh',
            '--progress',
            '--partial',
            f'--bwlimit={self.bandwidth_limit}',
            '--ignore-existing',
            '--stats',
            f'{self.source_path}/',
            destination
        ]
        
        for attempt in range(1, self.max_retries + 1):
            self.logger.info(f"Sync attempt {attempt} of {self.max_retries}")
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    self.logger.info("Sync completed successfully")
                    self.logger.info(f"rsync output: {result.stdout}")
                    return True
                else:
                    self.logger.error(f"Sync attempt {attempt} failed (exit code: {result.returncode})")
                    self.logger.error(f"Error output: {result.stderr}")
                    
                    if attempt < self.max_retries:
                        self.logger.info("Waiting 60 seconds before retry...")
                        time.sleep(60)
                        
            except Exception as e:
                self.logger.error(f"Sync attempt {attempt} failed with exception: {e}")
                if attempt < self.max_retries:
                    time.sleep(60)
        
        self.logger.error(f"All sync attempts failed after {self.max_retries} tries")
        return False

    def run(self):
        """Main execution method"""
        try:
            self.logger.info("Emby sync script started")
            
            # Check if it's sleep time
            if not self.is_sleep_time():
                self.logger.info(f"Not in sleep hours ({self.sleep_start}:00 - {self.sleep_end}:00). Exiting.")
                return
            
            # Check for running instance
            self.check_lock()
            
            # Create lock file
            self.create_lock()
            
            # Test SSH connection
            if not self.test_ssh_connection():
                self.logger.error("SSH connection failed. Exiting.")
                return
            
            # Start sync
            self.logger.info(f"Starting media sync from {self.source_path} to {self.dest_user}@{self.dest_host}:{self.dest_path}")
            success = self.sync_files()
            
            if success:
                self.logger.info("Media sync completed successfully")
            else:
                self.logger.error("Media sync failed")
                
        except KeyboardInterrupt:
            self.logger.info("Sync interrupted by user")
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
        finally:
            self.remove_lock()
            self.logger.info("Sync process ended")

if __name__ == "__main__":
    sync = EmbySync()
    sync.run()