/**
 * ============================================================================
 * IoT-Based Detection of Pesticide in Organic Fruits and Vegetables
 * Firmware for ESP32-CAM (AI-Thinker) + ADS1115 + MQ-2 + MQ-135
 * ============================================================================
 *
 * Required Libraries (Install via Arduino IDE Library Manager):
 * 1. "Adafruit ADS1X15" by Adafruit (v2.4.0+)
 * 2. "ArduinoJson" by Benoit Blanchon (v6.21.0+)
 * 3. ESP32 Board Package by Espressif Systems (v2.0.11+)
 *
 * Board Settings in Arduino IDE:
 * - Board: "AI Thinker ESP32-CAM"
 * - CPU Frequency: 240MHz (WiFi/BT)
 * - Flash Frequency: 80MHz
 * - Flash Mode: QIO
 * - Partition Scheme: "Huge APP (3MB No OTA/1MB SPIFFS)"
 * - Core Debug Level: None / Info
 * - PSRAM: Enabled
 * ============================================================================
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <ArduinoJson.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

#include "camera_pins.h"
#include "config.h"

// Hardware instances
Adafruit_ADS1115 ads;
WiFiClient client;

// Multipart boundary
const char* BOUNDARY = "----ESP32CamPesticideBoundary7MA4YWxkTrZu0gW";

// Function Declarations
bool initCamera();
void connectWiFi();
void warmupSensors();
void sampleSensors(float &mq2_mv, float &mq135_mv);
bool sendInspectionPayload(float mq2_mv, float mq135_mv, const String &produce);
void triggerActuators(const String &status, float risk_score);
void beep(int frequency, int duration_ms);


void setup() {
  // Disable brownout detector to prevent reboot on WiFi transmit current spikes
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  Serial.begin(115200);
  delay(1000);
  Serial.println("\n========================================================");
  Serial.println("   IoT Organic Produce Pesticide Detection System");
  Serial.println("   Firmware: ESP32-CAM + ADS1115 + MQ2 + MQ135");
  Serial.println("========================================================");

  // Initialize GPIO Actuators
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_ALERT_PIN, OUTPUT);
  pinMode(BUTTON_TRIGGER_PIN, INPUT_PULLUP);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_ALERT_PIN, LOW);

  // 1. Initialize I2C Bus on GPIO14 (SDA) and GPIO15 (SCL)
  // This solves the ESP32-CAM ADC2 conflict with WiFi!
  Serial.printf("[*] Initializing I2C (SDA=GPIO%d, SCL=GPIO%d)...\n", I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);

  // 2. Initialize ADS1115 ADC
  Serial.println("[*] Connecting to ADS1115 16-bit ADC...");
  if (!ads.begin(ADS1115_I2C_ADDR, &Wire)) {
    Serial.println("[!] ERROR: ADS1115 not found at 0x48! Check wiring.");
    // Blink LED error code
    while (1) {
      digitalWrite(LED_ALERT_PIN, HIGH);
      delay(200);
      digitalWrite(LED_ALERT_PIN, LOW);
      delay(200);
    }
  }
  // GAIN_ONE: +/- 4.096V 1 bit = 0.125mV (ideal for 3.3V or scaled 5V inputs)
  ads.setGain(GAIN_ONE);
  Serial.println("[+] ADS1115 initialized successfully.");

  // 3. Initialize OV2640 Camera
  Serial.println("[*] Initializing OV2640 Camera Sensor...");
  if (!initCamera()) {
    Serial.println("[!] ERROR: Camera initialization failed!");
    while (1) { delay(1000); }
  }
  Serial.println("[+] Camera initialized successfully.");

  // 4. Sensor Warm-up Countdown
  warmupSensors();

  // 5. Connect to WiFi Network
  connectWiFi();

  Serial.println("\n[+] Setup complete. System ready for produce screening.");
  beep(1000, 150); // Ready chime
}


void loop() {
  // Check trigger condition
  bool shouldTrigger = false;

  if (OPERATION_MODE == MODE_BUTTON) {
    Serial.println("[*] Waiting for push button press to inspect produce...");
    while (digitalRead(BUTTON_TRIGGER_PIN) == HIGH) {
      delay(50);
    }
    Serial.println("[+] Button pressed! Starting inspection...");
    shouldTrigger = true;
    delay(300); // Debounce
  } else {
    // Timer Trigger Mode
    shouldTrigger = true;
  }

  if (shouldTrigger) {
    // Ensure WiFi is connected
    if (WiFi.status() != WL_CONNECTED) {
      connectWiFi();
    }

    // Step 1: Read and average MQ-2 and MQ-135 sensors via ADS1115
    float mq2_mv = 0.0, mq135_mv = 0.0;
    sampleSensors(mq2_mv, mq135_mv);

    // Step 2: Capture JPEG photo and POST to Flask ML server
    Serial.println("[*] Capturing image and sending payload to backend...");
    bool success = sendInspectionPayload(mq2_mv, mq135_mv, TARGET_PRODUCE);

    if (!success) {
      Serial.println("[!] Retrying transmission in 3 seconds...");
      delay(3000);
      sendInspectionPayload(mq2_mv, mq135_mv, TARGET_PRODUCE);
    }

    // Step 3: Handle cycle delay or deep sleep
    if (ENABLE_DEEP_SLEEP) {
      Serial.printf("[*] Entering Deep Sleep for %d seconds...\n", SLEEP_DURATION_SEC);
      esp_sleep_enable_timer_wakeup((uint64_t)SLEEP_DURATION_SEC * 1000000ULL);
      esp_deep_sleep_start();
    } else {
      Serial.printf("[*] Sleeping for %d seconds until next scan...\n\n", TIMER_INTERVAL_SEC);
      delay(TIMER_INTERVAL_SEC * 1000);
    }
  }
}


/**
 * Take multiple samples from ADS1115 and compute average millivolts
 */
