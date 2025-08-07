#!/bin/bash
#/usr/local/bin/rotate-emby-log.sh
#sudo chmod +x /usr/local/bin/rotate-emby-log.sh
#sudo chown avelis:avelis /usr/local/bin/rotate-emby-log.sh

LOG_FILE="/var/log/emby-sync.log"
BACKUP_DIR="/var/log"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

echo $LOG_FILE
echo $BACKUP_DIR
echo $TIMESTAMP

# Copy and compress the log
echo "cp $LOG_FILE" "${BACKUP_DIR}/emby-sync_${TIMESTAMP}.log"
sudo cp "$LOG_FILE" "${BACKUP_DIR}/emby-sync_${TIMESTAMP}.log"
sudo gzip "${BACKUP_DIR}/emby-sync_${TIMESTAMP}.log"

# Clear the original log
echo "Emby Sync Log: ${TIMESTAMP}" > "$LOG_FILE"

# Keep only last 7 compressed logs
cd "$BACKUP_DIR"
ls -t emby-sync_*.log.gz | tail -n +8 | sudo xargs -r rm