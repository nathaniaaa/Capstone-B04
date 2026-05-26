import cv2
import time
from ultralytics import YOLO

print("Loading model YOLOv8...")
model = YOLO('best.pt')
# cap = cv2.VideoCapture(0) # Ganti ke 1 atau 2 kalau pakai webcam eksternal
cap = cv2.VideoCapture(2)

# Koordinat Garis Virtual
LINE_START_Y = 150
LINE_END_Y = 350

entry_times = {}

# --- VARIABEL STATE MEMORY (Perbaikan Logika) ---
waktu_ngebut_terakhir = 0  # Menyimpan kapan terakhir ada mobil ngebut
waktu_pir_aktif = 0        # Menyimpan kapan terakhir tombol 'P' (PIR) ditekan
durasi_tahan_ngebut = 2.0  # Status NGEBUT bertahan 2 detik
durasi_latch_pir = 3.0     # Sensor PIR menahan sinyal selama 3 detik

# --- VARIABEL TIMER LAMPU PEJALAN KAKI ---
timer_berjalan = False
waktu_sisa = 0
waktu_update_terakhir = time.time()
pesan_sistem = "Standby (Kosong)"

while cap.isOpened():
    success, frame = cap.read()
    if not success: break
    
    # HAPUS parameter classes, dan paksa confidence turun drastis ke 10% (0.10)
    results = model.track(frame, persist=True, conf=0.10, verbose=False)
    
    current_density = 0
    active_ids = []

    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        
        # Set default ke 0 di awal loop
    current_density = 0
    active_ids = []

    # Cek apakah ada objek yang TERDETEKSI (jangan bergantung pada ID dulu)
    if len(results[0].boxes) > 0:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        current_density = len(boxes) # Hitung jumlah mobil dari jumlah kotak!

        # Lanjutkan logika tracking hanya JIKA YOLO berhasil memberikan ID
        if results[0].boxes.id is not None:
            track_ids = results[0].boxes.id.int().cpu().tolist()
            active_ids = track_ids 
            
            for box, track_id in zip(boxes, track_ids):
                x1, y1, x2, y2 = box
                center_y = int((y1 + y2) / 2)
                
                # Gambar Kotak Mobil
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                cv2.putText(frame, f"ID: {track_id}", (int(x1), int(y1) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                
                # Logika Start Line
                if LINE_START_Y - 10 < center_y < LINE_START_Y + 10:
                    if track_id not in entry_times:
                        entry_times[track_id] = time.time()
                
                # Logika End Line
                if LINE_END_Y - 10 < center_y < LINE_END_Y + 10:
                    if track_id in entry_times:
                        time_taken = time.time() - entry_times[track_id]
                        
                        # THRESHOLD NGEBUT
                        if time_taken < 1.5: 
                            waktu_ngebut_terakhir = time.time()
                        
                        del entry_times[track_id]

    # Hapus memori mobil yang diangkat keluar frame
    keys_to_delete = [k for k in entry_times.keys() if k not in active_ids]
    for k in keys_to_delete:
        del entry_times[k]

    # --- TENTUKAN STATUS KENDARAAN (Pakai Memori) ---
    if time.time() - waktu_ngebut_terakhir < durasi_tahan_ngebut:
        status_kendaraan = "NGEBUT"
    else:
        status_kendaraan = "AMAN"

    # --- BACA TOMBOL KEYBOARD (Simulasi PIR) ---
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('p'):
        # Orang terdeteksi! Tahan sinyal selama 3 detik.
        waktu_pir_aktif = time.time()

    is_pedestrian_waiting = (time.time() - waktu_pir_aktif < durasi_latch_pir)

    # --- LOGIKA SISTEM PENYEBERANGAN ---
    if not timer_berjalan:
        if is_pedestrian_waiting:
            # Kalau ada pejalan kaki, cek jalannya aman nggak?
            if status_kendaraan == "NGEBUT":
                pesan_sistem = "HOLD! ADA MOBIL NGEBUT!"
            else:
                # Jalan aman, mulai timer dinamis!
                waktu_sisa = 10 if current_density >= 3 else 15
                timer_berjalan = True
                waktu_update_terakhir = time.time()
                pesan_sistem = f"LAMPU HIJAU: {waktu_sisa} detik"
                
                # Reset PIR supaya nggak nambah-nambah terus
                waktu_pir_aktif = 0 
        else:
            # Tidak ada orang nyebrang
            pesan_sistem = "Standby (Tekan 'P' utk PIR)"

    # --- LOGIKA HITUNG MUNDUR TIMER LAMPU HIJAU ---
    if timer_berjalan:
        if time.time() - waktu_update_terakhir >= 1.0:
            waktu_sisa -= 1
            waktu_update_terakhir = time.time()
            pesan_sistem = f"LAMPU HIJAU: {waktu_sisa} detik"
            
        if waktu_sisa <= 0:
            timer_berjalan = False
            pesan_sistem = "Standby (Tekan 'P' utk PIR)"


    # --- GAMBAR UI DASHBOARD ---
    cv2.line(frame, (0, LINE_START_Y), (frame.shape[1], LINE_START_Y), (255, 255, 0), 2)
    cv2.putText(frame, "START", (10, LINE_START_Y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)
    
    cv2.line(frame, (0, LINE_END_Y), (frame.shape[1], LINE_END_Y), (0, 0, 255), 2)
    cv2.putText(frame, "END", (10, LINE_END_Y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    
    # Kotak Background UI
    cv2.rectangle(frame, (10, 10), (450, 110), (0, 0, 0), -1)
    
    # Teks UI Kepadatan
    cv2.putText(frame, f"Kepadatan : {current_density} Mobil", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    # Teks UI Kecepatan
    warna_kendaraan = (0, 0, 255) if status_kendaraan == "NGEBUT" else (0, 255, 0)
    cv2.putText(frame, f"Kecepatan : {status_kendaraan}", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, warna_kendaraan, 2)
    
    # Teks UI Pesan Sistem Pejalan Kaki
    warna_sistem = (0, 255, 255) if timer_berjalan else (0, 165, 255)
    if "HOLD" in pesan_sistem: warna_sistem = (0, 0, 255)
    cv2.putText(frame, f"Sistem : {pesan_sistem}", (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, warna_sistem, 2)

    # Tampilkan Video
    cv2.imshow("Dashboard CV - Smart Safe Walking Path", frame)

cap.release()
cv2.destroyAllWindows()