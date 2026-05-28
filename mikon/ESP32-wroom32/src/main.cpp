#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

// ================= KONFIGURASI WIFI & MQTT =================
const char* ssid = "veroo";         // GANTI DENGAN WIFI HP/ROUTER
const char* password = "veronica"; // GANTI DENGAN PASS WIFI
const char* mqtt_server = "10.236.155.191";  // IP Laptop kamu

WiFiClient espClient;
PubSubClient client(espClient);

// ============================================================
// KONFIGURASI PIN & KONSTANTA
// ============================================================
const int PIR_PIN         = 13;
const int LDR_PIN         = 34;
const int LED_PIN         = 5;
const int LDR_THRESHOLD   = 400;
const int LDR_SAMPLES     = 10;          // Jumlah sample untuk averaging
const unsigned long MOTION_TIMEOUT_MS = 15000UL;

// ============================================================
// HANDLE TASK & SINKRONISASI
// ============================================================
TaskHandle_t xTaskPIRHandle    = NULL;
TaskHandle_t xTaskLEDHandle    = NULL;
TaskHandle_t xTaskMQTTHandle = NULL;

// ✅ PERBAIKAN: Ganti volatile bool dengan Mutex + variable biasa
// Mutex menjamin mutual exclusion antar core
SemaphoreHandle_t xMutexAdaOrang = NULL;
bool adaOrang = false;  // Diakses HANYA lewat mutex

// ✅ PERBAIKAN: Timestamp disimpan atomic dengan mutex yang sama
unsigned long lastMotionTime = 0;

// ============================================================
// HELPER: Akses adaOrang dengan aman (thread-safe)
// ============================================================
bool getAdaOrang() {
  bool nilai;
  // Tunggu mutex maksimal 10ms, jangan block terlalu lama
  if (xSemaphoreTake(xMutexAdaOrang, pdMS_TO_TICKS(10)) == pdTRUE) {
    nilai = adaOrang;
    xSemaphoreGive(xMutexAdaOrang);
  } else {
    nilai = false; // Safe default jika mutex timeout
  }
  return nilai;
}

bool setAdaOrang(bool nilai, bool updateTimestamp = true) {
  if (xSemaphoreTake(xMutexAdaOrang, pdMS_TO_TICKS(10)) == pdTRUE) {
    bool sebelumnya = adaOrang;
    adaOrang = nilai;
    if (nilai && updateTimestamp) {
      lastMotionTime = millis(); // ✅ Update timestamp saat set true
    }
    xSemaphoreGive(xMutexAdaOrang);
    return sebelumnya; // Return nilai lama untuk deteksi perubahan
  }
  return nilai; // Gagal acquire, anggap tidak berubah
}

unsigned long getLastMotionTime() {
  unsigned long t;
  if (xSemaphoreTake(xMutexAdaOrang, pdMS_TO_TICKS(10)) == pdTRUE) {
    t = lastMotionTime;
    xSemaphoreGive(xMutexAdaOrang);
  } else {
    t = millis(); // Safe: anggap baru saja ada gerakan
  }
  return t;
}

// ============================================================
// HELPER: Median filter untuk ADC LDR (anti-noise)
// ============================================================
int bacaLDRFiltered() {
  int samples[LDR_SAMPLES];
  
  // Ambil beberapa sample
  for (int i = 0; i < LDR_SAMPLES; i++) {
    samples[i] = analogRead(LDR_PIN);
    vTaskDelay(pdMS_TO_TICKS(5)); // Beri jeda antar reading
  }
  
  // Bubble sort sederhana untuk median
  for (int i = 0; i < LDR_SAMPLES - 1; i++) {
    for (int j = 0; j < LDR_SAMPLES - i - 1; j++) {
      if (samples[j] > samples[j + 1]) {
        int tmp = samples[j];
        samples[j] = samples[j + 1];
        samples[j + 1] = tmp;
      }
    }
  }
  
  return samples[LDR_SAMPLES / 2]; // Median value
}

// ============================================================
// ISR — PIR (Harus seminimal mungkin, hanya di IRAM)
// ============================================================
void IRAM_ATTR pirInterruptHandler() {
  BaseType_t xHigherPriorityTaskWoken = pdFALSE;
  vTaskNotifyGiveFromISR(xTaskPIRHandle, &xHigherPriorityTaskWoken);
  // ✅ portYIELD_FROM_ISR dengan parameter — cara yang benar
  portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
}

