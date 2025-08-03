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
        self.exclude_file = "/tmp/emby-sync-exclude.txt"
        self.bandwidth_limit = "1028" # KB/s
        self.max_retries = 3
        
        # Sleep hours (24-hour format)
        self.sleep_start = 0
        self.sleep_end = 24
        
        # Files/patterns to exclude (embytwo-only content)
        # Add your show names here - supports wildcards
        self.exclusions = [
            # "ShowName*",
            # "/path/to/show", 
            # "specific_file.mp4",
        ]
        
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

    def create_exclude_file(self):
        """Create exclusion file for rsync"""
        try:
            with open(self.exclude_file, 'w') as f:
                for exclusion in self.exclusions:
                    f.write(f"{exclusion}\n")
            self.logger.info(f"Created exclusion file with {len(self.exclusions)} patterns")
        except Exception as e:
            self.logger.error(f"Failed to create exclusion file: {e}")
            return False
        return True

    def cleanup_exclude_file(self):
        """Remove temporary exclusion file"""
        try:
            os.remove(self.exclude_file)
        except FileNotFoundError:
            pass

    def is_sleep_time(self):
        """Check if current time is within sleep hours"""
        current_hour = datetime.now().hour
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
        """Perform rsync with checksum comparison and exclusions"""
        destination = f"{self.dest_user}@{self.dest_host}:{self.dest_path}/"
        
        cmd = [
            'rsync',
            '-avzh',
            '--progress',
            '--partial',
            '--checksum',
            '--delete',
            f'--bwlimit={self.bandwidth_limit}',
            '--stats',
            f'--exclude-from={self.exclude_file}',
            f'{self.source_path}/',
            destination
        ]
        
        for attempt in range(1, self.max_retries + 1):
            self.logger.info(f"Sync attempt {attempt} of {self.max_retries}")
            self.logger.info("Using checksum comparison (this may take longer for initial scan)")
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    self.logger.info("Sync completed successfully")
                    if "Number of files:" in result.stdout:
                        stats_lines = [line for line in result.stdout.split('\n') if 'Number of' in line or 'Total file size:' in line or 'sent' in line]
                        for line in stats_lines:
                            if line.strip():
                                self.logger.info(f"Stats: {line.strip()}")
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
                self.logger.info(f"Not in sleep hours ({self.sleep_start}:00 - {self.sleep_end-1}:59). Exiting.")
                return
            
            # Check for running instance
            self.check_lock()
            
            # Create lock file
            self.create_lock()
            
            # Create exclusion file
            if not self.create_exclude_file():
                self.logger.error("Failed to create exclusion file. Exiting.")
                return
            
            # Test SSH connection
            if not self.test_ssh_connection():
                self.logger.error("SSH connection failed. Exiting.")
                return
            
            # Start sync
            self.logger.info(f"Starting media sync from {self.source_path} to {self.dest_user}@{self.dest_host}:{self.dest_path}")
            if self.exclusions:
                self.logger.info(f"Excluding {len(self.exclusions)} patterns from sync")
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
            self.cleanup_exclude_file()
            self.remove_lock()
            self.logger.info("Sync process ended")

if __name__ == "__main__":
    sync = EmbySync()
    sync.run()