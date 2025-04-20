#!/usr/bin/env python3

import subprocess
import hashlib
import os
import sys
import logging
import time
import argparse
from datetime import datetime
from tzlocal import get_localzone

# Configure logging with local timezone
local_tz = get_localzone()
formatter = logging.Formatter(fmt='%(asctime)s - %(message)s', datefmt='%Y%m%d_%H%M')
formatter.converter = lambda *args: datetime.now(local_tz).timetuple()

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers = []
file_handler = logging.FileHandler('/var/log/embymove.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

def calculate_file_hash(file_path, is_remote=False, dest_user=None, dest_server=None):
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    if is_remote:
        cmd = f"ssh {dest_user}@{dest_server} 'cat {file_path}'"
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
        while chunk := process.stdout.read(8192):
            sha256.update(chunk)
        process.wait()
    else:
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
    return sha256.hexdigest()

def copy_file(input_path, output_path, dest_user, dest_server, max_retries=10):
    """Copy file using scp with retry and verification."""
    # Construct destination file path by appending source filename to output directory
    filename = os.path.basename(input_path)
    dest_file_path = os.path.join(output_path, filename)
    dest_path = f"{dest_user}@{dest_server}:{dest_file_path}"
    
    attempt = 0
    success = False
    
    logging.info("EmbyMove - SSH copy operation started")
    
    while attempt < max_retries and not success:
        attempt += 1
        try:
            # Calculate source hash
            source_hash = calculate_file_hash(input_path)
            
            # Ensure destination directory exists
            subprocess.run(
                f"ssh {dest_user}@{dest_server} 'mkdir -p {output_path}'",
                shell=True, check=True
            )
            
            # Copy file using scp with quiet mode
            logging.info(f"EmbyMove - Starting file transfer to {dest_path}")
            subprocess.run(
                f"scp -q {input_path} {dest_path}",
                shell=True, check=True
            )
            
            # Verify copy
            dest_hash = calculate_file_hash(dest_file_path, is_remote=True, dest_user=dest_user, dest_server=dest_server)
            
            if source_hash == dest_hash:
                success = True
                logging.info("EmbyMove - SSH copy operation completed successfully")
            else:
                logging.error("EmbyMove - Hash verification failed")
                subprocess.run(f"ssh {dest_user}@{dest_server} 'rm {dest_file_path}'", shell=True)
                
        except Exception as e:
            logging.error(f"EmbyMove - SSH copy operation interrupted!!! Error: {str(e)}")
            time.sleep(30)
            if attempt < max_retries:
                logging.info(f"EmbyMove - Retrying attempt {attempt + 1}/{max_retries}")
    
    return success

def main():
    parser = argparse.ArgumentParser(description='Fault-tolerant SSH file copier')
    parser.add_argument('--user', required=True, help='Destination username')
    parser.add_argument('--server', required=True, help='Destination server hostname')
    parser.add_argument('--input', required=True, help='Source file path (local on embyone)')
    parser.add_argument('--output', required=True, help='Destination directory path (on remote server)')
    args = parser.parse_args()
    
    if copy_file(args.input, args.output, args.user, args.server):
        print("File copied successfully")
        sys.exit(0)
    else:
        print("Failed to copy file after maximum retries")
        sys.exit(1)

if __name__ == "__main__":
    main()