void sampleSensors(float &mq2_mv, float &mq135_mv) {
  int32_t mq2_accum = 0;
  int32_t mq135_accum = 0;

  Serial.printf("[*] Sampling sensors (%d samples averaged)...\n", SENSOR_SAMPLES);
  for (int i = 0; i < SENSOR_SAMPLES; i++) {
    int16_t raw_mq2 = ads.readADC_SingleEnded(ADS_CH_MQ2);
    int16_t raw_mq135 = ads.readADC_SingleEnded(ADS_CH_MQ135);
    
    mq2_accum += raw_mq2;
    mq135_accum += raw_mq135;
    delay(SAMPLE_DELAY_MS);
  }

  float avg_raw_mq2 = (float)mq2_accum / SENSOR_SAMPLES;
  float avg_raw_mq135 = (float)mq135_accum / SENSOR_SAMPLES;

  // Convert raw 16-bit counts to millivolts (GAIN_ONE: 0.125 mV per LSB)
  mq2_mv = avg_raw_mq2 * 0.125f;
  mq135_mv = avg_raw_mq135 * 0.125f;

  Serial.printf("[+] MQ-2 (A0):   %.1f mV (Raw: %.0f)\n", mq2_mv, avg_raw_mq2);
  Serial.printf("[+] MQ-135 (A1): %.1f mV (Raw: %.0f)\n", mq135_mv, avg_raw_mq135);
}


/**
 * Capture camera frame and send multipart/form-data POST request
 */
bool sendInspectionPayload(float mq2_mv, float mq135_mv, const String &produce) {
  // Capture picture
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[!] Camera capture failed!");
    return false;
  }
  Serial.printf("[+] JPEG captured: %u bytes (%dx%d)\n", fb->len, fb->width, fb->height);

  // Connect to Laptop Flask Server
  Serial.printf("[*] Connecting to http://%s:%d%s...\n", SERVER_HOST, SERVER_PORT, ANALYZE_ENDPOINT);
  if (!client.connect(SERVER_HOST, SERVER_PORT)) {
    Serial.println("[!] Failed to connect to Flask server!");
    esp_camera_fb_return(fb);
    return false;
  }

  // Build form fields
  String part_mq2 = String("--") + BOUNDARY + "\r\n" +
                    "Content-Disposition: form-data; name=\"mq2_raw\"\r\n\r\n" +
                    String(mq2_mv, 1) + "\r\n";

  String part_mq135 = String("--") + BOUNDARY + "\r\n" +
                      "Content-Disposition: form-data; name=\"mq135_raw\"\r\n\r\n" +
                      String(mq135_mv, 1) + "\r\n";

  String part_produce = String("--") + BOUNDARY + "\r\n" +
                        "Content-Disposition: form-data; name=\"produce_type\"\r\n\r\n" +
                        produce + "\r\n";

  String file_header = String("--") + BOUNDARY + "\r\n" +
                       "Content-Disposition: form-data; name=\"image\"; filename=\"esp32_scan.jpg\"\r\n" +
                       "Content-Type: image/jpeg\r\n\r\n";

  String footer = String("\r\n--") + BOUNDARY + "--\r\n";

  // Calculate total payload length
  size_t total_length = part_mq2.length() + part_mq135.length() + part_produce.length() +
                        file_header.length() + fb->len + footer.length();

  // Send HTTP POST headers
  client.printf("POST %s HTTP/1.1\r\n", ANALYZE_ENDPOINT);
  client.printf("Host: %s:%d\r\n", SERVER_HOST, SERVER_PORT);
  client.println("User-Agent: ESP32-CAM-Pesticide-Screen/1.0");
  client.println("Connection: close");
  client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", BOUNDARY);
  client.printf("Content-Length: %u\r\n\r\n", total_length);

  // Send body parts
  client.print(part_mq2);
  client.print(part_mq135);
  client.print(part_produce);
  client.print(file_header);

  // Stream raw image buffer in 1024-byte chunks
  size_t bytes_remaining = fb->len;
  uint8_t *fb_ptr = fb->buf;
  while (bytes_remaining > 0) {
    size_t chunk_size = (bytes_remaining < 1024) ? bytes_remaining : 1024;
    client.write(fb_ptr, chunk_size);
    fb_ptr += chunk_size;
    bytes_remaining -= chunk_size;
  }
  client.print(footer);

  // Free camera buffer immediately
  esp_camera_fb_return(fb);
  Serial.println("[+] Request sent. Waiting for ML response...");

  // Read response
  unsigned long start_time = millis();
  while (!client.available()) {
    if (millis() - start_time > HTTP_TIMEOUT_MS) {
      Serial.println("[!] Server response timeout!");
      client.stop();
      return false;
    }
    delay(10);
  }

  // Skip HTTP headers to locate JSON body
  bool in_body = false;
  String json_body = "";
  while (client.available()) {
    String line = client.readStringUntil('\n');
    if (!in_body) {
      if (line == "\r" || line.length() == 0) {
        in_body = true;
      }
    } else {
      json_body += line;
    }
  }
  client.stop();

  Serial.println("\n[+] Raw Server Response:");
  Serial.println(json_body);

  // Parse JSON response
  DynamicJsonDocument doc(2048);
  DeserializationError err = deserializeJson(doc, json_body);
  if (err) {
    Serial.printf("[!] JSON parse error: %s\n", err.c_str());
    return false;
  }

  const char* status = doc["status"] | "UNKNOWN";
  float risk_score = doc["risk_score"] | 0.0;
  float confidence = doc["confidence"] | 0.0;
  const char* recommendation = doc["action_recommendation"] | "";

  Serial.println("========================================================");
  Serial.printf("   VERDICT:       %s\n", status);
  Serial.printf("   RISK SCORE:    %.1f %%\n", risk_score);
  Serial.printf("   CONFIDENCE:    %.1f %%\n", confidence);
  Serial.printf("   ACTION:        %s\n", recommendation);
  Serial.println("========================================================");

  // Actuate local Buzzer + LED
  triggerActuators(String(status), risk_score);
  return true;
}