// ============================================================
// DEKLARASI GLOBAL TIMER & FLAG (dipakai lintas task)
// ============================================================
SemaphoreHandle_t xMutexTimer = NULL;
int timerDariCV = 10;               // default fallback 10 detik
bool statusHold = false;            // true jika CV kirim "HOLD"
bool pendingPublishTrigger = false; // flag publish PIR, dieksekusi di Core 0

// ============================================================
// TASK 1: PIR Handler — Core 1, Priority 3
// ============================================================
void vTaskPIR(void *pvParameters) {
  // ✅ Debounce: simpan timestamp terakhir trigger
  TickType_t lastTriggerTick = 0;
  const TickType_t debounceTicks = pdMS_TO_TICKS(500); // 500ms debounce

  for (;;) {
    // Blocked sempurna sampai ISR memberi notifikasi
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    Serial.println("SYSTEM:LED_TASK_ACTIVE");

    // ✅ TAMBAH: Beri waktu 200ms agar MQTT sempat update timerDariCV terbaru
    vTaskDelay(pdMS_TO_TICKS(200));

    TickType_t sekarang = xTaskGetTickCount();
    
    // ✅ Cek debounce — abaikan trigger terlalu cepat
    if ((sekarang - lastTriggerTick) < debounceTicks) {
      continue; // Abaikan, terlalu cepat dari trigger sebelumnya
    }
    lastTriggerTick = sekarang;

    // Double-check pin state (konfirmasi sinyal valid)
    if (digitalRead(PIR_PIN) == HIGH) {
      bool sebelumnya = setAdaOrang(true);
      
      if (!sebelumnya) {
        // Hanya log dan notify jika state benar-benar berubah
        Serial.println("STATUS:PIR_TRIGGERED");

        // ✅ REVISI: Ganti client.publish() langsung dari Core 1 (race condition)
        // Gunakan flag yang dieksekusi di Core 0 lewat vTaskMQTT
        if (xSemaphoreTake(xMutexTimer, pdMS_TO_TICKS(10)) == pdTRUE) {
          pendingPublishTrigger = true;
          xSemaphoreGive(xMutexTimer);
        }

        xTaskNotifyGive(xTaskLEDHandle);
      } else {
        // Orang masih ada, refresh timestamp saja (sudah dilakukan setAdaOrang)
        Serial.println("STATUS:PIR_REFRESH");
      }
    }
  }
}

// ============================================================
// TASK 2: MQTT — Core 0, Priority 2
// ============================================================

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.print("Data dari CV: ");
  Serial.println(message);

  // ✅ Parse JSON dan simpan timer
  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, message);
  if (!err) {
    if (xSemaphoreTake(xMutexTimer, pdMS_TO_TICKS(10)) == pdTRUE) {
      // ✅ REVISI: Cek tipe dengan benar — timer bisa integer atau string "HOLD"
      if (doc["timer"].is<const char*>() && String(doc["timer"].as<const char*>()) == "HOLD") {
        statusHold = true;
      } else {
        statusHold = false;
        timerDariCV = doc["timer"] | 10; // fallback 10 jika null
      }
      xSemaphoreGive(xMutexTimer);
    }
  }
}

