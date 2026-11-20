# Raspberry Pi Silo Gateway & QR Scanner (`raspi-silo-gateway`)

Repositori ini berisi program **Gateway IoT dan Pemindai QR Code** berbasis Python yang berjalan di **Raspberry Pi 3** untuk proyek *Sistem Monitoring & Pengendalian Silo Raw Material dengan PID Conveyor*.

Program ini berperan sebagai penghubung dua arah antara **mikrokontroler ESP32** (perangkat lapangan) dengan **Broker MQTT** (yang terhubung ke Backend Go dan Web Dashboard Laravel), sekaligus menangani pemindaian identitas bahan baku melalui *webcam* USB.

---

## 1. Struktur Folder

```text
raspi-silo-gateway/
├── raspi_gateway.py      # Program utama gateway (Serial ESP32 <-> Webcam QR <-> MQTT)
├── requirements.txt      # Daftar library Python untuk instalasi di Raspberry Pi 3
├── buat_qr.py            # Skrip pembuat gambar QR Code dummy untuk pengujian
├── qr_batch_001.png      # Contoh gambar QR Code Batch 1
├── qr_batch_002.png      # Contoh gambar QR Code Batch 2
└── README.md             # Dokumentasi penjelasan kode dan panduan konfigurasi
```

---

## 2. Alur Kerja Sistem

Secara ringkas, alur kerja `raspi_gateway.py` berjalan terus-menerus (*looping*) dalam empat tahap utama:

1. **Menerima Telemetri dari ESP32 (Serial/UART):** Raspberry Pi membaca baris data JSON yang dikirim ESP32 setiap 1 detik (berisi suhu, kelembapan, nilai gas MQ-2, jarak ultrasonik, galat PID, fase *conveyor*, status silo, status kipas, dan sudut servo).
2. **Meneruskan Telemetri ke MQTT:** Data dari ESP32 ditambahkan `device_id` dan `timestamp`, lalu dipublikasikan (*publish*) ke topik telemetri di *broker* MQTT agar bisa disimpan oleh *backend* dan ditampilkan di *dashboard*.
3. **Pemindaian QR Otomatis saat Barang Berhenti (`AT_WEBCAM`):**
   * Begitu ESP32 melaporkan `"conv_phase": "AT_WEBCAM"`, Raspberry Pi otomatis mengaktifkan *webcam* USB untuk memindai QR Code pada kotak barang.
   * **Jika QR terbaca dan valid:** Penghitung `jumlah_barang` bertambah `+1`, data barang dipublikasikan ke topik *barcode* MQTT, dan Raspberry Pi mengirim perintah `{"cmd": "SCAN_PASS"}` ke ESP32 agar *conveyor* mengalirkan barang masuk ke silo.
   * **Jika QR tidak sesuai format (*REJECT*) atau melewati batas waktu (*TIMEOUT*):** Raspberry Pi mengirim peringatan ke topik *alert* MQTT dan menahan *conveyor* sampai ada tindakan dari operator.
4. **Menerima Perintah dari Dashboard (MQTT $\rightarrow$ ESP32):** Program berlangganan (*subscribe*) pada topik perintah MQTT. Jika operator menekan tombol kontrol di *dashboard* (misal nyalakan kipas, buka ventilasi, lanjutkan *conveyor*, atau *scan* ulang), Raspberry Pi langsung meneruskan perintah tersebut ke ESP32 lewat jalur serial.

---

## 3. Penjelasan Fungsi Utama di `raspi_gateway.py`

