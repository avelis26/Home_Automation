#!/usr/bin/env python3
import os
import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path

class EmbySync:
    def __init__(self):
        self.local_base_path = "/mnt/data/Media"
        self.remote_base_path = "/mnt/data/Media"
        self.remote_user = "grace"
        self.remote_host = "embytwo"
        self.scan_paths = ["tmp"]  # Single source of truth for scan_paths
        self.exclusions = []
        self.dry_run = False
        
        # Setup logging
        self.logger = logging.getLogger('EmbySync')
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)
        
    def _generate_remote_script(self):
        # Convert self.scan_paths to a JSON string to safely embed in the remote script
        scan_paths_json = json.dumps(self.scan_paths)
        script = f"""
import os
import json
from pathlib import Path

exclusions = []
base_path = "/mnt/data/Media"
scan_paths = {scan_paths_json}

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
"""
        return script

    def test_ssh_connection(self):
        self.logger.info("Testing SSH connection to {}...".format(self.remote_host))
        try:
            result = subprocess.run(
                f"ssh {self.remote_user}@{self.remote_host} echo 'SSH_OK'",
                shell=True,
                capture_output=True,
                text=True,
                check=True
            )
            if "SSH_OK" in result.stdout:
                self.logger.info("SSH connection successful")
                return True
            else:
                self.logger.error("SSH connection test failed")
                return False
        except subprocess.CalledProcessError as e:
            self.logger.error(f"SSH connection failed: {e.stderr}")
            return False

    def scan_local_files(self):
        self.logger.info(f"Scanning local files from {len(self.scan_paths)} paths...")
        local_files = {}
        
        for rel_scan_path in self.scan_paths:
            full_scan_path = os.path.join(self.local_base_path, rel_scan_path)
            self.logger.info(f"Scanning: {full_scan_path}")
            
            if not os.path.exists(full_scan_path):
                continue
                
            for root, dirs, files in os.walk(full_scan_path):
                for file in files:
                    # Skip hidden files and system files
                    if file.startswith('.'):
                        continue
                        
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self.local_base_path)
                    
                    # Skip excluded files
                    if any(excl in rel_path for excl in self.exclusions):
                        continue
                        
                    try:
                        stat_info = os.stat(file_path)
                        local_files[rel_path] = {
                            'size': stat_info.st_size,
                            'mtime': stat_info.st_mtime
                        }
                    except Exception:
                        pass
                        
        self.logger.info(f"Found {len(local_files)} local files")
        return local_files

    def scan_remote_files(self):
        self.logger.info("Scanning remote files...")
        remote_script = self._generate_remote_script()
        self.logger.debug(f"Generated remote script:\n{remote_script}")
        
        # Pipe the Python script to the remote Python interpreter
        cmd = f"echo '{remote_script}' | ssh {self.remote_user}@{self.remote_host} python3 -"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            remote_files = json.loads(result.stdout)
            return remote_files
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Remote scan failed: {e.stderr}")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse remote file list: {e}")
            return {}

    def sync_files(self, local_files, remote_files):
        self.logger.info(f"Syncing {len(local_files)} files...")
        
        for rel_path, local_info in local_files.items():
            self.logger.info(f"Syncing {len(local_files)} files from {self.local_base_path}")
            local_path = os.path.join(self.local_base_path, rel_path)
            remote_path = os.path.join(self.remote_base_path, rel_path)
            
            # Check if file needs syncing
            needs_sync = True
            if rel_path in remote_files:
                remote_info = remote_files[rel_path]
                if (abs(local_info['size'] - remote_info['size']) < 1000 and
                    abs(local_info['mtime'] - remote_info['mtime']) < 2):
                    needs_sync = False
                    
            if needs_sync:
                if not self.dry_run:
                    try:
                        cmd = f"rsync -av --progress '{local_path}' {self.remote_user}@{self.remote_host}:'{remote_path}'"
                        subprocess.run(cmd, shell=True, check=True)
                    except subprocess.CalledProcessError as e:
                        self.logger.error(f"Failed to sync {rel_path}: {e.stderr}")
                else:
                    self.logger.info(f"[DRY RUN] Would sync: {rel_path}")
                    
        self.logger.info("File sync completed successfully")

    def cleanup_remote_files(self, local_files, remote_files):
        files_to_remove = [rel_path for rel_path in remote_files if rel_path not in local_files]
        
        if not files_to_remove:
            self.logger.info("No remote files to clean up")
            return
            
        self.logger.info(f"Removing {len(files_to_remove)} extra files from remote...")
        
        for rel_path in files_to_remove:
            remote_path = os.path.join(self.remote_base_path, rel_path)
            if not self.dry_run:
                try:
                    cmd = f"ssh {self.remote_user}@{self.remote_host} rm -f '{remote_path}'"
                    subprocess.run(cmd, shell=True, check=True)
                    self.logger.info(f"Removed remote file: {rel_path}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to remove {rel_path}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would remove: {rel_path}")

    def run(self):
        self.logger.info("Smart Emby sync script started")
        
        # Test SSH connection
        if not self.test_ssh_connection():
            self.logger.error("Aborting due to SSH connection failure")
            return
            
        # Scan files
        local_files = self.scan_local_files()
        remote_files = self.scan_remote_files()
        
        # Sync files
        self.sync_files(local_files, remote_files)
        
        # Cleanup extra files on remote
        self.cleanup_remote_files(local_files, remote_files)
        
        self.logger.info("Smart sync completed successfully")
        
if __name__ == "__main__":
    sync = EmbySync()
    try:
        sync.run()
    except Exception as e:
        sync.logger.error(f"Sync failed: {str(e)}")
    finally:
        sync.logger.info("Sync process ended")