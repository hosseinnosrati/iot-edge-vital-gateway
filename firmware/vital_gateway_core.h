/**
 * @file vital_gateway_core.h
 * @brief Zero-Allocation Embedded C99 Core for IoT Vital Telemetry & Edge DSP.
 * @author Hossein Nosrati (hosseinnosrati)
 * 
 * Target: Low-power ARM Cortex-M0+/M4/M33 and ESP32 IoT Nodes.
 * Characteristics:
 *   - Strictly 0 bytes dynamic heap allocation (malloc/free free).
 *   - Ring-buffer based bounded sliding-window conditioning.
 *   - Online PPG/ECG AC/DC extraction, Ratio-of-Ratios SpO2 calculation.
 *   - Embedded CRC-16-CCITT packet framing and data integrity verification.
 *   - Fixed-point deterministic anomaly alarm triggering.
 */

#ifndef VITAL_GATEWAY_CORE_H
#define VITAL_GATEWAY_CORE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define VITAL_WINDOW_SIZE 128
#define VITAL_MAX_NODES   8
#define VITAL_PACKET_MAGIC 0xAA55

#pragma pack(push, 1)
typedef struct {
    uint16_t magic;          /* 0xAA55 frame identifier */
    uint8_t  node_id;        /* Node ID (1..VITAL_MAX_NODES) */
    uint8_t  sequence_num;   /* Monotonic packet sequence counter */
    uint32_t timestamp_ms;   /* Node uptime timestamp */
    int16_t  raw_red;        /* Raw optical Red photodiode channel */
    int16_t  raw_ir;         /* Raw optical IR photodiode channel */
    int16_t  temperature_cx10; /* Sensor skin temperature in 0.1 deg C */
    uint16_t crc16;          /* CRC-16-CCITT over payload */
} VitalTelemetryPacket;

typedef struct {
    uint8_t  node_id;
    float    heart_rate_bpm;
    float    spo2_percentage;
    float    perfusion_index;
    float    temperature_c;
    bool     arrhythmia_flag;
    bool     hypoxemia_flag;
    uint32_t packets_received;
    uint32_t packets_corrupted;
} VitalNodeDiagnostics;
#pragma pack(pop)

typedef struct {
    int16_t buffer[VITAL_WINDOW_SIZE];
    uint16_t head;
    uint16_t count;
    int32_t  running_sum;
} VitalRingBuffer;

typedef struct {
    uint8_t node_id;
    bool active;
    VitalRingBuffer red_buf;
    VitalRingBuffer ir_buf;
    int32_t last_peak_timestamp;
    float current_hr;
    float current_spo2;
    uint32_t total_packets;
    uint32_t crc_errors;
} VitalNodeState;

typedef struct {
    VitalNodeState nodes[VITAL_MAX_NODES];
    uint8_t active_nodes_count;
} VitalGatewayRegistry;

/* Core Function Prototypes */
void vital_core_init(VitalGatewayRegistry *registry);
uint16_t vital_crc16_ccitt(const uint8_t *data, size_t length);
bool vital_packet_validate(const VitalTelemetryPacket *packet);
bool vital_process_packet(VitalGatewayRegistry *registry, 
                          const VitalTelemetryPacket *packet, 
                          VitalNodeDiagnostics *out_diag);
void vital_ring_buffer_push(VitalRingBuffer *rb, int16_t sample);
int16_t vital_ring_buffer_mean(const VitalRingBuffer *rb);
void vital_ring_buffer_ac_dc(const VitalRingBuffer *rb, float *out_ac, float *out_dc);

#ifdef __cplusplus
}
#endif

#endif /* VITAL_GATEWAY_CORE_H */
