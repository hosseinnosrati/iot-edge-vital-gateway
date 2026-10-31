/**
 * @file vital_gateway_core.c
 * @brief Implementation of Zero-Malloc C99 Vital Telemetry Gateway DSP Core.
 * @author Hossein Nosrati (hosseinnosrati)
 */

#include "vital_gateway_core.h"
#include <string.h>
#include <math.h>

#define CRC16_POLYNOMIAL 0x1021

uint16_t vital_crc16_ccitt(const uint8_t *data, size_t length) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < length; i++) {
        crc ^= (uint16_t)(data[i] << 8);
        for (uint8_t bit = 0; bit < 8; bit++) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ CRC16_POLYNOMIAL;
            } else {
                crc = crc << 1;
            }
        }
    }
    return crc;
}

void vital_core_init(VitalGatewayRegistry *registry) {
    if (!registry) return;
    memset(registry, 0, sizeof(VitalGatewayRegistry));
    registry->active_nodes_count = 0;
}

void vital_ring_buffer_push(VitalRingBuffer *rb, int16_t sample) {
    if (!rb) return;
    if (rb->count == VITAL_WINDOW_SIZE) {
        rb->running_sum -= rb->buffer[rb->head];
    } else {
        rb->count++;
    }
    rb->buffer[rb->head] = sample;
    rb->running_sum += sample;
    rb->head = (rb->head + 1) % VITAL_WINDOW_SIZE;
}

int16_t vital_ring_buffer_mean(const VitalRingBuffer *rb) {
    if (!rb || rb->count == 0) return 0;
    return (int16_t)(rb->running_sum / (int32_t)rb->count);
}

void vital_ring_buffer_ac_dc(const VitalRingBuffer *rb, float *out_ac, float *out_dc) {
    if (!rb || rb->count < 4) {
        if (out_ac) *out_ac = 0.0f;
        if (out_dc) *out_dc = 1.0f;
        return;
    }
    float dc = (float)rb->running_sum / (float)rb->count;
    int16_t min_val = 32767;
    int16_t max_val = -32768;
    for (uint16_t i = 0; i < rb->count; i++) {
        int16_t v = rb->buffer[i];
        if (v < min_val) min_val = v;
        if (v > max_val) max_val = v;
    }
    float ac = (float)(max_val - min_val);
    if (out_ac) *out_ac = ac;
    if (out_dc) *out_dc = (dc > 1.0f) ? dc : 1.0f;
}

bool vital_packet_validate(const VitalTelemetryPacket *packet) {
    if (!packet) return false;
    if (packet->magic != VITAL_PACKET_MAGIC) return false;
    if (packet->node_id == 0 || packet->node_id > VITAL_MAX_NODES) return false;
    
    /* Calculate CRC over packet excluding the crc16 field */
    size_t payload_len = sizeof(VitalTelemetryPacket) - sizeof(uint16_t);
    uint16_t calc_crc = vital_crc16_ccitt((const uint8_t *)packet, payload_len);
    return (calc_crc == packet->crc16);
}

bool vital_process_packet(VitalGatewayRegistry *registry, 
                          const VitalTelemetryPacket *packet, 
                          VitalNodeDiagnostics *out_diag) {
    if (!registry || !packet || !out_diag) return false;

    uint8_t idx = packet->node_id - 1;
    if (idx >= VITAL_MAX_NODES) return false;

    VitalNodeState *node = &registry->nodes[idx];
    if (!node->active) {
        node->active = true;
        node->node_id = packet->node_id;
        node->current_hr = 72.0f;
        node->current_spo2 = 98.0f;
        registry->active_nodes_count++;
    }

    node->total_packets++;

    /* CRC verification */
    if (!vital_packet_validate(packet)) {
        node->crc_errors++;
        out_diag->node_id = packet->node_id;
        out_diag->packets_received = node->total_packets;
        out_diag->packets_corrupted = node->crc_errors;
        return false;
    }

    /* Push optical samples to sliding ring buffers */
    vital_ring_buffer_push(&node->red_buf, packet->raw_red);
    vital_ring_buffer_push(&node->ir_buf, packet->raw_ir);

    /* Extract AC and DC components */
    float red_ac, red_dc, ir_ac, ir_dc;
    vital_ring_buffer_ac_dc(&node->red_buf, &red_ac, &red_dc);
    vital_ring_buffer_ac_dc(&node->ir_buf, &ir_ac, &ir_dc);

    /* Ratio-of-Ratios (R) for SpO2 calibration */
    float r = (red_ac / red_dc) / ((ir_ac / ir_dc) + 1e-5f);
    float estimated_spo2 = 110.0f - 25.0f * r;
    if (estimated_spo2 > 100.0f) estimated_spo2 = 100.0f;
    if (estimated_spo2 < 70.0f) estimated_spo2 = 70.0f;

    /* Perfusion Index (PI) */
    float pi = (ir_ac / ir_dc) * 100.0f;

    /* Heart rate estimation based on temporal peak intervals */
    int32_t dt = (int32_t)packet->timestamp_ms - node->last_peak_timestamp;
    if (packet->raw_ir > 1200 && dt > 400 && dt < 1800) {
        float instant_hr = 60000.0f / (float)dt;
        node->current_hr = 0.85f * node->current_hr + 0.15f * instant_hr;
        node->last_peak_timestamp = packet->timestamp_ms;
    }
    node->current_spo2 = 0.90f * node->current_spo2 + 0.10f * estimated_spo2;

    /* Fill output diagnostics */
    out_diag->node_id = packet->node_id;
    out_diag->heart_rate_bpm = node->current_hr;
    out_diag->spo2_percentage = node->current_spo2;
    out_diag->perfusion_index = pi;
    out_diag->temperature_c = (float)packet->temperature_cx10 / 10.0f;
    out_diag->arrhythmia_flag = (node->current_hr < 50.0f || node->current_hr > 120.0f);
    out_diag->hypoxemia_flag = (node->current_spo2 < 92.0f);
    out_diag->packets_received = node->total_packets;
    out_diag->packets_corrupted = node->crc_errors;

    return true;
}
