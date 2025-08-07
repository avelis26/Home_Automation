#!/usr/bin/env python3
import os
import json
import subprocess
import logging
from logging.handlers import RotatingFileHandler
import tempfile
from datetime import datetime
from pathlib import Path
import hashlib

class EmbySync:
    def __init__(self):
        self.local_base_path = "/mnt/data/Media"
        self.remote_base_path = "/mnt/data/Media"
        self.remote_user = "grace"
        self.remote_host = "embytwo"
        self.scan_paths = ["tmp", "tmp2"]
        self.bandwidth_limit = "4096"
        self.exclusions = []
        self.dry_run = False
        
        self.logger = logging.getLogger('EmbySync')
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler('/var/log/emby-sync.log', maxBytes=10485760, backupCount=5)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)
        
    def _generate_remote_script(self):
        scan_paths_json = json.dumps(self.scan_paths)
        script = f"""
import os
import json
import hashlib
from pathlib import Path

def calculate_md5(file_path, chunk_size=8192):
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return None

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
            if file.startswith('.'):
                continue
                
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, base_path)
            
            if any(excl in rel_path for excl in exclusions):
                continue
            
            try:
                stat_info = os.stat(file_path)
                md5_hash = calculate_md5(file_path)
                if md5_hash:
                    remote_files[rel_path] = {{
                        'size': stat_info.st_size,
                        'mtime': stat_info.st_mtime,
                        'md5': md5_hash
                    }}
            except Exception:
                pass

print(json.dumps(remote_files))
"""
        return script

    def calculate_md5(self, file_path, chunk_size=8192):
        """Calculate MD5 hash of a file"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            self.logger.warning(f"Failed to calculate MD5 for {file_path}: {e}")
            return None

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
        self.logger.info(f"{len(self.scan_paths)} path(s) to scan...")
        local_files = {}
        
        for rel_scan_path in self.scan_paths:
            full_scan_path = os.path.join(self.local_base_path, rel_scan_path)
            self.logger.info(f"Scanning: {full_scan_path}")
            
            if not os.path.exists(full_scan_path):
                continue
                
            for root, dirs, files in os.walk(full_scan_path):
                for file in files:
                    if file.startswith('.'):
                        continue
                        
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self.local_base_path)
                    
                    if any(excl in rel_path for excl in self.exclusions):
                        continue
                        
                    try:
                        stat_info = os.stat(file_path)
                        self.logger.debug(f"Calculating MD5 for {file}")
                        md5_hash = self.calculate_md5(file_path)
                        if md5_hash:
                            local_files[rel_path] = {
                                'size': stat_info.st_size,
                                'mtime': stat_info.st_mtime,
                                'md5': md5_hash
                            }
                    except Exception:
                        pass
                        
        self.logger.info(f"Found {len(local_files)} local files")
        return local_files

    def scan_remote_files(self):
        self.logger.info("Scanning remote files...")
        remote_script = self._generate_remote_script()
        self.logger.debug(f"Generated remote script:\n{remote_script}")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_file:
            temp_file.write(remote_script)
            temp_file_path = temp_file.name

        try:
            remote_script_path = "/tmp/embysync_remote.py"
            scp_cmd = f"scp {temp_file_path} {self.remote_user}@{self.remote_host}:{remote_script_path}"
            subprocess.run(scp_cmd, shell=True, check=True, capture_output=True, text=True)

            cmd = f"ssh {self.remote_user}@{self.remote_host} python3 {remote_script_path}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            
            remote_files = json.loads(result.stdout)
            self.logger.info(f"Found {len(remote_files)} remote files")
            return remote_files
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Remote scan failed: {e.stderr}")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse remote file list: {e}")
            return {}
        finally:
            os.unlink(temp_file_path)
            try:
                cleanup_cmd = f"ssh {self.remote_user}@{self.remote_host} rm -f {remote_script_path}"
                subprocess.run(cleanup_cmd, shell=True, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Failed to clean up remote script: {e.stderr}")

    def ensure_remote_directories(self, local_files):
        """Create necessary directories on remote server"""
        directories = set()
        for rel_path in local_files.keys():
            dir_path = os.path.dirname(rel_path)
            if dir_path:
                directories.add(dir_path)
        
        if not directories:
            return
            
        self.logger.info(f"Ensuring {len(directories)} remote directories exist...")
        
        for dir_path in directories:
            remote_dir = os.path.join(self.remote_base_path, dir_path)
            if not self.dry_run:
                try:
                    cmd = f"ssh {self.remote_user}@{self.remote_host} mkdir -p '{remote_dir}'"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    self.logger.debug(f"Created remote directory: {dir_path}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to create remote directory {dir_path}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would create directory: {dir_path}")

    def detect_renames(self, local_files, remote_files):
        """Detect files that have been renamed by matching MD5 hash"""
        renames = {}
        local_by_md5 = {}
        remote_by_md5 = {}
        
        for rel_path, info in local_files.items():
            if 'md5' in info and info['md5']:
                md5 = info['md5']
                if md5 not in local_by_md5:
                    local_by_md5[md5] = []
                local_by_md5[md5].append(rel_path)
            
        for rel_path, info in remote_files.items():
            if 'md5' in info and info['md5']:
                md5 = info['md5']
                if md5 not in remote_by_md5:
                    remote_by_md5[md5] = []
                remote_by_md5[md5].append(rel_path)
        
        for md5, local_paths in local_by_md5.items():
            if md5 in remote_by_md5:
                remote_paths = remote_by_md5[md5]
                if len(local_paths) == 1 and len(remote_paths) == 1:
                    local_path = local_paths[0]
                    remote_path = remote_paths[0]
                    if local_path != remote_path:
                        if os.path.dirname(local_path) == os.path.dirname(remote_path):
                            renames[remote_path] = local_path
        
        return renames

    def perform_renames(self, renames):
        """Execute renames on remote server"""
        if not renames:
            return
            
        self.logger.info(f"Performing {len(renames)} renames on remote...")
        
        for old_remote_path, new_local_path in renames.items():
            old_file_name = os.path.basename(old_remote_path)
            new_file_name = os.path.basename(new_local_path)
            
            old_full_path = os.path.join(self.remote_base_path, old_remote_path)
            new_full_path = os.path.join(self.remote_base_path, new_local_path)
            
            if not self.dry_run:
                try:
                    cmd = f"ssh {self.remote_user}@{self.remote_host} mv '{old_full_path}' '{new_full_path}'"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    self.logger.info(f"Renamed remote file: {old_file_name} -> {new_file_name}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to rename {old_file_name}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would rename: {old_file_name} -> {new_file_name}")

    def sync_files(self, local_files, remote_files, renames=None):
        self.logger.info(f"Processing {len(local_files)} files...")
        
        renamed_local_files = set(renames.values()) if renames else set()
        files_synced = 0
        files_skipped = 0
        
        for rel_path, local_info in local_files.items():
            if rel_path in renamed_local_files:
                continue
                
            file_name = os.path.basename(rel_path)
            local_path = os.path.join(self.local_base_path, rel_path)
            remote_path = os.path.join(self.remote_base_path, rel_path)
            
            needs_sync = True
            if rel_path in remote_files:
                remote_info = remote_files[rel_path]
                
                if ('md5' in local_info and local_info['md5'] and 
                    'md5' in remote_info and remote_info['md5']):
                    if local_info['md5'] == remote_info['md5']:
                        needs_sync = False
                        files_skipped += 1
                        self.logger.debug(f"Skipping {file_name} - identical MD5: {local_info['md5'][:8]}...")
                    else:
                        self.logger.info(f"MD5 differs for {file_name}: local={local_info['md5'][:8]}... vs remote={remote_info['md5'][:8]}...")
                elif local_info['size'] == remote_info['size']:
                    needs_sync = False
                    files_skipped += 1
                    self.logger.debug(f"Skipping {file_name} - same size ({local_info['size']} bytes), no MD5 available")
                    
            if needs_sync:
                if not self.dry_run:
                    try:
                        self.logger.info(f"Syncing file: {file_name}")
                        cmd = f"rsync -avzh --progress --partial --bwlimit={self.bandwidth_limit} '{local_path}' {self.remote_user}@{self.remote_host}:'{remote_path}'"
                        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                        files_synced += 1
                    except subprocess.CalledProcessError as e:
                        self.logger.error(f"Failed to sync {file_name}: {e.stderr}")
                else:
                    self.logger.info(f"[DRY RUN] Would sync: {file_name}")
                    files_synced += 1
                    
        self.logger.info(f"File sync completed successfully - {files_synced} synced, {files_skipped} skipped")

    def cleanup_remote_files(self, local_files, remote_files, renames=None):
        files_to_remove = [rel_path for rel_path in remote_files 
                          if rel_path not in local_files and 
                          (not renames or rel_path not in renames)]
        
        if not files_to_remove:
            self.logger.info("No remote files to clean up")
            return
            
        self.logger.info(f"Removing {len(files_to_remove)} extra files from remote...")
        
        for rel_path in files_to_remove:
            file_name = os.path.basename(rel_path)
            remote_path = os.path.join(self.remote_base_path, rel_path)
            if not self.dry_run:
                try:
                    cmd = f"ssh {self.remote_user}@{self.remote_host} rm -f '{remote_path}'"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    self.logger.info(f"Removed remote file: {file_name}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to remove {file_name}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would remove: {file_name}")

    def run(self):
        self.logger.info("Smart Emby sync script started")
        
        if not self.test_ssh_connection():
            self.logger.error("Aborting due to SSH connection failure")
            return
            
        local_files = self.scan_local_files()
        remote_files = self.scan_remote_files()
        
        renames = self.detect_renames(local_files, remote_files)
        self.perform_renames(renames)
        
        self.ensure_remote_directories(local_files)
        
        self.sync_files(local_files, remote_files, renames)
        
        self.cleanup_remote_files(local_files, remote_files, renames)
        
        self.logger.info("Smart sync completed successfully")
        
if __name__ == "__main__":
    sync = EmbySync()
    try:
        sync.run()
    except Exception as e:
        sync.logger.error(f"Sync failed: {str(e)}")
    finally:
        sync.logger.info("Sync process ended")