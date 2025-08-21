#!/usr/bin/env python3

import os
import sys
import time
import subprocess
import logging
import json
import hashlib
import signal
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

class NetworkThrottle:
    """Network throttling utility to limit bandwidth usage"""
    
    def __init__(self, max_bps: int):
        self.max_bps = max_bps
        self.last_time = time.time()
        self.transferred = 0
    
    def throttle(self, bytes_transferred: int):
        """Throttle network operations to stay under bandwidth limit"""
        self.transferred += bytes_transferred
        current_time = time.time()
        elapsed = current_time - self.last_time
        
        if elapsed > 0:
            current_bps = self.transferred / elapsed
            if current_bps > self.max_bps:
                sleep_time = (self.transferred / self.max_bps) - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        # Reset counters every second
        if elapsed >= 1.0:
            self.last_time = current_time
            self.transferred = 0

class EmbySync:
    def __init__(self, config_path: str = "./embysyncconfig.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.ignore_state_file = self.config.get("ignore_state_file", False)
        self.lock_file = "/tmp/emby-sync.lock"
        self.running = True
        self.throttle = NetworkThrottle(self.config["bandwidth_limit_bps"])
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        # Setup logging
        self._setup_logging()
        
        # Create state file for tracking progress
        self.state_file = self.config.get("state_file", "./embysync_state.json")
        self.state = self._load_state()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def _load_config(self) -> Dict:
        """Load configuration from JSON file"""
        default_config = {
            "source_paths": [
                "/mnt/data/Media/Movies",
                "/mnt/data/Media/Shows", 
                "/mnt/data/Media/Sports/NFL/Seahawks_2025"
            ],
            "dest_user": "grace",
            "dest_host": "embytwo",
            "dest_base_path": "/mnt/data/Media",
            "log_file": "/var/log/emby-sync.log",
            "log_level": "DEBUG",
            "bandwidth_limit_kbps": 500,
            "bandwidth_limit_bps": 512000,  # 500KB/s in bytes
            "max_retries": 3,
            "retry_delay": 60,
            "ssh_timeout": 30,
            "rsync_partial": True,
            "rsync_compress": True
        }
        
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
                # Merge with defaults
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
            else:
                # Create default config file
                with open(self.config_path, 'w') as f:
                    json.dump(default_config, f, indent=4)
                print(f"Created default configuration file: {self.config_path}")
                return default_config
        except Exception as e:
            print(f"Error loading config: {e}. Using defaults.")
            return default_config

    def _load_state(self) -> Dict:
        """Load sync state from JSON file"""
        if self.ignore_state_file:
            # Return a minimal valid state so script logic doesn't break
            self.logger.info("Ignoring state file per config setting.")
            return {
                "last_sync_time": None,
                "current_directory": None,
                "completed_directories": [],
                "failed_directories": []
            }
        default_state = {
            "last_sync_time": None,
            "current_directory": None,
            "completed_directories": [],
            "failed_directories": []
        }
        
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.debug(f"Could not load state file: {e}")
        
        return default_state

    def _save_state(self):
        """Save current sync state to JSON file"""
        # Don't update the state file if ignore_state_file flag is true
        if self.ignore_state_file:
            self.logger.info("Skipping state file update per config setting.")
            return
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")

    def _setup_logging(self):
        """Setup logging with configurable level"""
        log_level = getattr(logging, self.config["log_level"].upper(), logging.DEBUG)
        
        # Create formatter that works well with colorized output
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Setup file handler
        file_handler = logging.FileHandler(self.config["log_file"])
        file_handler.setFormatter(formatter)
        
        # Setup console handler (only if not running from cron)
        handlers = [file_handler]
        if os.isatty(sys.stdout.fileno()):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            handlers.append(console_handler)
        
        logging.basicConfig(
            level=log_level,
            handlers=handlers,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        
        self.logger = logging.getLogger(__name__)

    def _check_lock(self) -> bool:
        """Check for existing lock file and running process"""
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, 'r') as f:
                    data = json.load(f)
                    pid = data.get('pid')
                
                # Check if process is still running
                try:
                    os.kill(pid, 0)  # Send signal 0 to check if process exists
                    self.logger.info(f"Sync already running (PID: {pid}). Exiting.")
                    return False
                except ProcessLookupError:
                    self.logger.info("Stale lock file found. Removing.")
                    os.remove(self.lock_file)
                    
            except (ValueError, IOError, json.JSONDecodeError) as e:
                self.logger.warning(f"Error reading lock file: {e}. Removing.")
                os.remove(self.lock_file)
        
        return True

    def _create_lock(self):
        """Create lock file with current PID and start time"""
        self.logger.info(f"Creating lock file.")
        lock_data = {
            'pid': os.getpid(),
            'start_time': datetime.now().isoformat(),
            'config_file': self.config_path
        }
        
        with open(self.lock_file, 'w') as f:
            json.dump(lock_data, f, indent=4)

    def _remove_lock(self):
        """Remove lock file"""
        try:
            os.remove(self.lock_file)
        except FileNotFoundError:
            pass

    def _test_ssh_connection(self) -> bool:
        """Test SSH connection to destination host"""
        self.logger.info(f"Testing SSH connection to {self.config['dest_host']}...")
        
        cmd = [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes',
            f"{self.config['dest_user']}@{self.config['dest_host']}",
            'echo "SSH connection successful"'
        ]
        
        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                timeout=self.config["ssh_timeout"],
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info("SSH connection successful")
                return True
            else:
                self.logger.error(f"SSH connection failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("SSH connection timed out")
            return False
        except Exception as e:
            self.logger.error(f"SSH test failed: {e}")
            return False

    def _get_file_hash(self, file_path: str, chunk_size: int = 65536) -> Optional[str]:
        """Calculate MD5 hash of entire file (matching remote md5sum behavior)."""
        try:
            hash_md5 = hashlib.md5()
            
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    hash_md5.update(chunk)
            
            return hash_md5.hexdigest()
        
        except Exception as e:
            self.logger.debug(f"Could not hash file {file_path}: {e}")
            return None


    def _check_remote_file_exists(self, remote_path: str) -> Tuple[bool, Optional[int]]:
        """Check if a file exists on remote host and get its size"""
        cmd = [
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes',
            f"{self.config['dest_user']}@{self.config['dest_host']}",
            f'stat -c "%s" "{remote_path}" 2>/dev/null || echo "NOTFOUND"'
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15, text=True)
            output = result.stdout.strip()
            
            if output == "NOTFOUND" or result.returncode != 0:
                return False, None
            else:
                try:
                    size = int(output)
                    return True, size
                except ValueError:
                    return False, None
                    
        except Exception as e:
            self.logger.debug(f"Error checking remote file {remote_path}: {e}")
            return False, None

    def _detect_renames(self, source_dir: str, dest_full_path: str) -> List[Tuple[str, str]]:
        """Detect renamed files by comparing content hashes"""
        self.logger.debug(f"Detecting renames in {source_dir}")
        self.logger.debug(f"Checking destination path: {dest_full_path}")
        renames = []
        
        try:
            # Get all files in source directory
            source_files: Dict[str, List[str]] = {}
            for root, dirs, files in os.walk(source_dir):
                if not self.running:
                    break

                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, source_dir)
                    file_hash = self._get_file_hash(file_path)
                    if file_hash:
                        # store all relative paths for the same hash
                        source_files.setdefault(file_hash, []).append(rel_path)

            
            # Check for matching files in destination with different names
            # Get list of destination files
            cmd = [
                'ssh', '-o', 'BatchMode=yes',
                f"{self.config['dest_user']}@{self.config['dest_host']}",
                f'find "{dest_full_path}" -type f 2>/dev/null || true'
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
            
            if result.returncode == 0:
                dest_files = result.stdout.strip().split('\n')
                dest_files = [f for f in dest_files if f.strip()]
                self.logger.debug(f"Found {len(dest_files)} files in destination directory")
                
                for dest_file in dest_files:
                    if not self.running:
                        break
                        
                    # Get relative path
                    rel_dest = dest_file.replace(dest_full_path + '/', '')
                    
                    # Skip if file exists in source with same name
                    source_equivalent = os.path.join(source_dir, rel_dest)
                    if os.path.exists(source_equivalent):
                        continue
                    
                    # Get hash of destination file
                    hash_cmd = [
                        'ssh', '-o', 'BatchMode=yes',
                        f"{self.config['dest_user']}@{self.config['dest_host']}",
                        f'md5sum "{dest_file}" 2>/dev/null | cut -d" " -f1 || echo "ERROR"'
                    ]
                    
                    hash_result = subprocess.run(hash_cmd, capture_output=True, timeout=30, text=True)
                    
                    if hash_result.returncode == 0:
                        dest_hash = hash_result.stdout.strip()
                        
                        if dest_hash in source_files and dest_hash != "ERROR":
                            # pick the first matching relative path
                            source_rel = source_files[dest_hash][0]
                            self.logger.info(f"Detected rename: {rel_dest} -> {source_rel}")
                            renames.append((dest_file, source_rel))
            
        except Exception as e:
            self.logger.warning(f"Error detecting renames: {e}")
        
        return renames

    def _handle_renames(self, renames: List[Tuple[str, str]], dest_full_path: str):
        """Handle detected file renames on destination"""
        for old_path, new_rel_path in renames:
            if not self.running:
                break
                
            new_path = f"{dest_full_path}/{new_rel_path}"
            
            # Create destination directory if needed
            new_dir = os.path.dirname(new_path)
            mkdir_cmd = [
                'ssh', '-o', 'BatchMode=yes',
                f"{self.config['dest_user']}@{self.config['dest_host']}",
                f'mkdir -p "{new_dir}"'
            ]
            
            subprocess.run(mkdir_cmd, capture_output=True, timeout=30)
            
            # Perform the rename
            self.logger.info(f"RENAME: {old_path} -> {new_path}")
            
            rename_cmd = [
                'ssh', '-o', 'BatchMode=yes',
                f"{self.config['dest_user']}@{self.config['dest_host']}",
                f'mv "{old_path}" "{new_path}"'
            ]
            
            result = subprocess.run(rename_cmd, capture_output=True, timeout=60, text=True)
            
            if result.returncode == 0:
                self.logger.info(f"Successfully renamed file on destination")
            else:
                self.logger.error(f"Failed to rename file: {result.stderr}")

    def _sync_directory(self, source_path: str) -> bool:
        """Sync a single directory with rename detection"""
        if not self.running:
            return False
            
        self.logger.info(f"Starting sync of directory: {source_path}")
        self.state["current_directory"] = source_path
        self._save_state()
        
        # Extract the relative path for destination
        path_parts = Path(source_path).parts
        media_index = path_parts.index("Media")
        rel_path = "/".join(path_parts[media_index:])
        destination_path = f"{self.config['dest_user']}@{self.config['dest_host']}:{self.config['dest_base_path']}/{'/'.join(path_parts[media_index+1:])}"
        dest_full_path = f"{self.config['dest_base_path']}/{'/'.join(path_parts[media_index+1:])}"
        
        # Detect and handle renames first
        renames = self._detect_renames(source_path, dest_full_path)
        if renames:
            self._handle_renames(renames, dest_full_path)
        
        # Build rsync command
        cmd = [
            'rsync',
            '-avh',
            "--checksum",
            '--progress',
            '--delete',
            f'--bwlimit={self.config["bandwidth_limit_kbps"]}',
            '--stats',
            '--timeout=300'
        ]
        
        if self.config["rsync_partial"]:
            cmd.append('--partial')
        
        if self.config["rsync_compress"]:
            cmd.append('-z')
        
        cmd.extend([f'{source_path}/', destination_path])
        
        # Perform sync with retries
        for attempt in range(1, self.config["max_retries"] + 1):
            if not self.running:
                return False
                
            self.logger.info(f"SYNC: Attempt {attempt}/{self.config['max_retries']} for {source_path}")
            
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Monitor progress and log current file being transferred
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output and self.running:
                        line = output.strip()
                        if line and not line.startswith('sending incremental'):
                            # Log file being transferred
                            if '/' in line and not line.endswith('/'):
                                self.logger.debug(f"TRANSFER: {line}")
                            elif 'to-chk=' in line:
                                self.logger.debug(f"PROGRESS: {line}")
                
                return_code = process.poll()
                stderr_output = process.stderr.read()
                
                if return_code == 0:
                    self.logger.info(f"Successfully synced {source_path}")
                    return True
                else:
                    self.logger.error(f"Sync failed (attempt {attempt}) - exit code: {return_code}")
                    if stderr_output:
                        self.logger.error(f"Error output: {stderr_output}")
                    
                    if attempt < self.config["max_retries"] and self.running:
                        self.logger.info(f"Waiting {self.config['retry_delay']} seconds before retry...")
                        time.sleep(self.config["retry_delay"])
                        
            except Exception as e:
                self.logger.error(f"Sync attempt {attempt} failed with exception: {e}")
                if attempt < self.config["max_retries"] and self.running:
                    time.sleep(self.config["retry_delay"])
        
        self.logger.error(f"All sync attempts failed for {source_path}")
        return False

    def _get_subdirectories(self, path: str) -> List[str]:
        """Get all subdirectories in a path"""
        subdirs = []
        try:
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    subdirs.append(item_path)
        except Exception as e:
            self.logger.error(f"Error reading directory {path}: {e}")
        
        return sorted(subdirs)

    def run(self):
        """Main execution method"""
        try:
            self.logger.info("=== Emby Sync Script Started ===")
            self.logger.info(f"Configuration loaded from: {self.config_path}")
            self.logger.info(f"Bandwidth limit: {self.config['bandwidth_limit_kbps']} KB/s")
            self.logger.info(f"Target directories: {len(self.config['source_paths'])}")
            
            # Check for running instance
            if not self._check_lock():
                sys.exit(1)
            
            # Create lock file
            self._create_lock()
            
            # Test SSH connection
            if not self._test_ssh_connection():
                self.logger.error("SSH connection failed. Exiting.")
                sys.exit(1)
            
            # Process each source path
            for source_path in self.config["source_paths"]:
                if not self.running:
                    self.logger.info("Shutdown requested, stopping sync")
                    break
                    
                if not os.path.exists(source_path):
                    self.logger.warning(f"Source path does not exist: {source_path}")
                    continue
                
                self.logger.info(f"Processing source path: {source_path}")
                
                # Get all subdirectories
                subdirs = self._get_subdirectories(source_path)
                
                if not subdirs:
                    # If no subdirectories, sync the path itself
                    self.logger.info(f"No subdirectories found, syncing entire path: {source_path}")
                    success = self._sync_directory(source_path)
                    
                    if success:
                        if source_path not in self.state["completed_directories"]:
                            self.state["completed_directories"].append(source_path)
                    else:
                        if source_path not in self.state["failed_directories"]:
                            self.state["failed_directories"].append(source_path)
                else:
                    # Process each subdirectory
                    for subdir in subdirs:
                        if not self.running:
                            break
                            
                        # Skip if already completed
                        if subdir in self.state["completed_directories"]:
                            self.logger.info(f"Skipping already completed directory: {subdir}")
                            continue
                        
                        success = self._sync_directory(subdir)
                        
                        if success:
                            if subdir not in self.state["completed_directories"]:
                                self.state["completed_directories"].append(subdir)
                            # Remove from failed list if it was there
                            if subdir in self.state["failed_directories"]:
                                self.state["failed_directories"].remove(subdir)
                        else:
                            if subdir not in self.state["failed_directories"]:
                                self.state["failed_directories"].append(subdir)
                        
                        self._save_state()
            
            # Update final state
            self.state["last_sync_time"] = datetime.now().isoformat()
            self.state["current_directory"] = None
            self._save_state()
            
            if self.running:
                completed = len(self.state["completed_directories"])
                failed = len(self.state["failed_directories"])
                self.logger.info(f"=== Sync Complete ===")
                self.logger.info(f"Completed directories: {completed}")
                self.logger.info(f"Failed directories: {failed}")
                
                if failed > 0:
                    self.logger.warning(f"Failed directories: {self.state['failed_directories']}")
                    sys.exit(1)
            else:
                self.logger.info("Sync interrupted by shutdown signal")
                sys.exit(0)
                
        except KeyboardInterrupt:
            self.logger.info("Sync interrupted by user")
            sys.exit(1)
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}", exc_info=True)
            sys.exit(1)
        finally:
            self._remove_lock()
            self.logger.info("=== Emby Sync Script Ended ===")

if __name__ == "__main__":
    try:
        config_path = "./embysyncconfig_test.json"
        if len(sys.argv) > 1:
            config_path = sys.argv[1]
        
        sync = EmbySync(config_path)
        sync.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)