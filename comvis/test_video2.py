import cv2
import json
import paho.mqtt.client as mqtt
from ultralytics import YOLO

# ===============================================================
# SMART SAFE WALKING PATH — Final Edge CV Module v4
# Metode kecepatan : Virtual Line State-Crossing
# Fix v2→v3       : "spawn below line" edge case
# Fix v3→v4       : Arah crossing dibalik (bottom→top, sesuai bird-eye kamera)
# ===============================================================

# =================== 1. KONFIGURASI MQTT LOCAL EDGE ===================
MQTT_BROKER = "10.236.155.191"
MQTT_PORT   = 1883
MQTT_TOPIC  = "jalan/status"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    mqtt_connected = True
    print("[MQTT] Local Edge Broker terhubung!")
except Exception as e:
    mqtt_connected = False
    print(f"[MQTT] Broker tidak ditemukan → Mode Simulasi GUI. ({e})")

# =================== 2. LOAD MODEL YOLO ===================
print("[CV] Memuat model YOLOv8 Nano...")
model = YOLO("yolov8n.pt")

# =================== 3. LOAD VIDEO LOKAL ===================
video_path = "aman.mp4"
cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print(f"[ERROR] Gagal membuka video: {video_path}")
    exit()

video_fps = cap.get(cv2.CAP_PROP_FPS)
if video_fps <= 0:
    video_fps = 30.0
print(f"[INFO] Video FPS: {video_fps:.1f}")

# =================== 4. PARAMETER KECEPATAN ===================
# ----------------------------------------------------------------
# Kalibrasi JARAK_ANTAR_GARIS_METER:
#   Lihat video → hitung berapa marka jalan (tiap strip = 5m) yang
#   ada di antara LINE_A dan LINE_B. Kalikan dengan panjang strip.
#   Atau ukur dari referensi objek fisik yang diketahui dimensinya.
# ----------------------------------------------------------------
JARAK_ANTAR_GARIS_METER = 7.5  # ← SESUAIKAN dengan video kamu
SPEED_THRESHOLD_KMH     = 40    # ← batas "ngebut" (zona 40 km/h)

# Posisi garis virtual di frame ROI (rasio tinggi frame)
# Kendaraan bergerak BOTTOM → TOP (bird-eye, kamera di atas zebra cross)
# LINE_A = garis bawah (START — kendaraan melewati ini duluan)
# LINE_B = garis atas  (STOP  — kendaraan melewati ini setelahnya)
LINE_A_RATIO = 0.75  # garis bawah = START
LINE_B_RATIO = 0.40  # garis atas  = STOP

# =================== 5. STRUKTUR DATA ===================
# track_data[id] = {
#   "prev_y"     : float  — posisi Y frame sebelumnya
#   "crossed_A"  : bool   — sudah melewati LINE_A?
#   "crossed_B"  : bool   — sudah melewati LINE_B?
#   "time_A"     : float|None
#   "time_B"     : float|None
#   "valid_entry": bool   — apakah kendaraan spawn DI ATAS LINE_A?
# }
track_data  = {}
speed_cache = {}  # {track_id: speed_kmh} — persisten antar frame

# Debounce: status NGEBUT hanya aktif setelah N frame beruntun
NGEBUT_DEBOUNCE_FRAMES = 8
ngebut_counter         = 0

frame_count = 0

