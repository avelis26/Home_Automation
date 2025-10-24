$mac = "1C:87:2C:60:39:36"
$macBytes = $mac -split '[:-]' | ForEach-Object { [byte]('0x' + $_) }
$packet = [byte[]](,0xFF * 6) + ($macBytes * 16)
$udp = New-Object System.Net.Sockets.UdpClient
$udp.Connect(([System.Net.IPAddress]::Broadcast), 9)
$udp.Send($packet, $packet.Length)
$udp.Close()