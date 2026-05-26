import cv2
import time
from ultralytics import YOLO

print("Loading model YOLOv8...")
model = YOLO('yolov8n.pt')
# cap = cv2.VideoCapture(0) # Ganti ke 1 atau 2 kalau pakai webcam eksternal
cap = cv2.VideoCapture(2)

# Koordinat Garis Virtual
LINE_START_Y = 150
LINE_END_Y = 350

entry_times = {}

# --- VARIABEL SIMULASI TIMER & PIR ---
timer_berjalan = False
waktu_sisa = 0
waktu_update_terakhir = time.time()
pesan_sistem = "Standby (Tekan 'P' untuk nyebrang)"

while cap.isOpened():
    success, frame = cap.read()
    if not success: break
    
    # Deteksi dan Tracking
    results = model.track(frame, persist=True, classes=[2, 3, 5, 7], verbose=False)
    
    current_density = 0
    status_kendaraan = "AMAN"
    active_ids = []

    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        
        current_density = len(track_ids)
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
                    
                    # THRESHOLD NGEBUT (Ubah angka 1.5 ini sesuai kecepatan tanganmu saat dorong diecast)
                    if time_taken < 1.5: 
                        status_kendaraan = "NGEBUT"
                    
                    del entry_times[track_id] 

    # Hapus memori mobil yang diangkat keluar frame
    keys_to_delete = [k for k in entry_times.keys() if k not in active_ids]
    for k in keys_to_delete:
        del entry_times[k]

    # --- BACA TOMBOL KEYBOARD ---
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('p') and not timer_berjalan:
        # SIMULASI PIR: Kalau ada yg mau nyebrang, cek kondisi jalan!
        if status_kendaraan == "NGEBUT":
            pesan_sistem = "HOLD! ADA MOBIL NGEBUT!"
        else:
            # Set timer dinamis berdasarkan kepadatan
            waktu_sisa = 10 if current_density >= 3 else 15
            timer_berjalan = True
            waktu_update_terakhir = time.time()
            pesan_sistem = f"LAMPU HIJAU: {waktu_sisa} detik"

    # --- LOGIKA HITUNG MUNDUR TIMER ---
    if timer_berjalan:
        # Kurangi 1 detik setiap kali 1 detik waktu nyata berlalu
        if time.time() - waktu_update_terakhir >= 1.0:
            waktu_sisa -= 1
            waktu_update_terakhir = time.time()
            pesan_sistem = f"LAMPU HIJAU: {waktu_sisa} detik"
            
        # Kalau timer habis, matikan lampu
        if waktu_sisa <= 0:
            timer_berjalan = False
            pesan_sistem = "Standby (Tekan 'P' untuk nyebrang)"
    else:
        # Update tampilan hold jika lagi ngebut tapi ga ada yg nyebrang
        if status_kendaraan == "NGEBUT":
            pesan_sistem = "BAHAYA: KENDARAAN NGEBUT!"
        elif not timer_berjalan and pesan_sistem == "BAHAYA: KENDARAAN NGEBUT!":
             pesan_sistem = "Standby (Tekan 'P' untuk nyebrang)"


    # --- GAMBAR UI DASHBOARD ---
    cv2.line(frame, (0, LINE_START_Y), (frame.shape[1], LINE_START_Y), (255, 255, 0), 2)
    cv2.putText(frame, "START", (10, LINE_START_Y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)
    
    cv2.line(frame, (0, LINE_END_Y), (frame.shape[1], LINE_END_Y), (0, 0, 255), 2)
    cv2.putText(frame, "END", (10, LINE_END_Y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    
    # Kotak Background UI
    cv2.rectangle(frame, (10, 10), (450, 110), (0, 0, 0), -1)
    
    # Teks UI
    cv2.putText(frame, f"Kepadatan : {current_density} Mobil", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    warna_kendaraan = (0, 0, 255) if status_kendaraan == "NGEBUT" else (0, 255, 0)
    cv2.putText(frame, f"Kecepatan : {status_kendaraan}", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, warna_kendaraan, 2)
    
    warna_sistem = (0, 255, 255) if timer_berjalan else (0, 165, 255)
    if "HOLD" in pesan_sistem or "BAHAYA" in pesan_sistem: warna_sistem = (0, 0, 255)
    cv2.putText(frame, f"Sistem : {pesan_sistem}", (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, warna_sistem, 2)

    # Tampilkan Video
    cv2.imshow("Dashboard CV - Smart Safe Walking Path", frame)

cap.release()
cv2.destroyAllWindows()