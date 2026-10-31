import unittest
import struct
import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gateway.telemetry_gateway import (
    calculate_crc16_ccitt,
    SyntheticIoTNode,
    LocalStoreAndForwardDB,
    VITAL_PACKET_MAGIC
)

class TestIoTGateway(unittest.TestCase):

    def test_crc16_integrity(self):
        test_data = b"Cisco-IoT-Edge-Vital-Gateway"
        crc1 = calculate_crc16_ccitt(test_data)
        crc2 = calculate_crc16_ccitt(test_data)
        self.assertEqual(crc1, crc2)
        self.assertNotEqual(crc1, 0)

        # Bit flip test
        corrupted = bytearray(test_data)
        corrupted[0] ^= 0x01
        self.assertNotEqual(calculate_crc16_ccitt(bytes(corrupted)), crc1)

    def test_sensor_sample_generation(self):
        node = SyntheticIoTNode(node_id=1, base_hr=75.0, base_spo2=99.0)
        packet_bytes = node.generate_sample(timestamp_ms=1000)
        self.assertEqual(len(packet_bytes), 16)
        
        # Unpack header
        magic, node_id, seq, ts, red, ir, temp, crc = struct.unpack("<HBBihhhH", packet_bytes)
        self.assertEqual(magic, VITAL_PACKET_MAGIC)
        self.assertEqual(node_id, 1)
        self.assertEqual(seq, 1)
        self.assertEqual(ts, 1000)
        self.assertTrue(red > 0 and ir > 0)

        # Validate CRC verification
        calc_crc = calculate_crc16_ccitt(packet_bytes[:-2])
        self.assertEqual(calc_crc, crc)

    def test_fault_injection(self):
        node = SyntheticIoTNode(node_id=2)
        node.corrupt_rate = 1.0  # Force corruption
        packet_bytes = node.generate_sample(timestamp_ms=2000)
        calc_crc = calculate_crc16_ccitt(packet_bytes[:-2])
        _, _, _, _, _, _, _, crc = struct.unpack("<HBBihhhH", packet_bytes)
        self.assertNotEqual(calc_crc, crc)

    def test_sqlite_store_and_forward(self):
        db = LocalStoreAndForwardDB(":memory:")
        db.insert_record(node_id=1, seq=1, ts_ms=100, red=10500, ir=10800, temp_c=36.7, hr=72.5, spo2=98.5, anomaly=False)
        db.insert_record(node_id=1, seq=2, ts_ms=200, red=10520, ir=10810, temp_c=36.8, hr=73.0, spo2=98.4, anomaly=False)
        db.insert_record(node_id=1, seq=3, ts_ms=300, red=10550, ir=10830, temp_c=37.0, hr=125.0, spo2=91.0, anomaly=True)

        records = db.query_recent(node_id=1, limit=10)
        self.assertEqual(len(records), 3)
        self.assertTrue(records[-1]["anomaly"])
        self.assertEqual(records[-1]["hr"], 125.0)

if __name__ == "__main__":
    unittest.main()