# =================== 6. LOOP UTAMA ===================
while cap.isOpened():
    ret, original_frame = cap.read()
    if not ret:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        track_data.clear()
        speed_cache.clear()
        ngebut_counter = 0
        frame_count    = 0
        continue

    frame_count += 1
    current_time_s = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
    if frame_count % 2 == 0:
        continue  # frame skipping — baca tapi tidak proses

    # ---- ROI ----
    h_orig, w_orig = original_frame.shape[:2]
    roi_top  = int(h_orig * 0.25)
    roi_left = int(w_orig * 0.25)
    frame    = original_frame[roi_top:h_orig, roi_left:w_orig]
    h_roi, w_roi = frame.shape[:2]

    line_a_y = int(h_roi * LINE_A_RATIO)
    line_b_y = int(h_roi * LINE_B_RATIO)

    # ---- YOLO Tracking ----
    results = model.track(
        frame,
        persist = True,
        tracker = "bytetrack.yaml",
        classes = [0, 1, 2, 3, 5, 7],  # person, bicycle, car, motorcycle, bus, truck
        conf    = 0.25,
        iou     = 0.5,
        imgsz   = 640,
        verbose = False
    )

    kepadatan  = 0
    ada_ngebut = False

    if results[0].boxes.id is not None:
        boxes     = results[0].boxes.xywh.cpu()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        kepadatan = len(track_ids)

        for box, track_id in zip(boxes, track_ids):
            cx, cy = float(box[0]), float(box[1])
            bw, bh = float(box[2]), float(box[3])
            
            # --- REVISI KRUSIAL: GROUND PLANE TRACKING ---
            # Menggunakan titik roda menyentuh aspal, bukan atap mobil
            bottom_y = cy + (bh / 2)

            # ---- Inisialisasi track baru ----
            if track_id not in track_data:
                # Toleransi 100 piksel dari Garis A (menghindari telat deteksi YOLO)
                TOLERANSI_SPAWN = 100
                spawned_below_a = bottom_y > (line_a_y - TOLERANSI_SPAWN)
                
                track_data[track_id] = {
                    "prev_y"     : bottom_y,
                    "crossed_A"  : False,
                    "crossed_B"  : False,
                    "time_A"     : None,
                    "time_B"     : None,
                    "valid_entry": spawned_below_a,
                }
                speed_cache[track_id] = 0
                if not spawned_below_a:
                    print(f"[WARN] ID:{track_id} muncul di atas LINE_A (y={bottom_y:.0f} < {line_a_y}) → tidak dihitung")

            td     = track_data[track_id]
            prev_y = td["prev_y"]

            # ---- Hanya proses crossing kalau valid_entry = True ----
            if td["valid_entry"]:
                # State-crossing LINE_A
                if not td["crossed_A"] and prev_y > line_a_y >= bottom_y:
                    td["crossed_A"] = True
                    td["time_A"]    = current_time_s

                # State-crossing LINE_B
                if td["crossed_A"] and not td["crossed_B"] and prev_y > line_b_y >= bottom_y:
                    td["crossed_B"] = True
                    td["time_B"]    = current_time_s

                    delta_t = td["time_B"] - td["time_A"]
                    if delta_t > 0.05:
                        speed_ms  = JARAK_ANTAR_GARIS_METER / delta_t
                        speed_kmh = int(speed_ms * 3.6)
                        speed_cache[track_id] = speed_kmh
                        print(f"[SPEED] ID:{track_id} → {speed_kmh} km/h (Δt={delta_t:.2f}s)")

                    td["crossed_A"] = False
                    td["crossed_B"] = False
                    td["time_A"]    = None
                    td["time_B"]    = None

            # Update memori posisi roda untuk frame berikutnya
            td["prev_y"] = bottom_y

            # ---- Render ----
            kecepatan_kmh = speed_cache.get(track_id, 0)
            is_ngebut     = kecepatan_kmh > SPEED_THRESHOLD_KMH
            if is_ngebut:
                ada_ngebut = True

            color = (0, 0, 255) if is_ngebut else (0, 255, 0)
            # abu-abu untuk kendaraan yang invalid entry (tidak bisa dihitung)
            if not td["valid_entry"]:
                color = (150, 150, 150)

            x1, y1 = int(cx - bw / 2), int(cy - bh / 2)
            x2, y2 = int(cx + bw / 2), int(cy + bh / 2)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            spd_label = f"{kecepatan_kmh} km/h" if td["valid_entry"] else "N/A"
            cv2.putText(frame, f"ID:{track_id} | {spd_label}",
                        (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

            if is_ngebut:
                cv2.putText(frame, "NGEBUT!", (x1, y1 - 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    # ---- Debounce NGEBUT ----
    ngebut_counter = min(ngebut_counter + 2, NGEBUT_DEBOUNCE_FRAMES + 2) if ada_ngebut \
                     else max(ngebut_counter - 1, 0)
    status_jalan   = "NGEBUT" if ngebut_counter >= NGEBUT_DEBOUNCE_FRAMES else "AMAN"

    # ---- Cleanup memori (jangan biarkan dict membengkak) ----
    if results[0].boxes.id is not None:
        active_ids = set(results[0].boxes.id.int().cpu().tolist())
    else:
        active_ids = set()

    if len(track_data) > 300:
        for tid in [t for t in track_data if t not in active_ids]:
            track_data.pop(tid, None)
            speed_cache.pop(tid, None)

    # =================== 7. PUBLISH MQTT ===================
    if mqtt_connected:
        client.publish(MQTT_TOPIC, json.dumps({
            "kepadatan": kepadatan,
            "status"   : status_jalan
        }))

    # =================== 8. UI ===================
    # Garis virtual — LINE_A bawah (start), LINE_B atas (stop)
    cv2.line(frame, (0, line_a_y), (w_roi, line_a_y), (255, 120, 0), 1)
    cv2.putText(frame, "LINE A (start)", (5, line_a_y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 120, 0), 1)

    cv2.line(frame, (0, line_b_y), (w_roi, line_b_y), (0, 210, 210), 1)
    cv2.putText(frame, "LINE B (stop)", (5, line_b_y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 210, 210), 1)

    # Status utama
    cv2.putText(frame, f"Kendaraan: {kepadatan}", (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    status_color = (0, 0, 255) if status_jalan == "NGEBUT" else (0, 255, 0)
    cv2.putText(frame, f"Status: {status_jalan}", (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX, 1, status_color, 3)

    # Info kalibrasi
    cv2.putText(frame,
                f"Jarak garis: {JARAK_ANTAR_GARIS_METER}m | Threshold: {SPEED_THRESHOLD_KMH}km/h",
                (10, h_roi - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)

    cv2.imshow("Smart Safe Walking Path - Local Video Analysis", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# =================== 9. CLEANUP ===================
cap.release()
cv2.destroyAllWindows()
if mqtt_connected:
    client.loop_stop()
    client.disconnect()
    print("[MQTT] Koneksi ditutup.")