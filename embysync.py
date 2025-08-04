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
        # Configuration - now supports multiple source paths
        self.source_paths = [
            "/mnt/data/Media/Movies",
            "/mnt/data/Media/Shows"
            # Add more paths as needed
        ]
        self.dest_user = "grace"
        self.dest_host = "embytwo"
        self.dest_path = "/mnt/data/Media"
        self.log_file = "/var/log/emby-sync.log"
        self.lock_file = "/tmp/emby-sync.lock"
        self.exclude_file = "/tmp/emby-sync-exclude.txt"
        self.bandwidth_limit = "1028" # KB/s
        self.max_retries = 3
        
        # Sleep hours (24-hour format)
        self.sleep_start = 23
        self.sleep_end = 9
        
        # Files/patterns to exclude (embytwo-only content)
        # Add your show names here - supports wildcards
        self.exclusions = [
            # "ShowName*",
            # "/path/to/show", 
            # "specific_file.mp4",
        ]
        
        # Setup logging
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def validate_source_paths(self):
        """Validate that all source paths exist"""
        valid_paths = []
        for path in self.source_paths:
            if os.path.exists(path):
                valid_paths.append(path)
                self.logger.info(f"Source path validated: {path}")
            else:
                self.logger.warning(f"Source path does not exist: {path}")
        
        if not valid_paths:
            self.logger.error("No valid source paths found")
            return False
        
        self.source_paths = valid_paths
        return True

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
        
        # Handle overnight period (sleep_start > sleep_end means crossing midnight)
        if self.sleep_start > self.sleep_end:
            return current_hour >= self.sleep_start or current_hour < self.sleep_end
        else:
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

    def sync_single_path(self, source_path):
        """Perform rsync for a single source path with real-time output"""
        destination = f"{self.dest_user}@{self.dest_host}:{self.dest_path}/"
        
        # Get the relative path structure to maintain directory hierarchy
        source_name = os.path.basename(source_path)
        
        cmd = [
            'rsync',
            '-avzh',
            '--progress',
            '--partial',
            '--delete',
            f'--bwlimit={self.bandwidth_limit}',
            '--stats',
            f'--exclude-from={self.exclude_file}',
            f'{source_path}/',
            f'{destination}{source_name}/'
        ]
        
        for attempt in range(1, self.max_retries + 1):
            self.logger.info(f"Syncing {source_path} - attempt {attempt} of {self.max_retries}")
            
            try:
                # Use Popen for real-time output streaming
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                stdout_lines = []
                stderr_lines = []
                
                # Read output line by line in real-time
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        line = output.strip()
                        stdout_lines.append(line)
                        
                        # Log file transfers and progress
                        if line and not line.startswith('receiving') and not line.startswith('sent '):
                            # Skip empty lines and certain progress indicators
                            if (line.endswith('.mkv') or line.endswith('.mp4') or 
                                line.endswith('.avi') or line.endswith('.mov') or
                                line.endswith('.wmv') or line.endswith('.flv') or
                                line.endswith('.webm') or line.endswith('.m4v') or
                                line.endswith('.mpg') or line.endswith('.mpeg') or
                                '/' in line):
                                # This looks like a file being transferred
                                if not any(skip in line.lower() for skip in ['building file list', 'total size', 'speedup']):
                                    self.logger.info(f"Transferring: {line}")
                            elif 'to-chk=' in line or 'to-check=' in line:
                                # Progress indicator
                                self.logger.info(f"Progress: {line}")
                
                # Read any remaining stderr
                stderr_output = process.stderr.read()
                if stderr_output:
                    stderr_lines.extend(stderr_output.strip().split('\n'))
                
                # Wait for process to complete
                return_code = process.wait()
                
                if return_code == 0:
                    self.logger.info(f"Sync completed successfully for {source_path}")
                    
                    # Log statistics from stdout
                    stats_found = False
                    for line in stdout_lines:
                        if ('Number of files:' in line or 'Total file size:' in line or 
                            'sent ' in line or 'total size is' in line):
                            if line.strip():
                                self.logger.info(f"Stats for {source_name}: {line.strip()}")
                                stats_found = True
                    
                    return True
                else:
                    self.logger.error(f"Sync attempt {attempt} failed for {source_path} (exit code: {return_code})")
                    if stderr_lines:
                        for error_line in stderr_lines:
                            if error_line.strip():
                                self.logger.error(f"Error: {error_line.strip()}")
                    
                    if attempt < self.max_retries:
                        self.logger.info("Waiting 60 seconds before retry...")
                        time.sleep(60)
                        
            except Exception as e:
                self.logger.error(f"Sync attempt {attempt} failed for {source_path} with exception: {e}")
                if attempt < self.max_retries:
                    time.sleep(60)
        
        self.logger.error(f"All sync attempts failed for {source_path} after {self.max_retries} tries")
        return False

    def sync_files(self):
        """Perform rsync for all source paths"""
        self.logger.info("Using time and date for fast comparison...")
        self.logger.debug("Consider using checksum for more accurate comparisons")
        
        all_successful = True
        successful_syncs = 0
        
        for source_path in self.source_paths:
            self.logger.info(f"Starting sync for: {source_path}")
            success = self.sync_single_path(source_path)
            
            if success:
                successful_syncs += 1
                self.logger.info(f"Successfully synced: {source_path}")
            else:
                all_successful = False
                self.logger.error(f"Failed to sync: {source_path}")
        
        self.logger.info(f"Sync summary: {successful_syncs}/{len(self.source_paths)} paths synced successfully")
        return all_successful

    def run(self):
        """Main execution method"""
        try:
            self.logger.info("Emby sync script started")
            
            # Check if it's sleep time
            if not self.is_sleep_time():
                self.logger.info(f"Not in sleep hours ({self.sleep_start}:00 - {self.sleep_end-1}:59). Exiting.")
                return
            
            # Validate source paths
            if not self.validate_source_paths():
                self.logger.error("No valid source paths found. Exiting.")
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
            self.logger.info(f"Starting media sync from {len(self.source_paths)} source paths to {self.dest_user}@{self.dest_host}:{self.dest_path}")
            for path in self.source_paths:
                self.logger.info(f"  - {path}")
            
            if self.exclusions:
                self.logger.info(f"Excluding {len(self.exclusions)} patterns from sync")
            
            success = self.sync_files()
            
            if success:
                self.logger.info("All media sync operations completed successfully")
            else:
                self.logger.warning("Some media sync operations failed - check logs for details")
                
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