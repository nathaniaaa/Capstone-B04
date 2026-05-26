import cv2
import time
import math
import serial
import yt_dlp
import threading
from ultralytics import YOLO

# ================= 1. KELAS MULTI-THREADING =================
class VideoStreamWidget:
    def __init__(self, src):
        self.capture = cv2.VideoCapture(src)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.status, self.frame = self.capture.read()
        self.stopped = False
        self.thread = threading.Thread(target=self.update, args=(), daemon=True)
        self.thread.start()

    def update(self):
        while not self.stopped:
            if self.capture.isOpened():
                self.status, self.frame = self.capture.read()
            time.sleep(0.01)

    def read(self):
        return self.status, self.frame

    def stop(self):
        self.stopped = True
        self.capture.release()

# ================= 2. KONFIGURASI SERIAL ESP32 =================
try:
    ser = serial.Serial('COM3', 115200, timeout=0.1) 
    serial_connected = True
    print("Serial terhubung!")
except:
    serial_connected = False
    print("Peringatan: Serial ESP32 tidak terhubung. Mode Simulasi GUI.")

# ================= 3. LOAD MODEL YOLO (KEMBALI KE NANO) =================
print("Memuat model YOLOv8 Nano...")
# Kita pakai NANO lagi agar ringan di CPU laptop
model = YOLO("yolov8n.pt") 

# ================= 4. EKSTRAK LIVE YOUTUBE =================
youtube_url = "https://www.youtube.com/watch?v=ijG22Q85GRg"

print("Mengekstrak link streaming dari YouTube...")
ydl_opts = {'format': 'best', 'quiet': True}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
        stream_url = info['url']
except Exception as e:
    print(f"Gagal mengekstrak YouTube: {e}")
    exit()

# ================= 5. KONEKSI STREAMING =================
print("Membuka CCTV YouTube secara Multi-Threading...")
cap = VideoStreamWidget(stream_url)
time.sleep(2) 

if not cap.status:
    print("GAGAL membuka stream kamera.")
    exit()

track_history = {}
SPEED_THRESHOLD = 300 

# ================= 6. LOOP UTAMA DENGAN ROI =================
while True:
    ret, original_frame = cap.read()
    if not ret or original_frame is None:
        continue
    
    # --- TEKNIK CHEAT CODE: REGION OF INTEREST (ROI) ---
    # Kita potong 40% bagian atas video (buang langit dan pohon)
    h, w = original_frame.shape[:2]
    batas_potong = int(h * 0.4) 
    
    # frame sekarang HANYA berisi jalan raya saja
    frame = original_frame[batas_potong:h, 0:w] 
    
    # YOLO jalan di area yang lebih kecil (sangat meringankan CPU)
    # Kita pertahankan conf=0.15 agar motor tetap terbaca
    results = model.track(frame, persist=True, classes=[2, 3, 5, 7], conf=0.15, imgsz=640, verbose=False)
    
    kepadatan = 0
    status_jalan = "AMAN"

    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xywh.cpu()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        kepadatan = len(track_ids)

        for box, track_id in zip(boxes, track_ids):
            x, y, w, h_box = box
            current_time = time.time()
            current_center = (float(x), float(y))

            if track_id not in track_history:
                track_history[track_id] = (current_center, current_time)
            else:
                old_center, old_time = track_history[track_id]
                time_diff = current_time - old_time

                if time_diff > 0.2:
                    dx = current_center[0] - old_center[0]
                    dy = current_center[1] - old_center[1]
                    
                    if dy > 0: 
                        jarak_piksel = math.sqrt(dx**2 + dy**2)
                        pixel_speed = jarak_piksel / time_diff
                        
                        if pixel_speed > SPEED_THRESHOLD:
                            status_jalan = "NGEBUT"
                            cv2.putText(frame, "NGEBUT!", (int(x), int(y)-20), 
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                            cv2.rectangle(frame, (int(x-w/2), int(y-h_box/2)), 
                                          (int(x+w/2), int(y+h_box/2)), (0, 0, 255), 3)

                    track_history[track_id] = (current_center, current_time)

    # ================= 7. MENGIRIM DATA KE ESP32 =================
    data_to_send = f"<{kepadatan},{status_jalan}>\n"
    if serial_connected:
        ser.write(data_to_send.encode('utf-8'))

    # ================= 8. TAMPILAN UI =================
    cv2.putText(frame, f"Kendaraan: {kepadatan}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(frame, f"Status: {status_jalan}", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 1, 
                (0, 0, 255) if status_jalan == "NGEBUT" else (0, 255, 0), 3)

    # Tampilkan layar yang sudah dipotong (Cuma jalanan)
    cv2.imshow("Smart Safe Walking Path - AI Vision Mode", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.stop()
cv2.destroyAllWindows()
if serial_connected:
    ser.close()