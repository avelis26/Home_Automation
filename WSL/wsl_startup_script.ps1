# WSL Emby Startup Script
# Run this at Windows startup to mount the drive and start services
# Place in: C:\Scripts\Start-WSL-Emby.ps1
# Run as Administrator via Task Scheduler

$ErrorActionPreference = "Stop"
$LogFile = Join-Path $PSScriptRoot "wsl-emby-startup.log"
$wslName = "Ubuntu"
function Write-Log {
    param($Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$timestamp - $Message" | Out-File -Append -FilePath $LogFile
    Write-Host $Message
}

try {
    Write-Log "Starting WSL Emby services..."
    
    # Wait for system to be ready
    Start-Sleep -Seconds 5
    
    # Check if drive is already mounted
    $mountCheck = wsl -d $wslName -u root test -d /mnt/wsl/PHYSICALDRIVE1p1 '&&' echo "mounted" '||' echo "not mounted"
    
    if ($mountCheck -notmatch "mounted") {
        Write-Log "Mounting ext4 drive..."
        # Mount the physical drive (adjust PHYSICALDRIVE number if needed)
        wsl --mount \\.\PHYSICALDRIVE1 --partition 1 --type ext4
        Start-Sleep -Seconds 3
        Write-Log "Drive mounted successfully"
    } else {
        Write-Log "Drive already mounted"
    }
    
    # Start WSL and services
    Write-Log "Starting WSL services..."
    wsl -d $wslName -u root -- systemctl start emby-server
    wsl -d $wslName -u root -- systemctl start smbd
    wsl -d $wslName -u root -- systemctl start transmission-daemon
    
    Write-Log "All services started successfully"
    
    # Display service status
    Write-Log "Service Status:"
    $embyStatus = wsl -d $wslName -u root -- systemctl is-active emby-server
    $sambaStatus = wsl -d $wslName -u root -- systemctl is-active smbd
    $transStatus = wsl -d $wslName -u root -- systemctl is-active transmission-daemon
    
    Write-Log "  Emby: $embyStatus"
    Write-Log "  Samba: $sambaStatus"
    Write-Log "  Transmission: $transStatus"
    
    # Get WSL IP for Emby access
    $wslIP = wsl -d $wslName hostname -I | ForEach-Object { $_.Trim() }
    Write-Log ""
    Write-Log "Access Emby at: http://$wslIP`:8096"
    Write-Log "Access Samba share at: \\wsl$\$wslName\mnt\wsl\PHYSICALDRIVE1p1"
    
} catch {
    Write-Log "ERROR: $_"
    exit 1
}