/**
 * Drive local buzzer and LED indicators based on ML verdict
 */
void triggerActuators(const String &status, float risk_score) {
  if (status == "UNSAFE") {
    Serial.println("[!] ALERT: UNSAFE produce! Actuating alarm buzzer & LED...");
    for (int i = 0; i < 4; i++) {
      digitalWrite(LED_ALERT_PIN, HIGH);
      beep(1500, 200);
      digitalWrite(LED_ALERT_PIN, LOW);
      delay(150);
    }
  } else if (status == "MODERATE") {
    Serial.println("[*] Warning: Moderate residue. Beeping alert...");
    digitalWrite(LED_ALERT_PIN, HIGH);
    beep(900, 400);
    delay(1000);
    digitalWrite(LED_ALERT_PIN, LOW);
  } else {
    // SAFE
    Serial.println("[+] Produce is SAFE. Emitting confirmation chime.");
    digitalWrite(LED_ALERT_PIN, LOW);
    beep(600, 100);
    delay(100);
    beep(800, 150);
  }
}


/**
 * Simple tone generation on piezo buzzer pin
 */
void beep(int frequency, int duration_ms) {
  long half_period = 1000000L / (frequency * 2);
  long cycles = ((long)frequency * duration_ms) / 1000;
  for (long i = 0; i < cycles; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    delayMicroseconds(half_period);
    digitalWrite(BUZZER_PIN, LOW);
    delayMicroseconds(half_period);
  }
}


/**
 * Warm up MQ sensors to stabilize surface heating element
 */
void warmupSensors() {
  Serial.printf("[*] Sensor heating element warming up (%d seconds)...\n", QUICK_WARMUP_SEC);
  for (int s = QUICK_WARMUP_SEC; s > 0; s--) {
    Serial.printf("    Warmup countdown: %d sec remaining...\r", s);
    digitalWrite(LED_ALERT_PIN, (s % 2 == 0) ? HIGH : LOW);
    delay(1000);
  }
  digitalWrite(LED_ALERT_PIN, LOW);
  Serial.println("\n[+] Sensors operational and stabilized.");
}


/**
 * Connect to local WiFi network
 */
void connectWiFi() {
  Serial.printf("[*] Connecting to WiFi SSID: '%s'...", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
    delay(400);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[+] WiFi Connected!");
    Serial.printf("    Local IP Address: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("    Target Server:    http://%s:%d\n", SERVER_HOST, SERVER_PORT);
  } else {
    Serial.println("\n[!] WiFi connection timed out! Working in offline retry mode.");
  }
}


/**
 * Configure AI-Thinker OV2640 camera pins and clock
 */
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Frame size & quality
  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA; // 640x480
    config.jpeg_quality = 12;          // 0-63 (lower is higher quality)
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_QVGA; // 320x240
    config.jpeg_quality = 14;
    config.fb_count = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  return (err == ESP_OK);
}
