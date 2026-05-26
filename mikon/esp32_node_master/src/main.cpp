/* =====================================================
 * Demo LDR + LED + MQTT + PIR (Simulasi Keyboard)
 * TERANG = LED OFF | GELAP = LED ON
 * Board   : ESP32-C3 DevKitM-1
 * Pin LDR : GPIO3
 * Pin LED : GPIO2
 * ===================================================== */

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ================= 1. KONFIGURASI WIFI & MQTT =================
const char* ssid        = "veroo";
const char* password    = "veronica";
const char* mqtt_server = "10.236.155.191";
const int   mqtt_port   = 1883;

WiFiClient espClient;
PubSubClient client(espClient);

// ================= 2. KONFIGURASI LDR =================
#define PIN_LDR       3
#define PIN_LED       2
#define THRESHOLD_ON  1000   // ✅ Sesuaikan: < threshold = TERANG, >= threshold = GELAP
#define ADC_SAMPLES   20
#define DELAY_MS      300

int  ldr_raw   = 0;
bool is_terang = false;
bool prev_mode = false;

// ================= 3. FUNGSI MQTT CALLBACK & RECONNECT =================
void callback(char* topic, byte* payload, unsigned int length) {
    String message = "";
    for (int i = 0; i < length; i++) {
        message += (char)payload[i];
    }

    StaticJsonDocument<200> doc;
    DeserializationError error = deserializeJson(doc, message);

    if (error) {
        Serial.print("Gagal parsing JSON: ");
        Serial.println(error.c_str());
        return;
    }

    int kepadatan       = doc["kepadatan"];
    String status_jalan = doc["status"];

    Serial.println("\n========================================");
    Serial.printf(" [MQTT YOLO] Kepadatan: %d | Status: %s\n", kepadatan, status_jalan.c_str());
    Serial.println("========================================");
}

void reconnect() {
    while (!client.connected()) {
        Serial.print("\nMencoba konek ke MQTT Broker...");
        String clientId = "ESP32C3-Master-" + String(random(0, 0xffff), HEX);
        if (client.connect(clientId.c_str())) {
            Serial.println(" BERHASIL!");
            client.subscribe("jalan/status");
        } else {
            Serial.print(" GAGAL, rc=");
            Serial.print(client.state());
            Serial.println(" (Coba lagi 5 detik...)");
            delay(5000);
        }
    }
}

// ================= 4. FUNGSI BACA LDR =================
int bacaLDR() {
    long total = 0;
    for (int i = 0; i < ADC_SAMPLES; i++) {
        total += analogRead(PIN_LDR);
        delay(3);
    }
    return (int)(total / ADC_SAMPLES);
}

void setup() {
    Serial.begin(115200);
    delay(1000);

    // --- SETUP WIFI ---
    Serial.println();
    Serial.print("Konek ke WiFi: ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nWiFi Terhubung!");

    // --- SETUP MQTT ---
    client.setServer(mqtt_server, mqtt_port);
    client.setCallback(callback);

    // --- SETUP PIN ---
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);

    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    Serial.println("========================================");
    Serial.println("  Demo LDR: TERANG=OFF | GELAP=ON");
    Serial.println("  ESP32-C3 | PlatformIO | 2026");
    Serial.println("========================================");
    Serial.printf("  Threshold : %d\n", THRESHOLD_ON);
    Serial.println("  ADC < threshold → TERANG → LED OFF");
    Serial.println("  ADC >= threshold → GELAP  → LED ON");
    Serial.println("----------------------------------------");
    Serial.println("  ADC  | Kondisi | LED");
    Serial.println("----------------------------------------");
}

void loop() {
    // --- Jaga Koneksi MQTT ---
    if (!client.connected()) {
        reconnect();
    }
    client.loop();

    // --- BACA LDR ---
    ldr_raw   = bacaLDR();
    is_terang = (ldr_raw < THRESHOLD_ON);                    // ✅ < threshold = TERANG
    digitalWrite(PIN_LED, is_terang ? HIGH : LOW);           // ✅ TERANG=OFF, GELAP=ON

    Serial.printf("  %4d | %s | %s\n",
        ldr_raw,
        is_terang ? "GELAP " : "TERANG  ",
        is_terang ? "ON" : "OFF "
    );

    if (is_terang != prev_mode) {
        Serial.println("----------------------------------------");
        Serial.printf("  *** %s ***\n",
            is_terang ? "TERANG → LED OFF" : "GELAP → LED ON");
        Serial.println("----------------------------------------");
        prev_mode = is_terang;
    }

    // --- SIMULASI PIR (KEYBOARD P) ---
    if (Serial.available() > 0) {
        char input = Serial.read();
        if (input == 'P' || input == 'p') {
            Serial.println("\n[SIMULASI] Pejalan Kaki Terdeteksi! (Tombol P Ditekan)\n");
        }
    }

    delay(DELAY_MS);
}