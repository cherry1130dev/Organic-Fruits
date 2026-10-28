# ESP32-CAM Firmware Guide: Organic Produce Pesticide Residue Screener

This firmware runs on the **AI-Thinker ESP32-CAM** board. It interfaces with an **ADS1115 16-bit ADC** over I2C to read **MQ-2** and **MQ-135** gas sensors, captures JPEG produce images using the onboard **OV2640** camera, transmits them via HTTP `multipart/form-data` to a laptop Flask backend, parses the JSON prediction verdict, and drives a local buzzer and alert LED.

---

## 1. Critical Hardware Constraints & Engineering Solutions

### A. The ESP32-CAM ADC2 / WiFi Conflict
- **Problem:** The ESP32 SoC shares internal analog converter channels (ADC2) with the WiFi radio subsystem. When `WiFi.begin()` executes, all ADC2 pins (`GPIO0, GPIO2, GPIO4, GPIO12, GPIO13, GPIO14, GPIO15, GPIO25, GPIO26, GPIO27`) are disabled or return invalid constant readings (0 or 4095).
- **Solution:** We use an **ADS1115 16-bit 4-channel External I2C ADC** to read the MQ sensors. We map standard I2C bus pins to `GPIO14` (SDA) and `GPIO15` (SCL) via `Wire.begin(14, 15)`. Digital I2C communication is completely unaffected by WiFi activity!

### B. MQ Sensor 5V Output to 3.3V ADC Protection
- **Problem:** MQ-2 and MQ-135 heating coils require **5.0V VCC** to operate their internal tin dioxide ($SnO_2$) ceramic heating elements. Consequently, their analog output pins can swing up to 5.0V under high gas concentrations, which exceeds the 3.3V maximum input rating of an ESP32 or 3.3V-powered ADS1115.
- **Solution:** 
  1. **Option 1 (Recommended):** Power the ADS1115 from the 3.3V rail of the ESP32-CAM, and place a simple **2-resistor voltage divider** ($R_1 = 4.7k\Omega, R_2 = 10k\Omega$) on each MQ sensor's analog output.
     $$\text{Divider Ratio} = \frac{R_2}{R_1 + R_2} = \frac{10k}{4.7k + 10k} = 0.68 \implies 5.0V \times 0.68 = 3.4V \text{ max}$$
  2. **Option 2:** Power the ADS1115 from 5.0V, with $10k\Omega$ pull-up resistors on SDA/SCL tied to 3.3V (I2C open-drain safe).

### C. Sensor Burn-In & Warm-Up Times
- **Burn-In (Initial Use):** Brand-new MQ sensors require **24 to 48 hours** of continuous powered preheating in clean air to drive off ambient moisture and establish a stable chemical absorption layer.
- **Operational Warm-Up:** On each subsequent boot, the internal heating element takes **3 to 5 minutes** to reach thermal equilibrium ($~300^\circ\text{C}$). The firmware includes a non-blocking countdown routine to allow heater stabilization before taking critical baseline measurements.

---

## 2. Complete Wiring Table & Pinout Map

| Component | Pin / Terminal | Connects to ESP32-CAM | Connects to ADS1115 | Power / Notes |
| :--- | :--- | :--- | :--- | :--- |
| **ESP32-CAM** | 5V | External 5V 2A Supply | — | High-current regulated 5V input |
| **ESP32-CAM** | GND | Common GND | GND | Shared system ground |
| **ADS1115** | VDD | 3.3V (ESP32) | — | 3.3V Operating Voltage |
| **ADS1115** | GND | Common GND | — | Common ground |
| **ADS1115** | SCL | **GPIO 15** | — | Hardware I2C Clock |
| **ADS1115** | SDA | **GPIO 14** | — | Hardware I2C Data |
| **ADS1115** | ADDR | — | GND | Sets I2C Address to `0x48` |
| **ADS1115** | A0 | — | MQ-2 Analog Out | MQ-2 reading channel |
| **ADS1115** | A1 | — | MQ-135 Analog Out | MQ-135 reading channel |
| **MQ-2 Gas Sensor** | VCC | External 5V Rail | — | Requires 5V (heater coil) |
| **MQ-2 Gas Sensor** | GND | Common GND | — | Common ground |
| **MQ-2 Gas Sensor** | AOUT | — | **A0 (via divider)** | $4.7k\Omega / 10k\Omega$ divider |
| **MQ-135 Gas Sensor**| VCC | External 5V Rail | — | Requires 5V (heater coil) |
| **MQ-135 Gas Sensor**| GND | Common GND | — | Common ground |
| **MQ-135 Gas Sensor**| AOUT | — | **A1 (via divider)** | $4.7k\Omega / 10k\Omega$ divider |
| **Piezo Buzzer** | Positive (+) | **GPIO 12** | — | Driven via NPN transistor or direct |
| **Piezo Buzzer** | Negative (-) | Common GND | — | Common ground |
| **Alert Red LED** | Anode (+) | **GPIO 13** | — | With $220\Omega$ series resistor |
| **Alert Red LED** | Cathode (-) | Common GND | — | Common ground |
| **Push Button** | Terminal 1 | **GPIO 2** | — | Active LOW trigger |
| **Push Button** | Terminal 2 | Common GND | — | Internal pull-up enabled |

