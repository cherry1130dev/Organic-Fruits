/**
 * ============================================================================
 * IoT Pesticide Detection - Firmware Configuration
 * ESP32-CAM + ADS1115 + MQ-2 + MQ-135 + Buzzer
 * ============================================================================
 */

#ifndef CONFIG_H
#define CONFIG_H

// --- WiFi Settings ---
#define WIFI_SSID         "YOUR_WIFI_SSID"
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"
#define WIFI_TIMEOUT_MS   15000

// --- Laptop / Flask Backend Server ---
// Replace with your laptop's local LAN IP (e.g., 192.168.1.150)
#define SERVER_HOST       "192.168.1.100"
#define SERVER_PORT       5000
#define ANALYZE_ENDPOINT  "/api/analyze"
#define HTTP_TIMEOUT_MS   12000

// --- I2C Pins for ADS1115 ---
// CRITICAL: ESP32-CAM ADC2 pins fail when WiFi is active.
// Using software/hardware I2C on GPIO14 (SDA) and GPIO15 (SCL) bypasses this completely.
#define I2C_SDA_PIN       14
#define I2C_SCL_PIN       15
#define ADS1115_I2C_ADDR  0x48

// ADS1115 Input Channels
#define ADS_CH_MQ2        0 // Analog channel A0: MQ-2
#define ADS_CH_MQ135      1 // Analog channel A1: MQ-135

// --- Actuator & Indicator Pins ---
#define BUZZER_PIN        12 // Piezo buzzer (Active HIGH)
#define LED_ALERT_PIN     13 // Red alert LED (Active HIGH)
#define BUTTON_TRIGGER_PIN 2 // External push-button (Active LOW with internal pull-up)

// --- Sensor Heating & Sampling ---
// MQ sensors require 24-48h initial burn-in and 3-5 min operational warm-up
#define QUICK_WARMUP_SEC  25  // Warm-up countdown on boot (seconds)
#define SENSOR_SAMPLES     5  // Number of ADS1115 samples to average per reading
#define SAMPLE_DELAY_MS   100 // Delay between successive samples (ms)

// --- Operation Trigger Mode ---
// MODE_BUTTON: wait for push button press on BUTTON_TRIGGER_PIN
// MODE_TIMER: trigger automatically every TIMER_INTERVAL_SEC
#define MODE_BUTTON       1
#define MODE_TIMER        2
#define OPERATION_MODE    MODE_TIMER
#define TIMER_INTERVAL_SEC 15 // Time between automatic scans in timer mode

// --- Produce Under Inspection ---
#define TARGET_PRODUCE    "Apple"

// --- Deep Sleep Option ---
#define ENABLE_DEEP_SLEEP false
#define SLEEP_DURATION_SEC 60

#endif // CONFIG_H
