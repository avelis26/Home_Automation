#!/usr/bin/env python3

import os
import sys
import time
import subprocess
import logging
import psutil
import hashlib
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict

class SmartEmbySync:
    def __init__(self):
        # Specify only the folders you want to sync
        self.source_paths = [
            "/mnt/data/Media/Movies",
            "/mnt/data/Media/Shows"
        ]
        self.dest_user = "grace"
        self.dest_host = "embytwo"
        self.dest_path = "/mnt/data/Media"
        self.log_file = "/var/log/emby-sync.log"
        self.lock_file = "/tmp/emby-sync.lock"
        self.cache_file = "/tmp/emby-sync-cache.json"
        self.bandwidth_limit = "2056"  # KB/s
        self.max_retries = 3
        
        # Sleep hours (24-hour format)
        self.sleep_start = 0
        self.sleep_end = 24
        
        # Files/patterns to exclude
        self.exclusions = [
            # Add your exclusions paths here
        ]
        
        # Sync ALL file types (set to None to disable filtering)
        self.media_extensions = None
        
        # Minimum file size to consider for duplicate detection (in bytes)
        self.min_file_size = 64 * 1024 * 1024  # 64MB
        
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

    def get_file_hash_sample(self, file_path):
        """Get a fast hash sample of a file (first 1MB + last 1MB + size)"""
        try:
            file_size = os.path.getsize(file_path)
            if file_size < self.min_file_size:
                return None
                
            hasher = hashlib.md5()
            hasher.update(str(file_size).encode())
            
            with open(file_path, 'rb') as f:
                # Read first 1MB
                chunk = f.read(1024 * 1024)
                hasher.update(chunk)
                
                # If file is large enough, read last 1MB
                if file_size > 2 * 1024 * 1024:
                    f.seek(-1024 * 1024, 2)
                    chunk = f.read(1024 * 1024)
                    hasher.update(chunk)
                    
            return hasher.hexdigest()
        except Exception as e:
            self.logger.warning(f"Could not hash {file_path}: {e}")
            return None

    def scan_local_files(self):
        """Scan ALL files from specified source paths and create hash map"""
        self.logger.info(f"Scanning local files from {len(self.source_paths)} paths...")
        local_files = {}
        hash_to_files = defaultdict(list)
        
        for source_path in self.source_paths:
            if not os.path.exists(source_path):
                self.logger.warning(f"Source path does not exist: {source_path}")
                continue
                
            self.logger.info(f"Scanning: {source_path}")
            
            for root, dirs, files in os.walk(source_path):
                for file in files:
                    # Skip hidden files and system files
                    if file.startswith('.'):
                        continue
                        
                    file_path = os.path.join(root, file)
                    # Calculate relative path from /mnt/data/Media base
                    rel_path = os.path.relpath(file_path, "/mnt/data/Media")
                        
                        # Skip excluded files
                        if any(excl in rel_path for excl in self.exclusions):
                            continue
                        
                        try:
                            stat_info = os.stat(file_path)
                            file_info = {
                                'path': rel_path,
                                'size': stat_info.st_size,
                                'mtime': stat_info.st_mtime,
                                'hash': None
                            }
                            
                            # Get hash for large files
                            if stat_info.st_size >= self.min_file_size:
                                file_hash = self.get_file_hash_sample(file_path)
                                if file_hash:
                                    file_info['hash'] = file_hash
                                    hash_to_files[file_hash].append(rel_path)
                            
                            local_files[rel_path] = file_info
                            
                        except Exception as e:
                            self.logger.warning(f"Could not stat {file_path}: {e}")
        
        self.logger.info(f"Found {len(local_files)} local files")
        return local_files, hash_to_files

    def scan_remote_files(self):
        """Scan ALL remote files via SSH - only in the folders we care about"""
        self.logger.info("Scanning remote files...")
        
        # Convert source paths to relative paths for remote scanning
        relative_paths = []
        for source_path in self.source_paths:
            rel_path = os.path.relpath(source_path, "/mnt/data/Media")
            relative_paths.append(rel_path)
        
        # Create a Python script to run on remote host
        remote_script = f'''
import os
import json
from pathlib import Path

exclusions = {self.exclusions}
base_path = "{self.dest_path}"
scan_paths = {relative_paths}

remote_files = {{}}
for rel_scan_path in scan_paths:
    full_scan_path = os.path.join(base_path, rel_scan_path)
    if not os.path.exists(full_scan_path):
        continue
        
    for root, dirs, files in os.walk(full_scan_path):
        for file in files:
            # Skip hidden files and system files
            if file.startswith('.'):
                continue
                
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, base_path)
            
            # Skip excluded files
            if any(excl in rel_path for excl in exclusions):
                continue
            
            try:
                stat_info = os.stat(file_path)
                remote_files[rel_path] = {{
                    'size': stat_info.st_size,
                    'mtime': stat_info.st_mtime
                }}
            except Exception:
                pass

print(json.dumps(remote_files))
'''
        
        cmd = [
            'ssh', f'{self.dest_user}@{self.dest_host}',
            f'python3 -c "{remote_script}"'
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                remote_files = json.loads(result.stdout)
                self.logger.info(f"Found {len(remote_files)} remote files")
                return remote_files
            else:
                self.logger.error(f"Remote scan failed: {result.stderr}")
                return {}
        except Exception as e:
            self.logger.error(f"Remote scan failed: {e}")
            return {}

    def find_rename_candidates(self, local_files, remote_files, hash_to_files):
        """Find files that can be renamed instead of transferred"""
        rename_operations = []
        
        # Create reverse lookup for remote files by size
        remote_by_size = defaultdict(list)
        for remote_path, info in remote_files.items():
            remote_by_size[info['size']].append(remote_path)
        
        for file_hash, local_paths in hash_to_files.items():
            if len(local_paths) == 1:  # Only process files with unique hashes
                local_path = local_paths[0]
                local_info = local_files[local_path]
                
                if local_path not in remote_files:  # File doesn't exist at new location
                    # Look for files with same size on remote
                    candidates = remote_by_size.get(local_info['size'], [])
                    
                    for remote_path in candidates:
                        if remote_path not in [lp for lp in local_files.keys()]:  # Remote file doesn't exist locally
                            rename_operations.append({
                                'from': remote_path,
                                'to': local_path,
                                'size': local_info['size']
                            })
                            # Remove from candidates to avoid duplicate operations
                            remote_by_size[local_info['size']].remove(remote_path)
                            break
        
        return rename_operations

    def execute_renames(self, rename_operations):
        """Execute rename operations on remote server"""
        if not rename_operations:
            self.logger.info("No rename operations needed")
            return True
        
        self.logger.info(f"Executing {len(rename_operations)} rename operations...")
        
        for operation in rename_operations:
            old_path = f"{self.dest_path}/{operation['from']}"
            new_path = f"{self.dest_path}/{operation['to']}"
            
            # Create directory if needed
            new_dir = os.path.dirname(new_path)
            mkdir_cmd = [
                'ssh', f'{self.dest_user}@{self.dest_host}',
                f'mkdir -p "{new_dir}"'
            ]
            
            try:
                subprocess.run(mkdir_cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to create directory {new_dir}: {e}")
                continue
            
            # Perform rename
            rename_cmd = [
                'ssh', f'{self.dest_user}@{self.dest_host}',
                f'mv "{old_path}" "{new_path}"'
            ]
            
            try:
                result = subprocess.run(rename_cmd, check=True, capture_output=True)
                self.logger.info(f"Renamed: {operation['from']} -> {operation['to']} ({operation['size'] / 1024 / 1024:.1f} MB)")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to rename {operation['from']}: {e}")
        
        return True

    def sync_remaining_files(self, local_files, remote_files, renamed_files):
        """Sync only the files that actually need to be transferred"""
        files_to_sync = []
        
        for local_path, local_info in local_files.items():
            if local_path in renamed_files:
                continue  # Skip files we renamed
                
            remote_info = remote_files.get(local_path)
            
            if not remote_info:
                # File doesn't exist on remote
                files_to_sync.append(local_path)
            elif (local_info['size'] != remote_info['size'] or 
                  local_info['mtime'] > remote_info['mtime']):
                # File is different or newer
                files_to_sync.append(local_path)
        
        if not files_to_sync:
            self.logger.info("No files need syncing")
            return True
        
        self.logger.info(f"Syncing {len(files_to_sync)} files...")
        
        # Sync each source path separately
        for source_path in self.source_paths:
            if not os.path.exists(source_path):
                continue
                
            # Filter files for this source path
            source_rel_path = os.path.relpath(source_path, "/mnt/data/Media")
            source_files = [f for f in files_to_sync if f.startswith(source_rel_path)]
            
            if not source_files:
                continue
                
            self.logger.info(f"Syncing {len(source_files)} files from {source_path}")
            
            # Create temporary include file
            include_file = f"/tmp/emby-sync-include-{hash(source_path)}.txt"
            try:
                with open(include_file, 'w') as f:
                    for file_path in source_files:
                        # Make path relative to this source directory
                        rel_to_source = os.path.relpath(
                            os.path.join("/mnt/data/Media", file_path), 
                            source_path
                        )
                        f.write(f"{rel_to_source}\n")
                
                destination = f"{self.dest_user}@{self.dest_host}:{self.dest_path}/{source_rel_path}/"
                
                cmd = [
                    'rsync',
                    '-avzh',
                    '--progress',
                    '--partial',
                    f'--bwlimit={self.bandwidth_limit}',
                    '--stats',
                    f'--files-from={include_file}',
                    f'{source_path}/',
                    destination
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode != 0:
                    self.logger.error(f"File sync failed for {source_path}: {result.stderr}")
                    return False
                    
            except Exception as e:
                self.logger.error(f"File sync failed for {source_path}: {e}")
                return False
            finally:
                try:
                    os.remove(include_file)
                except FileNotFoundError:
                    pass
        
        self.logger.info("File sync completed successfully")
        return True

    def cleanup_remote_files(self, local_files, remote_files):
        """Remove files from remote that no longer exist locally"""
        files_to_remove = []
        
        for remote_path in remote_files:
            if remote_path not in local_files:
                files_to_remove.append(remote_path)
        
        if not files_to_remove:
            self.logger.info("No remote files to clean up")
            return True
        
        self.logger.info(f"Removing {len(files_to_remove)} obsolete files from remote...")
        
        for remote_path in files_to_remove:
            full_path = f"{self.dest_path}/{remote_path}"
            rm_cmd = [
                'ssh', f'{self.dest_user}@{self.dest_host}',
                f'rm -f "{full_path}"'
            ]
            
            try:
                subprocess.run(rm_cmd, check=True, capture_output=True)
                self.logger.info(f"Removed: {remote_path}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to remove {remote_path}: {e}")
        
        return True

    def run(self):
        """Main execution method"""
        try:
            self.logger.info("Smart Emby sync script started")
            
            # Check if it's sleep time
            if not self.is_sleep_time():
                self.logger.info(f"Not in sleep hours ({self.sleep_start}:00 - {self.sleep_end-1}:59). Exiting.")
                return
            
            # Check for running instance
            self.check_lock()
            
            # Create lock file
            self.create_lock()
            
            # Test SSH connection
            if not self.test_ssh_connection():
                self.logger.error("SSH connection failed. Exiting.")
                return
            
            # Step 1: Scan both local and remote files
            local_files, hash_to_files = self.scan_local_files()
            remote_files = self.scan_remote_files()
            
            # Step 2: Find rename opportunities
            rename_operations = self.find_rename_candidates(local_files, remote_files, hash_to_files)
            
            # Step 3: Execute renames
            renamed_files = set()
            if rename_operations:
                self.execute_renames(rename_operations)
                renamed_files = {op['to'] for op in rename_operations}
                
                # Update remote_files to reflect renames
                for operation in rename_operations:
                    if operation['from'] in remote_files:
                        remote_files[operation['to']] = remote_files.pop(operation['from'])
            
            # Step 4: Sync remaining files
            self.sync_remaining_files(local_files, remote_files, renamed_files)
            
            # Step 5: Clean up obsolete files
            self.cleanup_remote_files(local_files, remote_files)
            
            self.logger.info("Smart sync completed successfully")
            
        except KeyboardInterrupt:
            self.logger.info("Sync interrupted by user")
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
        finally:
            self.remove_lock()
            self.logger.info("Sync process ended")

if __name__ == "__main__":
    sync = SmartEmbySync()
    sync.run()