---

## 3. Circuit Schematic (ASCII Diagram)

```
        +5V External Power Supply (2A)
           |
           +--------------------+-------------------+
           |                    |                   |
       [ESP32-CAM]            [MQ-2]             [MQ-135]
       +---------+          +--------+          +--------+
       |   5V    |          |  VCC   |          |  VCC   |
       |         |          |        |          |        |
       |  GPIO14 |---[SDA]--|        |          |        |
       |  GPIO15 |---[SCL]--|        |          |        |
       |         |          |  AOUT  |          |  AOUT  |
       |  GPIO12 |-[Buzzer] +---+----+          +---+----+
       |  GPIO13 |-[LED]        |                   |
       |  GPIO2  |-[Button]     | [4.7k]            | [4.7k]
       |         |              +----/\/\--+        +----/\/\--+
       |   3.3V  |---+          |          |        |          |
       |   GND   |---|--+       | [10k]    |        | [10k]    |
       +---------+   |  |     +---/\/\-+   |      +---/\/\-+   |
                     |  |     |        |   |      |        |   |
                     |  |    GND       |  GND    GND       |  GND
                     |  |              |                   |
                     |  |       +------+-------------------+
                     |  |       |      |
                     |  |    +--+------+----+
                     +--|----| VDD     A0   |<-- MQ-2 Scaled (0-3.3V)
                        |    |         A1   |<-- MQ-135 Scaled (0-3.3V)
                        +----| GND     SDA  |<-- GPIO14
                             | ADDR    SCL  |<-- GPIO15
                             +--------------+
                                [ADS1115]
```

---

## 4. Arduino IDE Setup & Configuration

1. **Install ESP32 Board Support:**
   - In Arduino IDE, go to **File > Preferences**.
   - Add this URL to "Additional Boards Manager URLs":
     `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Go to **Tools > Board > Boards Manager**, search for `esp32` by Espressif Systems, and click **Install** (v2.0.11 or later).

2. **Install Required Arduino Libraries:**
   - Go to **Sketch > Include Library > Manage Libraries...**
   - Search for and install:
     - `Adafruit ADS1X15` by Adafruit
     - `ArduinoJson` by Benoit Blanchon (version 6.x)

3. **Select Board Options:**
   - **Board:** `AI Thinker ESP32-CAM`
   - **CPU Frequency:** `240MHz (WiFi/BT)`
   - **Flash Frequency:** `80MHz`
   - **Flash Mode:** `QIO`
   - **Partition Scheme:** `Huge APP (3MB No OTA/1MB SPIFFS)`
   - **PSRAM:** `Enabled` (Crucial for camera frame buffer!)

4. **Flash / Upload Procedure:**
   - Connect an **FTDI USB-to-UART programmer** (set to 3.3V or 5V):
     - FTDI `TX` $\to$ ESP32-CAM `RX` (GPIO 3)
     - FTDI `RX` $\to$ ESP32-CAM `TX` (GPIO 1)
     - FTDI `GND` $\to$ ESP32-CAM `GND`
     - FTDI `VCC` $\to$ ESP32-CAM `5V`
   - **CRITICAL:** Bridge **GPIO 0 to GND** before powering on to enter Flash Download mode.
   - Click **Upload** in Arduino IDE.
   - When upload completes, **disconnect GPIO 0 from GND** and press the ESP32-CAM **RST** button to start normal firmware execution!

---

## 5. Hardware Troubleshooting Guide

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| **`Brownout detector was triggered`** | Current draw exceeds power supply during WiFi connection. | Power ESP32-CAM with an external 5V 2A dedicated supply. Add a $470\mu\text{F}$ capacitor across 5V and GND. The firmware also disables RTC brownout in `setup()`. |
| **`Camera init failed with error 0x20003`** | PSRAM is not enabled or bad ribbon cable connection. | Enable PSRAM in Tools menu. Gently reseat the camera ribbon cable into its locking connector. |
| **`ADS1115 not found at 0x48`** | I2C wiring loose or address pin floating. | Verify SDA is on GPIO14 and SCL is on GPIO15. Ensure ADS1115 ADDR pin is tied to GND. |
| **`WiFi Connection Drops`** | Metal camera shield or high RF noise. | Ensure stable 5V rail. Keep FTDI/wiring away from the onboard trace antenna. |
| **`Sensor Readings Stuck at Max`** | Heating coil not yet warmed up or ground missing. | Ensure MQ sensors share common GND with ESP32-CAM and ADS1115. Allow 3-5 minutes for coil heating. |
