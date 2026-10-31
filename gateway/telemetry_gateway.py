"""
Telemetry Gateway & Synthetic IoT Sensor Node Engine
===================================================
Author: Hossein Nosrati (hosseinnosrati)
Architecture: AsyncIO + MQTT / WebSocket + SQLite Local Store-and-Forward
"""

import struct
import time
import math
import random
import asyncio
import sqlite3
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

VITAL_PACKET_MAGIC = 0xAA55
VITAL_PACKET_FORMAT = "<HHBBihhhH"  # Struct packing format
PACKET_SIZE = 18

CRC16_POLYNOMIAL = 0x1021

def calculate_crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC16_POLYNOMIAL) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

@dataclass
class TelemetryFrame:
    magic: int
    node_id: int
    sequence_num: int
    timestamp_ms: int
    raw_red: int
    raw_ir: int
    temperature_cx10: int
    crc16: int

    def pack(self) -> bytes:
        # Exclude crc16 for payload hashing
        payload = struct.pack(
            "<HBBihhh",
            self.magic,
            self.node_id,
            self.sequence_num,
            self.timestamp_ms,
            self.raw_red,
            self.raw_ir,
            self.temperature_cx10
        )
        calculated_crc = calculate_crc16_ccitt(payload)
        return payload + struct.pack("<H", calculated_crc)

    @classmethod
    def unpack(cls, raw_bytes: bytes) -> Optional["TelemetryFrame"]:
        if len(raw_bytes) != 14:  # 2 + 1 + 1 + 4 + 2 + 2 + 2 = 14 bytes + 2 bytes crc = 16
            pass
        try:
            magic, node_id, seq, ts, red, ir, temp, crc = struct.unpack("<HBBihhhH", raw_bytes)
            # Verify CRC
            computed_crc = calculate_crc16_ccitt(raw_bytes[:-2])
            if computed_crc != crc:
                return None
            return cls(magic, node_id, seq, ts, red, ir, temp, crc)
        except Exception:
            return None

class SyntheticIoTNode:
    """Simulates an edge wearable device measuring Photoplethysmogram (PPG) and temperature."""
    def __init__(self, node_id: int, base_hr: float = 72.0, base_spo2: float = 98.0, base_temp: float = 36.8):
        self.node_id = node_id
        self.base_hr = base_hr
        self.base_spo2 = base_spo2
        self.base_temp = base_temp
        self.sequence_num = 0
        self.phase = random.uniform(0, 2 * math.pi)
        self.drop_rate = 0.0
        self.corrupt_rate = 0.0

    def generate_sample(self, timestamp_ms: int) -> bytes:
        self.sequence_num = (self.sequence_num + 1) % 256
        t_sec = timestamp_ms / 1000.0

        # PPG Pulse synthesis
        hr_hz = self.base_hr / 60.0
        ppg_fundamental = math.sin(2 * math.pi * hr_hz * t_sec + self.phase)
        ppg_dicrotic = 0.35 * math.sin(4 * math.pi * hr_hz * t_sec + self.phase + 0.8)
        ppg_signal = ppg_fundamental + ppg_dicrotic

        # Optical absorption ratio for SpO2
        # R = (AC_red / DC_red) / (AC_ir / DC_ir)
        # SpO2 ~ 110 - 25 * R
        target_r = (110.0 - self.base_spo2) / 25.0
        dc_ir = 10000
        dc_red = 10000
        ac_ir = int(800 * ppg_signal + random.gauss(0, 20))
        ac_red = int(800 * target_r * ppg_signal + random.gauss(0, 20))

        raw_ir = dc_ir + ac_ir
        raw_red = dc_red + ac_red
        temp_cx10 = int((self.base_temp + 0.1 * math.sin(0.05 * t_sec)) * 10)

        payload = struct.pack(
            "<HBBihhh",
            VITAL_PACKET_MAGIC,
            self.node_id,
            self.sequence_num,
            timestamp_ms,
            raw_red,
            raw_ir,
            temp_cx10
        )
        crc = calculate_crc16_ccitt(payload)

        # Fault injection
        if random.random() < self.corrupt_rate:
            crc ^= 0x1234  # Invalidate CRC

        return payload + struct.pack("<H", crc)

class LocalStoreAndForwardDB:
    """Embedded SQLite fallback for zero-loss offline store-and-forward telemetry."""
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id INTEGER,
                    seq INTEGER,
                    ts_ms INTEGER,
                    raw_red INTEGER,
                    raw_ir INTEGER,
                    temp_c REAL,
                    hr_bpm REAL,
                    spo2 REAL,
                    is_anomaly INTEGER,
                    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def insert_record(self, node_id: int, seq: int, ts_ms: int, red: int, ir: int, 
                      temp_c: float, hr: float, spo2: float, anomaly: bool):
        with self.conn:
            self.conn.execute("""
                INSERT INTO telemetry_log (node_id, seq, ts_ms, raw_red, raw_ir, temp_c, hr_bpm, spo2, is_anomaly)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (node_id, seq, ts_ms, red, ir, temp_c, hr, spo2, int(anomaly)))

    def query_recent(self, node_id: int, limit: int = 50) -> List[Dict]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT node_id, seq, ts_ms, raw_red, raw_ir, temp_c, hr_bpm, spo2, is_anomaly, logged_at
            FROM telemetry_log
            WHERE node_id = ?
            ORDER BY id DESC LIMIT ?
        """, (node_id, limit))
        rows = cur.fetchall()
        return [
            {
                "node_id": r[0], "seq": r[1], "ts_ms": r[2], "red": r[3], "ir": r[4],
                "temp_c": r[5], "hr": r[6], "spo2": r[7], "anomaly": bool(r[8]), "time": r[9]
            }
            for r in reversed(rows)
        ]
