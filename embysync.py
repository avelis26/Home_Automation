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

class IntelligentEmbySync:
    def __init__(self, dry_run=False):
        # Configuration
        self.source_base = "/mnt/data/Media"
        self.dest_user = "grace"
        self.dest_host = "embytwo"
        self.dest_base = "/mnt/data/Media"
        
        # Only sync these subdirectories
        self.sync_dirs = ["Movies", "Shows"]
        
        # Dry run mode - no actual changes made
        self.dry_run = dry_run
        self.log_file = "/var/log/emby-sync.log"
        self.lock_file = "/tmp/emby-sync.lock"
        self.cache_file = "/tmp/emby-sync-cache.json"
        self.bandwidth_limit = "550"  # KB/s
        self.max_retries = 3
        
        # Sleep hours (24-hour format)
        self.sleep_start = 23
        self.sleep_end = 9
        
        # File size threshold for checksum comparison (in bytes)
        # Files smaller than this will always be checksummed
        # Larger files will use size+mtime first, then checksum if needed
        self.checksum_threshold = 100 * 1024 * 1024  # 100MB
        
        # Setup logging
        log_level = logging.INFO
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        
        if self.dry_run:
            log_format = '%(asctime)s - [DRY RUN] %(levelname)s - %(message)s'
        
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        if self.dry_run:
            self.logger.info("="*50)
            self.logger.info("DRY RUN MODE - NO CHANGES WILL BE MADE")
            self.logger.info("="*50)

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

    def get_file_checksum(self, file_path, remote=False):
        """Calculate MD5 checksum of a file"""
        try:
            if remote:
                # Calculate checksum on remote host
                cmd = f"ssh {self.dest_user}@{self.dest_host} \"md5sum '{file_path}' 2>/dev/null || echo 'ERROR'\""
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
                if result.returncode == 0 and not result.stdout.startswith('ERROR'):
                    return result.stdout.split()[0]
                return None
            else:
                # Calculate checksum locally
                hash_md5 = hashlib.md5()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_md5.update(chunk)
                return hash_md5.hexdigest()
        except Exception as e:
            self.logger.warning(f"Failed to calculate checksum for {file_path}: {e}")
            return None

    def get_file_info(self, file_path, remote=False):
        """Get file size and modification time"""
        try:
            if remote:
                cmd = f"ssh {self.dest_user}@{self.dest_host} \"stat -c '%s %Y' '{file_path}' 2>/dev/null || echo 'ERROR'\""
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                if result.returncode == 0 and not result.stdout.startswith('ERROR'):
                    size, mtime = result.stdout.strip().split()
                    return int(size), int(mtime)
                return None, None
            else:
                stat = os.stat(file_path)
                return stat.st_size, int(stat.st_mtime)
        except Exception as e:
            self.logger.warning(f"Failed to get file info for {file_path}: {e}")
            return None, None

    def scan_directory(self, base_path, remote=False):
        """Scan directory and return file information"""
        files_info = {}
        
        if remote:
            # Use find command on remote host to get all files with size and mtime
            cmd = f"ssh {self.dest_user}@{self.dest_host} \"find '{base_path}' -type f -exec stat -c '%n|%s|%Y' {{}} \\; 2>/dev/null\""
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if '|' in line:
                            parts = line.rsplit('|', 2)
                            if len(parts) == 3:
                                filepath, size, mtime = parts
                                try:
                                    files_info[filepath] = {
                                        'size': int(size),
                                        'mtime': int(mtime),
                                        'checksum': None
                                    }
                                except ValueError:
                                    continue
            except Exception as e:
                self.logger.error(f"Failed to scan remote directory: {e}")
                return {}
        else:
            # Local directory scan
            try:
                for root, dirs, files in os.walk(base_path):
                    for file in files:
                        filepath = os.path.join(root, file)
                        try:
                            size, mtime = self.get_file_info(filepath)
                            if size is not None:
                                files_info[filepath] = {
                                    'size': size,
                                    'mtime': mtime,
                                    'checksum': None
                                }
                        except Exception as e:
                            self.logger.warning(f"Skipping file {filepath}: {e}")
                            continue
            except Exception as e:
                self.logger.error(f"Failed to scan local directory: {e}")
                return {}
        
        self.logger.info(f"Found {len(files_info)} files in {'remote' if remote else 'local'} directory")
        return files_info

    def find_content_matches(self, source_files, dest_files, source_base, dest_base):
        """Find files with identical content but different names/paths"""
        self.logger.info("Analyzing file content matches...")
        
        # Group files by size first (quick filter)
        source_by_size = defaultdict(list)
        dest_by_size = defaultdict(list)
        
        for path, info in source_files.items():
            source_by_size[info['size']].append(path)
        
        for path, info in dest_files.items():
            dest_by_size[info['size']].append(path)
        
        matches = {}  # dest_path: source_path
        potential_matches = []
        
        # Find potential matches (same size)
        for size in source_by_size:
            if size in dest_by_size:
                for src_path in source_by_size[size]:
                    for dest_path in dest_by_size[size]:
                        # Skip if paths are identical (already in sync)
                        src_relative = os.path.relpath(src_path, source_base)
                        dest_relative = os.path.relpath(dest_path, dest_base)
                        if src_relative == dest_relative:
                            continue
                        potential_matches.append((src_path, dest_path, size))
        
        self.logger.info(f"Found {len(potential_matches)} potential content matches to verify")
        
        # Verify matches with checksums
        for i, (src_path, dest_path, size) in enumerate(potential_matches):
            if i % 10 == 0:
                self.logger.info(f"Verifying content match {i+1}/{len(potential_matches)}")
            
            # For large files, check mtime first as additional filter
            if size > self.checksum_threshold:
                src_mtime = source_files[src_path]['mtime']
                dest_mtime = dest_files[dest_path]['mtime']
                # If files are very different in age, less likely to be the same content
                if abs(src_mtime - dest_mtime) > 86400:  # More than 1 day difference
                    continue
            
            # Calculate checksums
            src_checksum = self.get_file_checksum(src_path, remote=False)
            if src_checksum is None:
                continue
                
            dest_checksum = self.get_file_checksum(dest_path, remote=True)
            if dest_checksum is None:
                continue
            
            if src_checksum == dest_checksum:
                # Found a match! 
                matches[dest_path] = src_path
                self.logger.info(f"Content match: {os.path.basename(dest_path)} -> {os.path.basename(src_path)}")
        
        self.logger.info(f"Found {len(matches)} files with matching content but different names")
        return matches

    def remote_rename(self, old_path, new_path):
        """Rename file on remote host"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would rename: {old_path} -> {new_path}")
            return True
            
        try:
            # Create destination directory if needed
            dest_dir = os.path.dirname(new_path)
            cmd = f"ssh {self.dest_user}@{self.dest_host} \"mkdir -p '{dest_dir}' && mv '{old_path}' '{new_path}'\""
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Failed to rename {old_path} to {new_path}: {e}")
            return False

    def remote_delete(self, file_path):
        """Delete file on remote host"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would delete: {file_path}")
            return True
            
        try:
            cmd = f"ssh {self.dest_user}@{self.dest_host} \"rm -f '{file_path}'\""
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Failed to delete {file_path}: {e}")
            return False

    def transfer_file(self, src_path, dest_path):
        """Transfer single file with bandwidth limit"""
        if self.dry_run:
            file_size = os.path.getsize(src_path) if os.path.exists(src_path) else 0
            self.logger.info(f"[DRY RUN] Would transfer: {src_path} -> {dest_path} ({file_size} bytes)")
            return True
            
        try:
            # Create destination directory
            dest_dir = os.path.dirname(dest_path)
            mkdir_cmd = f"ssh {self.dest_user}@{self.dest_host} \"mkdir -p '{dest_dir}'\""
            subprocess.run(mkdir_cmd, shell=True, timeout=30)
            
            # Transfer file
            cmd = [
                'rsync', '-avz', '--progress', '--partial',
                f'--bwlimit={self.bandwidth_limit}',
                src_path,
                f"{self.dest_user}@{self.dest_host}:{dest_path}"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode == 0:
                self.logger.info(f"Transferred: {os.path.basename(src_path)}")
                return True
            else:
                self.logger.error(f"Transfer failed for {src_path}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Transfer failed for {src_path}: {e}")
            return False

    def cleanup_empty_dirs(self, dest_path):
        """Remove empty directories on remote host"""
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would cleanup empty directories in: {dest_path}")
            return
            
        try:
            cmd = f"ssh {self.dest_user}@{self.dest_host} \"find '{dest_path}' -type d -empty -delete 2>/dev/null\""
            subprocess.run(cmd, shell=True, timeout=120)
            self.logger.info(f"Cleaned up empty directories in {dest_path}")
        except Exception as e:
            self.logger.warning(f"Failed to cleanup empty directories in {dest_path}: {e}")

    def intelligent_sync(self):
        """Perform intelligent sync with content comparison and rename detection"""
        self.logger.info("Starting intelligent media sync...")
        
        total_renames = 0
        total_deletions = 0
        total_transfers = 0
        
        # Process each sync directory separately
        for sync_dir in self.sync_dirs:
            source_path = os.path.join(self.source_base, sync_dir)
            dest_path = os.path.join(self.dest_base, sync_dir)
            
            self.logger.info(f"Processing directory: {sync_dir}")
            
            # Check if source directory exists
            if not os.path.exists(source_path):
                self.logger.warning(f"Source directory {source_path} does not exist, skipping")
                continue
            
            # Scan both directories
            self.logger.info(f"Scanning source directory: {source_path}")
            source_files = self.scan_directory(source_path, remote=False)
            if not source_files:
                self.logger.error(f"Failed to scan source directory: {source_path}")
                continue
            
            self.logger.info(f"Scanning destination directory: {dest_path}")
            dest_files = self.scan_directory(dest_path, remote=True)
            
            # Find content matches (same content, different names)
            content_matches = self.find_content_matches(source_files, dest_files, source_path, dest_path)
            
            # Phase 1: Rename matching content
            rename_count = 0
            for dest_file_path, src_file_path in content_matches.items():
                # Calculate new destination path
                relative_path = os.path.relpath(src_file_path, source_path)
                new_dest_path = os.path.join(dest_path, relative_path)
                
                if dest_file_path != new_dest_path:
                    self.logger.info(f"Renaming: {os.path.basename(dest_file_path)} -> {relative_path}")
                    if self.remote_rename(dest_file_path, new_dest_path):
                        rename_count += 1
                        # Update dest_files to reflect the rename
                        if dest_file_path in dest_files:
                            dest_files[new_dest_path] = dest_files.pop(dest_file_path)
                    else:
                        self.logger.error(f"Failed to rename {dest_file_path}")
            
            self.logger.info(f"Renamed {rename_count} files in {sync_dir}")
            total_renames += rename_count
            
            # Phase 2: Identify files that need transfer or deletion
            # Get current state after renames
            dest_files_updated = self.scan_directory(dest_path, remote=True)
            
            files_to_transfer = []
            files_to_delete = []
            
            # Check each source file
            for src_file_path, src_info in source_files.items():
                relative_path = os.path.relpath(src_file_path, source_path)
                expected_dest_path = os.path.join(dest_path, relative_path)
                
                if expected_dest_path in dest_files_updated:
                    # File exists, check if content is the same
                    dest_info = dest_files_updated[expected_dest_path]
                    if src_info['size'] != dest_info['size']:
                        # Different size = different content, needs transfer
                        files_to_transfer.append((src_file_path, expected_dest_path))
                    # Note: We skip mtime comparison since renames might change it
                else:
                    # File doesn't exist at expected location, needs transfer
                    files_to_transfer.append((src_file_path, expected_dest_path))
            
            # Check for files that exist on dest but not on source (need deletion)
            for dest_file_path in dest_files_updated:
                relative_path = os.path.relpath(dest_file_path, dest_path)
                expected_src_path = os.path.join(source_path, relative_path)
                if expected_src_path not in source_files:
                    files_to_delete.append(dest_file_path)
            
            # Phase 3: Delete orphaned files first (to free space)
            self.logger.info(f"Deleting {len(files_to_delete)} orphaned files in {sync_dir}...")
            delete_count = 0
            for dest_file_path in files_to_delete:
                if self.remote_delete(dest_file_path):
                    delete_count += 1
                else:
                    self.logger.error(f"Failed to delete {dest_file_path}")
            
            self.logger.info(f"Deleted {delete_count} orphaned files in {sync_dir}")
            total_deletions += delete_count
            
            # Phase 4: Transfer new/changed files
            self.logger.info(f"Transferring {len(files_to_transfer)} files in {sync_dir}...")
            transfer_count = 0
            for src_file_path, dest_file_path in files_to_transfer:
                if self.transfer_file(src_file_path, dest_file_path):
                    transfer_count += 1
                else:
                    self.logger.error(f"Failed to transfer {src_file_path}")
            
            self.logger.info(f"Transferred {transfer_count} files in {sync_dir}")
            total_transfers += transfer_count
        
        # Phase 5: Cleanup empty directories in all sync dirs
        for sync_dir in self.sync_dirs:
            dest_path = os.path.join(self.dest_base, sync_dir)
            self.cleanup_empty_dirs(dest_path)
        
        # Summary
        summary_prefix = "[DRY RUN] Would perform:" if self.dry_run else "Total sync completed:"
        self.logger.info(f"{summary_prefix} {total_renames} renames, {total_deletions} deletions, {total_transfers} transfers")
        
        if self.dry_run:
            self.logger.info("="*50)
            self.logger.info("DRY RUN COMPLETE - NO ACTUAL CHANGES WERE MADE")
            self.logger.info("="*50)
            
        return True

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

    def run(self):
        """Main execution method"""
        try:
            script_mode = "DRY RUN" if self.dry_run else "LIVE"
            self.logger.info(f"Intelligent Emby sync script started in {script_mode} mode")
            
            # Check if it's sleep time (skip in dry run for testing)
            if not self.dry_run and not self.is_sleep_time():
                self.logger.info(f"Not in sleep hours ({self.sleep_start}:00 - {self.sleep_end-1}:59). Exiting.")
                return
            elif self.dry_run:
                self.logger.info(f"Dry run mode - skipping sleep time check")
            
            # Check for running instance (skip lock in dry run)
            if not self.dry_run:
                self.check_lock()
                self.create_lock()
            else:
                self.logger.info("Dry run mode - skipping process lock")
            
            # Test SSH connection
            if not self.test_ssh_connection():
                self.logger.error("SSH connection failed. Exiting.")
                return
            
            # Start intelligent sync
            success = self.intelligent_sync()
            
            if success:
                mode_msg = "completed successfully (no changes made)" if self.dry_run else "completed successfully"
                self.logger.info(f"Intelligent media sync {mode_msg}")
            else:
                self.logger.error("Intelligent media sync failed")
                
        except KeyboardInterrupt:
            self.logger.info("Sync interrupted by user")
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
        finally:
            if not self.dry_run:
                self.remove_lock()
            self.logger.info("Sync process ended")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Intelligent Emby Media Synchronization')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Show what would be done without making any changes')
    parser.add_argument('--force-time', action='store_true',
                       help='Run regardless of sleep hours (useful for testing)')
    
    args = parser.parse_args()
    
    sync = IntelligentEmbySync(dry_run=args.dry_run)
    
    # Override sleep time check if force-time is used
    if args.force_time:
        sync.is_sleep_time = lambda: True
        sync.logger.info("Force-time mode enabled - ignoring sleep hours")
    
    sync.run()