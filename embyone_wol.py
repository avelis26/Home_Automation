import socket
import struct

def wake_on_lan(mac_address):
    # Remove separators and convert to bytes
    mac = mac_address.replace(':', '').replace('-', '')
    mac_bytes = bytes.fromhex(mac)
    
    # Build magic packet: 6 bytes of FF + MAC repeated 16 times
    packet = b'\xff' * 6 + mac_bytes * 16
    
    # Send via UDP broadcast
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(packet, ('255.255.255.255', 9))
    sock.close()
    print(f"Magic packet sent to {mac_address}")

if __name__ == "__main__":
    wake_on_lan("1C:87:2C:60:39:36")