void vTaskMQTT(void *pvParameters) {
  for (;;) {
    if (!client.connected()) {
      Serial.print("Koneksi MQTT...");
      if (client.connect("ESP32_Master")) {
        Serial.println("Terhubung!");
        client.subscribe("jalan/status"); // Dengerin data dari laptop
      } else {
        vTaskDelay(pdMS_TO_TICKS(3000));
      }
    } else {
      client.loop(); // Jaga koneksi MQTT tetap hidup

      // ✅ REVISI: Publish trigger PIR dari Core 0 — aman, tidak race condition
      if (xSemaphoreTake(xMutexTimer, pdMS_TO_TICKS(10)) == pdTRUE) {
        if (pendingPublishTrigger) {
          client.publish("jalan/trigger", "ADA_ORANG");
          pendingPublishTrigger = false;
        }
        xSemaphoreGive(xMutexTimer);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

// ============================================================
// TASK 3: LED & LDR Controller — Core 0, Priority 1
// ============================================================
void vTaskLED(void *pvParameters) {
  for (;;) {
    // ✅ Satu for(;;) yang benar — tunggu notifikasi
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    Serial.println("SYSTEM:LED_TASK_ACTIVE");

    while (getAdaOrang()) {
      // ✅ Ambil timer dari CV, jalankan countdown
      int countdown = 10; // fallback default
      bool hold = false;

      if (xSemaphoreTake(xMutexTimer, pdMS_TO_TICKS(10)) == pdTRUE) {
        countdown = timerDariCV;
        hold = statusHold;
        xSemaphoreGive(xMutexTimer);
      }

      if (hold) {
        // Kondisi HOLD: jangan mulai countdown, tunggu sampai aman
        Serial.println("STATUS:HOLD_WAITING");
        vTaskDelay(pdMS_TO_TICKS(1000));
        // Cek timeout tetap berjalan agar tidak stuck selamanya
        if ((millis() - getLastMotionTime()) > MOTION_TIMEOUT_MS) {
          setAdaOrang(false, false);
          digitalWrite(LED_PIN, LOW);
          Serial.println("SYSTEM:TIMEOUT_STANDBY");
          break;
        }
        continue;
      }

      // ✅ Countdown loop
      Serial.print("STATUS:COUNTDOWN_START:");
      Serial.println(countdown);

      for (int i = countdown; i > 0 && getAdaOrang(); i--) {
        int nilaiLDR = bacaLDRFiltered();
        Serial.print("LDR_VALUE:");
        Serial.println(nilaiLDR);

        if (nilaiLDR < LDR_THRESHOLD) {
          digitalWrite(LED_PIN, HIGH);
          Serial.println("STATUS:LED_ON");
        } else {
          digitalWrite(LED_PIN, LOW);
          Serial.println("STATUS:LED_OFF");
        }

        Serial.print("STATUS:COUNTDOWN:");
        Serial.println(i); // ← ini yang nanti dikirim ke 7-segment

        vTaskDelay(pdMS_TO_TICKS(1000)); // 1 detik per tick
      }

      // Countdown selesai → standby
      setAdaOrang(false, false);
      digitalWrite(LED_PIN, LOW);
      Serial.println("SYSTEM:COUNTDOWN_DONE_STANDBY");
      break;
    } // end while (getAdaOrang())

  } // end for(;;)
} // end vTaskLED

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); } // Tunggu Serial ready

  pinMode(PIR_PIN, INPUT_PULLDOWN);
  pinMode(LDR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // ✅ Setup WiFi & MQTT
  WiFi.begin(ssid, password);
  Serial.print("Konek WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi OK!");
  
  client.setServer(mqtt_server, 1883);
  client.setCallback(mqttCallback);

  // ✅ Buat mutex SEBELUM task dibuat
  xMutexAdaOrang = xSemaphoreCreateMutex();
  xMutexTimer = xSemaphoreCreateMutex();
  
  // ✅ REVISI: Cek kedua mutex sekaligus
  if (xMutexAdaOrang == NULL || xMutexTimer == NULL) {
    Serial.println("FATAL:MUTEX_CREATION_FAILED");
    while(1); // Halt — tidak bisa lanjut tanpa mutex
  }

  // Core 1: Task PIR — prioritas tinggi, dekat hardware
  xTaskCreatePinnedToCore(
    vTaskPIR, "Task_PIR",
    2048,           // Stack cukup, task ini ringan
    NULL, 3,
    &xTaskPIRHandle, 1
  );

  // Core 0: Task MQTT — stack lebih besar untuk char buffer
  xTaskCreatePinnedToCore(
    vTaskMQTT, "Task_MQTT", 
    4096, 
    NULL, 2, 
    &xTaskMQTTHandle, 0);

  // Core 0: Task LED — stack cukup, LDR filter pakai stack lokal
  xTaskCreatePinnedToCore(
    vTaskLED, "Task_LED",
    3072,           // ✅ Diperbesar untuk array samples filter
    NULL, 1,
    &xTaskLEDHandle, 0
  );

  attachInterrupt(digitalPinToInterrupt(PIR_PIN), pirInterruptHandler, RISING);

  Serial.println("SYSTEM:READY_DUAL_CORE_RTOS");
}

void loop() {
  vTaskDelete(NULL);
}