#!/bin/bash
#/usr/local/bin/rotate-emby-log.sh
#sudo chmod +x /usr/local/bin/rotate-emby-log.sh
#sudo chown avelis:avelis /usr/local/bin/rotate-emby-log.sh

LOG_FILE="/var/log/emby-sync.log"
BACKUP_DIR="/var/log"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

# Copy and compress the log
cp "$LOG_FILE" "${BACKUP_DIR}/emby-sync_${TIMESTAMP}.log"
gzip "${BACKUP_DIR}/emby-sync_${TIMESTAMP}.log"

# Clear the original log
> "$LOG_FILE"

# Keep only last 7 compressed logs
cd "$BACKUP_DIR"
ls -t emby-sync_*.log.gz | tail -n +8 | xargs -r rm