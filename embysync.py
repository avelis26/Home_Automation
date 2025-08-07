#!/usr/bin/env python3
# Made by Graham Pinkston (graham.pinkston@gmail.com) with the help of Claude & Grok (Claude is better)
# 2025-08-07_03:08
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class EmbySync:
    def __init__(self):
        self.local_base_path = "/mnt/data/Media"
        self.remote_base_path = "/mnt/data/Media"
        self.remote_user = "grace"
        self.remote_host = "embytwo"
        self.scan_paths = ["Shows", "Movies"]
        self.bandwidth_limit = "666"
        self.exclusions = []
        self.dry_run = False

        self.logger = logging.getLogger('EmbySync')
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            '/var/log/emby-sync.log',
            maxBytes=10485760,
            backupCount=5
        )
        handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(handler)

    def _generate_remote_script(self):
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
            if file.startswith('.'):
                continue
                
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, base_path)
            
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
        self.logger.info(f"Testing SSH connection to {self.remote_host}...")
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
        
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False
        ) as temp_file:
            temp_file.write(remote_script)
            temp_file_path = temp_file.name

        try:
            remote_script_path = "/tmp/embysync_remote.py"
            scp_cmd = (
                f"scp {temp_file_path} "
                f"{self.remote_user}@{self.remote_host}:{remote_script_path}"
            )
            subprocess.run(
                scp_cmd,
                shell=True,
                check=True,
                capture_output=True,
                text=True
            )

            cmd = (
                f"ssh {self.remote_user}@{self.remote_host} "
                f"python3 {remote_script_path}"
            )
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                check=True
            )
            
            remote_files = json.loads(result.stdout)
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
                cleanup_cmd = (
                    f"ssh {self.remote_user}@{self.remote_host} "
                    f"rm -f {remote_script_path}"
                )
                subprocess.run(
                    cleanup_cmd,
                    shell=True,
                    check=True,
                    capture_output=True,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Failed to clean up remote script: {e.stderr}")

    def ensure_remote_directories(self, local_files):
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
                    cmd = (
                        f"ssh {self.remote_user}@{self.remote_host} "
                        f"mkdir -p '{remote_dir}'"
                    )
                    subprocess.run(
                        cmd,
                        shell=True,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    self.logger.debug(f"Created remote directory: {dir_path}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(
                        f"Failed to create remote directory {dir_path}: {e.stderr}"
                    )
            else:
                self.logger.info(f"[DRY RUN] Would create directory: {dir_path}")

    def detect_renames(self, local_files, remote_files):
        renames = {}
        local_by_signature = {}
        remote_by_signature = {}
        
        for rel_path, info in local_files.items():
            signature = (info['size'], int(info['mtime']))
            if signature not in local_by_signature:
                local_by_signature[signature] = []
            local_by_signature[signature].append(rel_path)
            
        for rel_path, info in remote_files.items():
            signature = (info['size'], int(info['mtime']))
            if signature not in remote_by_signature:
                remote_by_signature[signature] = []
            remote_by_signature[signature].append(rel_path)
        
        for signature, local_paths in local_by_signature.items():
            if signature in remote_by_signature:
                remote_paths = remote_by_signature[signature]
                if len(local_paths) == 1 and len(remote_paths) == 1:
                    local_path = local_paths[0]
                    remote_path = remote_paths[0]
                    if local_path != remote_path:
                        if (os.path.dirname(local_path) == 
                            os.path.dirname(remote_path)):
                            renames[remote_path] = local_path
        
        return renames

    def detect_directory_renames(self, local_files, remote_files):
        """Detect when entire directories have been renamed"""
        dir_renames = {}
        local_dirs = {}
        remote_dirs = {}
        
        # Group files by directory and create directory signatures
        for rel_path, info in local_files.items():
            dir_path = os.path.dirname(rel_path)
            if dir_path:
                if dir_path not in local_dirs:
                    local_dirs[dir_path] = []
                local_dirs[dir_path].append((os.path.basename(rel_path), info['size']))
        
        for rel_path, info in remote_files.items():
            dir_path = os.path.dirname(rel_path)
            if dir_path:
                if dir_path not in remote_dirs:
                    remote_dirs[dir_path] = []
                remote_dirs[dir_path].append((os.path.basename(rel_path), info['size']))
        
        # Create signatures (sorted list of filename+size tuples)
        local_signatures = {dir_path: tuple(sorted(files)) for dir_path, files in local_dirs.items()}
        remote_signatures = {dir_path: tuple(sorted(files)) for dir_path, files in remote_dirs.items()}
        
        # Find matching signatures
        for local_dir, local_sig in local_signatures.items():
            for remote_dir, remote_sig in remote_signatures.items():
                if (local_sig == remote_sig and 
                    local_dir != remote_dir and
                    os.path.dirname(local_dir) == os.path.dirname(remote_dir)):  # Same parent dir
                    dir_renames[remote_dir] = local_dir
                    break
        
        return dir_renames

    def perform_renames(self, renames):
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
                    cmd = (
                        f"ssh {self.remote_user}@{self.remote_host} "
                        f"mv '{old_full_path}' '{new_full_path}'"
                    )
                    subprocess.run(
                        cmd,
                        shell=True,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    self.logger.info(
                        f"Renamed remote file: {old_file_name} -> {new_file_name}"
                    )
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to rename {old_file_name}: {e.stderr}")
            else:
                self.logger.info(
                    f"[DRY RUN] Would rename: {old_file_name} -> {new_file_name}"
                )

    def perform_directory_renames(self, dir_renames):
        """Execute directory renames on remote server"""
        if not dir_renames:
            return
            
        self.logger.info(f"Performing {len(dir_renames)} directory renames on remote...")
        
        for old_remote_dir, new_local_dir in dir_renames.items():
            old_full_path = os.path.join(self.remote_base_path, old_remote_dir)
            new_full_path = os.path.join(self.remote_base_path, new_local_dir)
            
            if not self.dry_run:
                try:
                    cmd = f"ssh {self.remote_user}@{self.remote_host} mv '{old_full_path}' '{new_full_path}'"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    self.logger.info(f"Renamed remote directory: {old_remote_dir} -> {new_local_dir}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to rename directory {old_remote_dir}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would rename directory: {old_remote_dir} -> {new_local_dir}")

    def sync_files(self, local_files, remote_files, renames=None, dir_renames=None):
        self.logger.info(f"Processing {len(local_files)} files...")
        
        renamed_local_files = set(renames.values()) if renames else set()
        renamed_dirs = set(dir_renames.values()) if dir_renames else set()
        
        for rel_path, local_info in local_files.items():
            if any(rel_path.startswith(renamed_dir + '/') for renamed_dir in renamed_dirs):
                continue
                
            file_name = os.path.basename(rel_path)
            local_path = os.path.join(self.local_base_path, rel_path)
            remote_path = os.path.join(self.remote_base_path, rel_path)
            
            needs_sync = True
            if rel_path in remote_files:
                remote_info = remote_files[rel_path]
                if (abs(local_info['size'] - remote_info['size']) < 1000 and
                    abs(local_info['mtime'] - remote_info['mtime']) < 2):
                    needs_sync = False
                    
            if needs_sync:
                if not self.dry_run:
                    try:
                        self.logger.info(f"Syncing file: {file_name}")
                        cmd = (
                            f"rsync -avzh --progress --partial "
                            f"--bwlimit={self.bandwidth_limit} '{local_path}' "
                            f"{self.remote_user}@{self.remote_host}:'{remote_path}'"
                        )
                        subprocess.run(
                            cmd,
                            shell=True,
                            check=True,
                            capture_output=True,
                            text=True
                        )
                    except subprocess.CalledProcessError as e:
                        self.logger.error(f"Failed to sync {file_name}: {e.stderr}")
                else:
                    self.logger.info(f"[DRY RUN] Would sync: {file_name}")
                    
        self.logger.info("File sync completed successfully")

    def cleanup_remote_files(self, local_files, remote_files, renames=None, dir_renames=None):
        renamed_dirs = set(dir_renames.keys()) if dir_renames else set()
        
        files_to_remove = [
            rel_path for rel_path in remote_files 
            if rel_path not in local_files and 
            (not renames or rel_path not in renames) and
            not any(rel_path.startswith(renamed_dir + '/') for renamed_dir in renamed_dirs)
        ]
        
        if not files_to_remove:
            self.logger.info("No remote files to clean up")
            return
            
        self.logger.info(f"Removing {len(files_to_remove)} extra files from remote...")
        
        for rel_path in files_to_remove:
            file_name = os.path.basename(rel_path)
            remote_path = os.path.join(self.remote_base_path, rel_path)
            if not self.dry_run:
                try:
                    cmd = (
                        f"ssh {self.remote_user}@{self.remote_host} "
                        f"rm -f '{remote_path}'"
                    )
                    subprocess.run(
                        cmd,
                        shell=True,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    self.logger.info(f"Removed remote file: {file_name}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to remove {file_name}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would remove: {file_name}")

    def cleanup_remote_dirs(self, local_files, remote_files, dir_renames=None):
        renamed_dirs = set(dir_renames.values()) if dir_renames else set()

        # Collect local and remote directory paths relative to base
        local_dirs = set(os.path.dirname(path) for path in local_files if os.path.dirname(path))
        remote_dirs = set(os.path.dirname(path) for path in remote_files if os.path.dirname(path))

        dirs_to_remove = [
            d for d in remote_dirs
            if d not in local_dirs
            and d not in dir_renames
            and os.path.dirname(d) == os.path.dirname(d)  # optional: ensure same parent
        ]
        if not dirs_to_remove:
            return self.logger.info("No extra remote directories to clean up")

        self.logger.info(f"Removing {len(dirs_to_remove)} extra directories from remote...")
        for d in sorted(dirs_to_remove, key=lambda x: len(x), reverse=True):
            remote_full = os.path.join(self.remote_base_path, d)
            if not self.dry_run:
                try:
                    cmd = f"ssh {self.remote_user}@{self.remote_host} rmdir '{remote_full}'"
                    subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
                    self.logger.info(f"Removed remote directory: {d}")
                except subprocess.CalledProcessError as e:
                    self.logger.error(f"Failed to remove directory {d}: {e.stderr}")
            else:
                self.logger.info(f"[DRY RUN] Would remove directory: {d}")


    def run(self):
        self.logger.info("Smart Emby sync script started")
        
        if not self.test_ssh_connection():
            self.logger.error("Aborting due to SSH connection failure")
            return
            
        local_files = self.scan_local_files()
        remote_files = self.scan_remote_files()
        
        renames = self.detect_renames(local_files, remote_files)
        dir_renames = self.detect_directory_renames(local_files, remote_files)
        
        self.perform_renames(renames)

        self.perform_directory_renames(dir_renames)
        
        self.ensure_remote_directories(local_files)
        
        self.sync_files(local_files, remote_files, renames, dir_renames)
        
        self.cleanup_remote_files(local_files, remote_files, renames, dir_renames)

        self.cleanup_remote_dirs(local_files, remote_files, dir_renames)
        
        self.logger.info("Smart sync completed successfully")


if __name__ == "__main__":
    sync = EmbySync()
    try:
        sync.run()
    except Exception as e:
        sync.logger.error(f"Sync failed: {str(e)}")
    finally:
        sync.logger.info("Sync process ended")