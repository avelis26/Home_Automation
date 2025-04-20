#!/usr/bin/env python3

import paramiko
import hashlib
import os
import sys
import logging
import time
import argparse

# Configure logging
logging.basicConfig(
    filename='/var/log/embymove.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y%m%d_%H%M'
)

def calculate_file_hash(file_path, ssh_client=None):
    """Calculate SHA256 hash of a file, handling both local and remote files."""
    sha256 = hashlib.sha256()
    if ssh_client:  # Remote file
        sftp = ssh_client.open_sftp()
        try:
            with sftp.file(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
        finally:
            sftp.close()
    else:  # Local file
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
    return sha256.hexdigest()

def copy_file(input_path, output_path, dest_user, dest_server, max_retries=10):
    """Copy file with retry mechanism and verification."""
    # Construct destination path for SSH
    dest_path = f"{dest_user}@{dest_server}:{output_path}"
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    attempt = 0
    success = False
    
    # Log start of operation
    logging.info(f"EmbyMove - SSH copy operation started")
    
    while attempt < max_retries and not success:
        attempt += 1
        try:
            # Calculate source file hash (local file on embyone)
            source_hash = calculate_file_hash(input_path)
            
            # Connect to destination server
            ssh_client.connect(dest_server, username=dest_user)
            sftp = ssh_client.open_sftp()
            
            try:
                # Ensure destination directory exists
                dest_dir = os.path.dirname(output_path)
                try:
                    sftp.stat(dest_dir)
                except IOError:
                    # Create directory if it doesn't exist
                    sftp.mkdir(dest_dir)
                
                # Copy file
                sftp.put(input_path, output_path)
                
                # Verify copy by comparing hashes
                dest_hash = calculate_file_hash(output_path, ssh_client)
                
                if source_hash == dest_hash:
                    success = True
                    logging.info(f"EmbyMove - SSH copy operation completed successfully")
                else:
                    logging.error("EmbyMove - Hash verification failed")
                    # Remove failed copy
                    try:
                        sftp.remove(output_path)
                    except:
                        pass
                
            finally:
                sftp.close()
                ssh_client.close()
                
        except Exception as e:
            logging.error(f"EmbyMove - SSH copy operation interrupted!!! Error: {str(e)}")
            time.sleep(300)  # Wait before retry
            if attempt < max_retries:
                logging.info(f"EmbyMove - Retrying attempt {attempt + 1}/{max_retries}")
            continue
    
    return success

def main():
    parser = argparse.ArgumentParser(description='Fault-tolerant SSH file copier')
    parser.add_argument('--user', required=True, help='Destination username')
    parser.add_argument('--server', required=True, help='Destination server hostname')
    parser.add_argument('--input', required=True, help='Source file path (local on embyone)')
    parser.add_argument('--output', required=True, help='Destination path (on remote server)')
    args = parser.parse_args()
    
    if copy_file(args.input, args.output, args.user, args.server):
        print("File copied successfully")
        sys.exit(0)
    else:
        print("Failed to copy file after maximum retries")
        sys.exit(1)

if __name__ == "__main__":
    main()