| Nama Fungsi | Peran dan Cara Kerja |
| :--- | :--- |
| `connect_serial()` | Membuka koneksi serial ke ESP32 pada *baudrate* 115200 bps. Dilengkapi percobaan ulang otomatis tiap 3 detik jika kabel belum tersambung atau terputus. |
| `kirim_perintah_ke_esp()` | Mengirimkan paket perintah JSON (diakhiri karakter `\n`) ke ESP32 secara *thread-safe* menggunakan `serial_lock` agar pengiriman dari *thread* MQTT tidak bentrok dengan *loop* utama. |
| `setup_mqtt()`, `on_connect()`, `on_disconnect()` | Menginisialisasi klien Paho MQTT (kompatibel untuk Paho v1 maupun v2), menangani status koneksi, dan otomatis *subscribe* ke `TOPIC_COMMAND` saat terhubung. |
| `on_message()` | Memproses perintah yang masuk dari *dashboard* via MQTT, memvalidasinya terhadap daftar perintah yang dikenali firmware ESP32, lalu meneruskannya ke ESP32. Juga menangani perintah khusus `RETRY_SCAN` untuk mereset kunci pemindaian. |
| `publish_json()` | Mengubah *dictionary* Python menjadi teks JSON dan memublikasikannya ke topik MQTT tujuan dengan QoS 1. |
| `scan_qr_webcam()` | Membuka *webcam* pada resolusi 640×480 (ringan untuk Raspi 3), membuang 5 *frame* awal agar pencahayaan stabil, lalu membaca QR Code menggunakan `cv2.QRCodeDetector()` hingga berhasil atau mencapai batas `SCAN_TIMEOUT_SEC`. |
| `parse_json_line()` | Mengekstrak bagian `{ ... }` dari teks serial yang diterima. Aman digunakan baik saat ESP32 mengirim JSON murni lewat UART2 maupun saat mengirim log berawalan `[TELEMETRY -> RASPI]: ` lewat USB. |
| `main()` | Menjalankan *loop* utama: membaca serial, meneruskan telemetri, serta mengatur variabel pengaman `item_already_handled` agar satu barang yang diam di depan kamera hanya dipindai **satu kali** (tidak berulang tiap detik). |

---

## 4. Perintah Kontrol yang Didukung (MQTT $\rightarrow$ ESP32)

Perintah yang dikirim ke `TOPIC_COMMAND` dapat berupa JSON `{"cmd": "NAMA_PERINTAH"}` maupun teks langsung:

* **`SCAN_PASS` / `SCAN_DONE` / `RESUME_CONVEYOR` / `RUN_CONVEYOR`**: Melanjutkan *conveyor* agar barang masuk ke silo (fase `DRAIN_SILO`).
* **`STOP_CONVEYOR` / `CONV_STOP`**: Menghentikan *conveyor* secara darurat/manual.
* **`FAN_ON` / `FAN_OFF`**: Menyalakan atau mematikan paket 2 kipas *exhaust* secara manual.
* **`VENT_OPEN` / `VENT_CLOSE`**: Membuka (90°) atau menutup (0°) katup ventilasi servo SG90 secara manual.
* **`RETRY_SCAN`**: Dijalankan di sisi Raspberry Pi untuk mengulang pemindaian *webcam* setelah terjadi *timeout* atau *reject*.

---

## 5. Yang Harus Dilakukan Selanjutnya (Checklist `# TODO`)

Sebelum program dijalankan secara penuh bersama sistem *backend* dan perangkat keras, selesaikan daftar konfigurasi bertanda **`# TODO`** di bagian atas file `raspi_gateway.py`:

- [ ] **1. Tentukan Port Serial (`SERIAL_PORT`)**
  * Gunakan `"/dev/serial0"` jika ESP32 dihubungkan ke Raspberry Pi lewat kabel *jumper* GPIO (Pin 8 TX / Pin 10 RX).
  * Gunakan `"/dev/ttyUSB0"` jika ESP32 langsung dicolok ke port USB Raspberry Pi memakai kabel data USB.
