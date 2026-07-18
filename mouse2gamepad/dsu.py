"""Servidor DSU (protocolo cemuhook) usado por Cemu para leer datos de motion."""

import random
import socket
import struct
import time
import zlib


class DSUServer:
    MSG_VERSION, MSG_PORTS, MSG_DATA = 0x100000, 0x100001, 0x100002

    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.setblocking(False)
        self.server_id = random.randint(0, 0xFFFFFFFF)
        self.clients = {}
        self.counter = 0
        self._last_prune = 0.0
        self.ctrl_header = struct.pack("<BBBB6sB", 0, 2, 2, 1,
                                       b"\x0a\x1b\x2c\x3d\x4e\x5f", 5)

    def _packet(self, msg_type, payload):
        data = struct.pack("<I", msg_type) + payload
        header = struct.pack("<4sHHII", b"DSUS", 1001, len(data), 0, self.server_id)
        pkt = header + data
        crc = zlib.crc32(pkt) & 0xFFFFFFFF
        return pkt[:8] + struct.pack("<I", crc) + pkt[12:]

    def handle_incoming(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (BlockingIOError, InterruptedError, OSError):
                return
            if len(data) < 20 or data[:4] != b"DSUC":
                continue
            msg = struct.unpack_from("<I", data, 16)[0]
            if msg == self.MSG_VERSION:
                self.sock.sendto(self._packet(self.MSG_VERSION,
                                              struct.pack("<H", 1001)), addr)
            elif msg == self.MSG_PORTS and len(data) >= 24:
                count = struct.unpack_from("<i", data, 20)[0]
                for i in range(min(count, 4)):
                    if 24 + i >= len(data):
                        break
                    port = data[24 + i]
                    if port == 0:
                        payload = self.ctrl_header + b"\x00"
                    else:
                        payload = struct.pack("<BBBB6sB", port, 0, 0, 0,
                                              b"\x00" * 6, 0) + b"\x00"
                    self.sock.sendto(self._packet(self.MSG_PORTS, payload), addr)
            elif msg == self.MSG_DATA:
                self.clients[addr] = time.time()

    def send_motion(self, pitch, yaw, roll=0.0, acc=(0.0, -1.0, 0.0)):
        now = time.time()
        if now - self._last_prune >= 1.0:
            self.clients = {a: t for a, t in self.clients.items() if now - t < 5.0}
            self._last_prune = now
        if not self.clients:
            return
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        payload = (self.ctrl_header + b"\x01"
                   + struct.pack("<I", self.counter)
                   + b"\x00\x00\x00\x00"
                   + bytes([128, 128, 128, 128])
                   + b"\x00" * 12 + b"\x00" * 12
                   + struct.pack("<Q", int(now * 1_000_000))
                   + struct.pack("<fff", *acc)
                   + struct.pack("<fff", pitch, yaw, roll))
        pkt = self._packet(self.MSG_DATA, payload)
        for addr in self.clients:
            try:
                self.sock.sendto(pkt, addr)
            except OSError:
                pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
