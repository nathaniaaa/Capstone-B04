import cv2

print("Mencoba membuka kamera...")
# Angka 0 biasanya untuk kamera bawaan laptop. 
# Kalau pakai webcam USB eksternal, biasanya angkanya 1 atau 2.
cap = cv2.VideoCapture(2)

# Cek apakah kamera berhasil diakses oleh Python
if not cap.isOpened():
    print("❌ ERROR: Kamera gagal dibuka!")
    print("Saran: Coba ganti cv2.VideoCapture(0) menjadi (1) atau (2).")
    exit()

print("✅ BERHASIL! Kamera terbuka. Tekan tombol 'q' pada keyboard untuk keluar.")

while True:
    # Baca frame (gambar) dari kamera per sekon
    ret, frame = cap.read()
    
    if not ret:
        print("❌ ERROR: Gagal membaca frame/gambar dari kamera.")
        break

    # Tampilkan jendela video
    cv2.imshow("Test Kamera OpenCV", frame)

    # Logika untuk menutup jendela jika tombol 'q' ditekan
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Bersihkan dan tutup semua proses setelah selesai
cap.release()
cv2.destroyAllWindows()