- [ ] **2. Sesuaikan Alamat Broker MQTT (`MQTT_BROKER`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`)**
  * Samakan persis dengan konfigurasi yang dipakai di `backend/.env` pada repositori *dashboard* (`silo-monitoring`).
- [ ] **3. Isi Nama Topik MQTT Asli**
  * Ganti placeholder pada `TOPIC_TELEMETRY`, `TOPIC_BARCODE`, `TOPIC_ALERT`, dan `TOPIC_COMMAND` sesuai topik yang disepakati di kode *backend* Go (`backend/internal/mqttclient/mqtt.go`).
- [ ] **4. Cocokkan Nama Field JSON dengan Backend**
  * Periksa kembali kunci JSON di `telemetry_payload`, `barcode_payload`, dan `alert_payload` pada fungsi `main()` agar cocok dengan struktur *struct* penerima di `backend/internal/model/model.go`.
- [ ] **5. Atur Parameter Webcam & Validasi QR**
  * Cek `CAMERA_INDEX` (`0` atau `1`) setelah *webcam* USB dicolok ke Raspberry Pi.
  * Tentukan durasi `SCAN_TIMEOUT_SEC` (standar: `10` detik) dan isi `QR_REQUIRED_PREFIX` (misal `"BATCH-"`) jika ingin mengaktifkan penolakan otomatis untuk QR yang tidak sesuai standar.
  * Biarkan `SHOW_CAMERA_WINDOW = False` jika Raspberry Pi dijalankan tanpa monitor (lewat SSH).

---

## 6. Panduan Instalasi & Menjalankan di Raspberry Pi 3

### A. Persiapan Perangkat Keras
1. **Catu Daya:** Pastikan Raspberry Pi 3 disuplai dari regulator *Step-down* 1 (5,15 V – 5,20 V) agar tidak mengalami *undervoltage*.
2. **Koneksi ESP32 $\leftrightarrow$ Raspberry Pi:**
   * **Opsi Kabel Jumper UART2:** Hubungkan `GPIO 17 (TX2)` ESP32 ke `Pin 10 (RXD)` Raspi, `GPIO 16 (RX2)` ESP32 ke `Pin 8 (TXD)` Raspi, dan satukan jalur **GND** keduanya.
   * **Opsi Kabel USB (Lebih Praktis):** Colokkan kabel USB ESP32 langsung ke port USB Raspberry Pi 3.
3. **Webcam USB:** Colokkan *webcam* ke port USB Raspberry Pi 3 dan posisikan menghadap titik henti *conveyor* (jarak *setpoint* 15,0 cm).
4. **Koneksi Internet:** Hubungkan Raspberry Pi 3 ke jaringan WiFi yang sama dengan *broker* MQTT / *server*.

### B. Aktifkan Serial Port (Khusus Opsi Kabel Jumper GPIO)
Jika memakai kabel *jumper* Pin 8 & Pin 10, jalankan perintah ini di terminal Raspberry Pi:
```bash
sudo raspi-config
```
Pilih **Interface Options** $\rightarrow$ **Serial Port**:
* *"Would you like a login shell to be accessible over serial?"* $\rightarrow$ Pilih **No**.
* *"Would you like the serial port hardware to be enabled?"* $\rightarrow$ Pilih **Yes**.
* Simpan lalu *reboot* Raspberry Pi (`sudo reboot`).

### C. Instalasi Library & Menjalankan Program
1. *Clone* repositori ini ke Raspberry Pi:
   ```bash
   git clone https://github.com/Alzbayne19/<nama-repo-kamu>.git
   cd <nama-repo-kamu>
   ```
2. Install dependensi Python:
   ```bash
   pip3 install -r requirements.txt
   ```
   *(Jika muncul pesan `externally-managed-environment` pada Raspberry Pi OS versi baru, buat virtual environment dulu dengan `python3 -m venv venv && source venv/bin/activate`, lalu jalankan `pip install -r requirements.txt`.)*
3. Jalankan skrip gateway:
   ```bash
   python3 raspi_gateway.py
   ```

---

## 7. Troubleshooting Singkat

* **Permission denied saat membuka `/dev/ttyUSB0` atau `/dev/serial0`:** Tambahkan user ke grup `dialout` dengan perintah `sudo usermod -a -G dialout $USER`, lalu *reboot*.
* **Webcam tidak terbaca:** Jalankan `ls /dev/video*` di terminal Raspi untuk memastikan *webcam* terdeteksi, lalu sesuaikan angka `CAMERA_INDEX`.
* **QR Code sulit terbaca saat uji fisik:** Gunakan kertas HVS tidak mengilap dengan ukuran QR minimal 3,5 × 3,5 cm, sisakan margin putih di tepi QR, dan pastikan area kamera memiliki pencahayaan cukup.