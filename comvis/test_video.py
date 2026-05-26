import cv2
import math
import json
import paho.mqtt.client as mqtt
from ultralytics import YOLO

# ================= 1. KONFIGURASI LOCAL EDGE MQTT =================
MQTT_BROKER = "localhost" # Alamat laptop lokal
MQTT_PORT = 1883
MQTT_TOPIC = "jalan/status"

client = mqtt.Client()
try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start() # Jalankan background thread MQTT
    mqtt_connected = True
    print("MQTT Local Edge Broker terhubung!")
except Exception as e:
    mqtt_connected = False
    print("Peringatan: Broker MQTT tidak ditemukan. Mode Simulasi GUI.")

# ================= 2. LOAD MODEL YOLO =================
print("Memuat model YOLOv8 Nano...")
model = YOLO("yolov8n.pt") 

# ================= 3. LOAD VIDEO LOKAL =================
video_path = "aman.mp4" 
cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print(f"GAGAL membuka video {video_path}. Pastikan nama file benar!")
    exit()

# Kamus memori untuk melacak posisi dan kecepatan
track_history = {}
speed_history = {} 

SPEED_THRESHOLD_KMH = 60 
frame_count = 0

# ================= 4. LOOP UTAMA =================
while cap.isOpened():
    ret, original_frame = cap.read()
    if not ret:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        track_history.clear()
        speed_history.clear() 
        continue
    
    frame_count += 1
    
    # Frame Skipping (Meringankan CPU)
    if frame_count % 2 == 0:
        continue

    # Waktu asli dari video (Bukan waktu dunia nyata)
    current_time = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 
    
    # ROI: Pertahankan potongan 25% atas dan 25% kiri milikmu
    h, w = original_frame.shape[:2]
    batas_atas = int(h * 0.25)  
    batas_kiri = int(w * 0.25)  
    frame = original_frame[batas_atas:h, batas_kiri:w]
    
    # Tracking dengan setelan yang sudah matang
    results = model.track(frame, persist=True, classes=[0, 1, 2, 3, 5, 7], conf=0.10, iou=0.7, imgsz=640, verbose=False)
    
    kepadatan = 0
    status_jalan = "AMAN"

    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xywh.cpu()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        kepadatan = len(track_ids)

        for box, track_id in zip(boxes, track_ids):
            x, y, w_box, h_box = box
            current_center = (float(x), float(y))
            color = (0, 255, 0) 

            if track_id not in track_history:
                track_history[track_id] = (current_center, current_time)
                speed_history[track_id] = 0
            else:
                old_center, old_time = track_history[track_id]
                time_diff = current_time - old_time

                if time_diff >= 0.2:
                    dx = current_center[0] - old_center[0]
                    dy = current_center[1] - old_center[1]
                    jarak_piksel = math.sqrt(dx**2 + dy**2)
                    
                    if jarak_piksel < 5:
                        estimasi_kmh = 0
                    else:
                        pixel_speed = jarak_piksel / time_diff
                        estimasi_kmh = int(pixel_speed * 0.15) 
                    
                    speed_history[track_id] = estimasi_kmh
                    track_history[track_id] = (current_center, current_time)

            kecepatan_tampil = speed_history.get(track_id, 0)
            
            if kecepatan_tampil > SPEED_THRESHOLD_KMH:
                status_jalan = "NGEBUT"
                color = (0, 0, 255) 
                cv2.putText(frame, "NGEBUT!", (int(x), int(y)-30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            cv2.rectangle(frame, (int(x-w_box/2), int(y-h_box/2)), (int(x+w_box/2), int(y+h_box/2)), color, 2)
            text_info = f"ID:{track_id} | {kecepatan_tampil} Km/h"
            cv2.putText(frame, text_info, (int(x-w_box/2), int(y-h_box/2) + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    # ================= 5. PUBLISH DATA MQTT (JSON) =================
    # Format data diubah dari string mentah menjadi dictionary JSON
    if mqtt_connected:
        payload = json.dumps({
            "kepadatan": kepadatan,
            "status": status_jalan
        })
        client.publish(MQTT_TOPIC, payload)

    # ================= 6. TAMPILAN UI =================
    cv2.putText(frame, f"Kendaraan: {kepadatan}", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(frame, f"Status: {status_jalan}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, 
                (0, 0, 255) if status_jalan == "NGEBUT" else (0, 255, 0), 3)

    cv2.imshow("Smart Safe Walking Path - Local Video Analysis", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'): 
        break

cap.release()
cv2.destroyAllWindows()
# Mematikan koneksi MQTT dengan aman saat program ditutup
if mqtt_connected:
    client.loop_stop()
    client.disconnect()