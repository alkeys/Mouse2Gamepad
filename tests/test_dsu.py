import socket
import struct
import zlib

from mouse2gamepad.dsu import DSUServer


def make_client_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(1.0)
    return sock


def build_client_packet(msg_type, extra=b""):
    payload = struct.pack("<I", msg_type) + extra
    header = struct.pack("<4sHHII", b"DSUC", 1001, len(payload), 0, 0xDEADBEEF)
    return header + payload


def test_packet_crc_is_valid():
    server = DSUServer(0)
    try:
        pkt = server._packet(DSUServer.MSG_VERSION, struct.pack("<H", 1001))
        stated_crc = struct.unpack_from("<I", pkt, 8)[0]
        zeroed = pkt[:8] + b"\x00\x00\x00\x00" + pkt[12:]
        assert zlib.crc32(zeroed) & 0xFFFFFFFF == stated_crc
    finally:
        server.close()


def test_version_request_gets_version_response():
    server = DSUServer(0)
    try:
        port = server.sock.getsockname()[1]
        client = make_client_socket()
        client.sendto(build_client_packet(DSUServer.MSG_VERSION), ("127.0.0.1", port))
        server.handle_incoming()
        data, _ = client.recvfrom(1024)
        assert data[:4] == b"DSUS"
        assert struct.unpack_from("<I", data, 16)[0] == DSUServer.MSG_VERSION
    finally:
        server.close()


def test_ports_request_for_connected_slot_0():
    server = DSUServer(0)
    try:
        port = server.sock.getsockname()[1]
        client = make_client_socket()
        extra = struct.pack("<i", 1) + bytes([0])  # count=1, slot 0
        client.sendto(build_client_packet(DSUServer.MSG_PORTS, extra), ("127.0.0.1", port))
        server.handle_incoming()
        data, _ = client.recvfrom(1024)
        assert struct.unpack_from("<I", data, 16)[0] == DSUServer.MSG_PORTS
    finally:
        server.close()


def test_data_request_registers_client_and_send_motion_delivers_packet():
    server = DSUServer(0)
    try:
        port = server.sock.getsockname()[1]
        client = make_client_socket()
        client.sendto(build_client_packet(DSUServer.MSG_DATA), ("127.0.0.1", port))
        server.handle_incoming()
        assert len(server.clients) == 1

        server.send_motion(pitch=1.5, yaw=2.5, roll=3.5)
        data, _ = client.recvfrom(1024)
        assert struct.unpack_from("<I", data, 16)[0] == DSUServer.MSG_DATA
        # pitch/yaw/roll son siempre los últimos 12 bytes del paquete (3 floats).
        pitch, yaw, roll = struct.unpack_from("<fff", data, len(data) - 12)
        assert (pitch, yaw, roll) == (1.5, 2.5, 3.5)
    finally:
        server.close()


def test_send_motion_without_clients_does_not_raise():
    server = DSUServer(0)
    try:
        server.send_motion(pitch=0.0, yaw=0.0, roll=0.0)
    finally:
        server.close()
