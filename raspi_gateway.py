import cv2
import json
import time
import serial
import threading
import paho.mqtt.client as mqtt

# ============================================================================
# KONFIGURASI UTAMA (ISI SEMUA BAGIAN YANG BERTANDA # TODO)
# ============================================================================

# --- 1. Koneksi Serial ESP32 <-> Raspberry Pi 3 ---
# Jika pakai kabel jumper GPIO (Pin 8 TX / Pin 10 RX): gunakan "/dev/serial0"
# Jika pakai kabel USB langsung dari ESP32 ke port USB Raspi: gunakan "/dev/ttyUSB0"
SERIAL_PORT = "/dev/serial0"  # TODO: sesuaikan port serial asli di Raspi (/dev/serial0 atau /dev/ttyUSB0)
BAUD_RATE = 115200            # Sesuai UART_BAUDRATE di config.h ESP32

# --- 2. Konfigurasi Broker MQTT ---
MQTT_BROKER = "broker.hivemq.com"  # TODO: ganti dengan alamat broker asli (mis. IP server Mosquitto / HiveMQ)
MQTT_PORT = 1883                   # TODO: sesuaikan port broker (default 1883)
MQTT_USERNAME = ""                 # TODO: isi username broker jika pakai autentikasi (kosongkan jika tidak)
MQTT_PASSWORD = ""                 # TODO: isi password broker jika pakai autentikasi (kosongkan jika tidak)
MQTT_CLIENT_ID = "raspi-silo-01"   # TODO: ganti jika butuh ID perangkat khusus

# --- 3. Daftar Topik MQTT ---
TOPIC_TELEMETRY = "TODO/silo/telemetry"  # TODO: isi dengan topik asli untuk data sensor ESP32
TOPIC_BARCODE   = "TODO/silo/barcode"    # TODO: isi dengan topik asli untuk data barang lolos scan QR
TOPIC_ALERT     = "TODO/silo/alert"      # TODO: isi dengan topik asli untuk peringatan gagal scan/timeout
TOPIC_COMMAND   = "TODO/silo/command"    # TODO: isi dengan topik asli perintah dari dashboard ke ESP32

# --- 4. Konfigurasi Webcam & Pemindaian QR ---
CAMERA_INDEX = 0             # TODO: ganti ke 1 jika webcam USB terdeteksi di indeks 1
FRAME_WIDTH = 640            # Resolusi ringan agar prosesor Raspi 3 tidak berat
FRAME_HEIGHT = 480
SCAN_TIMEOUT_SEC = 10        # TODO: sesuaikan batas waktu timeout scan (detik)
SHOW_CAMERA_WINDOW = False   # Biarkan False saat jalan di Raspi via SSH (True hanya jika pakai monitor HDMI)

# --- 5. Aturan Validasi Isi QR (Opsional) ---
# Kosongkan ("") jika semua isi QR dianggap valid, atau isi awalan tertentu mis. "BATCH-"
QR_REQUIRED_PREFIX = ""      # TODO: isi format awalan QR jika ada aturan validasi (kosongkan jika bebas)
DEVICE_ID = "SILO-01"        # TODO: sesuaikan ID silo jika backend meminta field device_id

# ============================================================================
# VARIABEL GLOBAL & STATE
# ============================================================================
ser = None
serial_lock = threading.Lock()
mqtt_connected = False

# Pengaman agar 1 barang di depan kamera tidak memicu scan berulang tiap 1 detik
item_already_handled = False
jumlah_barang = 0


# ============================================================================
# FUNGSI KOMUNIKASI SERIAL (RASPI <-> ESP32)
# ============================================================================
def connect_serial():
    global ser
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            time.sleep(2)  # Tunggu serial stabil
            ser.reset_input_buffer()
            print(f"[SERIAL] Terhubung ke ESP32 di {SERIAL_PORT} ({BAUD_RATE} bps)")
            return
        except serial.SerialException as e:
            print(f"[SERIAL] Gagal membuka {SERIAL_PORT}: {e}. Mencoba lagi dalam 3 detik...")
            time.sleep(3)


def kirim_perintah_ke_esp(perintah_dict):
    """Kirim perintah JSON ke ESP32 secara aman (thread-safe)."""
    global ser
    pesan = json.dumps(perintah_dict) + "\n"
    with serial_lock:
        if ser is not None and ser.is_open:
            try:
                ser.write(pesan.encode("utf-8"))
                ser.flush()
                print(f"[RASPI -> ESP32] Terkirim: {pesan.strip()}")
            except serial.SerialException as e:
                print(f"[SERIAL] Gagal kirim perintah: {e}")
        else:
            print(f"[SERIAL] Port belum siap, perintah batal dikirim: {pesan.strip()}")


# ============================================================================
# FUNGSI MQTT (PUBLISH & SUBSCRIBE PERINTAH DASHBOARD)
# ============================================================================
def on_connect(client, userdata, flags, reason_code, *args):
    global mqtt_connected
    if reason_code == 0:
        mqtt_connected = True
        print(f"[MQTT] Terhubung ke {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(TOPIC_COMMAND)
        print(f"[MQTT] Subscribe ke topik perintah: {TOPIC_COMMAND}")
    else:
        mqtt_connected = False
        print(f"[MQTT] Gagal konek, kode error: {reason_code}")


def on_disconnect(client, userdata, *args):
    global mqtt_connected
    mqtt_connected = False
    print("[MQTT] Koneksi terputus. Mencoba menyambung ulang otomatis...")


def on_message(client, userdata, msg):
    """Menerima perintah dari dashboard/backend lalu meneruskannya ke ESP32."""
    global item_already_handled
    payload_str = msg.payload.decode("utf-8", errors="ignore").strip()
    print(f"[MQTT -> RASPI] Perintah masuk ({msg.topic}): {payload_str}")

    try:
        # Format yang diharapkan ESP32: {"cmd": "FAN_ON"}, {"cmd": "SCAN_PASS"}, dll.
        data_cmd = json.loads(payload_str)
        cmd_name = str(data_cmd.get("cmd", "")).upper()
    except json.JSONDecodeError:
        # Jika dashboard mengirim teks mentah (mis. "SCAN_PASS"), bungkus ke JSON
        cmd_name = payload_str.upper()
        data_cmd = {"cmd": cmd_name}

    # Jika operator meminta scan ulang setelah timeout
    if cmd_name == "RETRY_SCAN":
        item_already_handled = False
        print("[SISTEM] Status scan direset oleh operator, siap memindai ulang.")
        return

    # Daftar perintah yang dikenali firmware ESP32 (communication.cpp)
    valid_cmds = {
        "SCAN_PASS", "SCAN_DONE", "RESUME_CONVEYOR", "RUN_CONVEYOR",
        "STOP_CONVEYOR", "CONV_STOP", "VENT_OPEN", "VENT_CLOSE",
        "FAN_ON", "FAN_OFF"
    }
    if cmd_name in valid_cmds:
        kirim_perintah_ke_esp(data_cmd)
    else:
        print(f"[WARNING] Perintah MQTT tidak dikenali ESP32: {cmd_name}")


def setup_mqtt():
    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    else:
        client = mqtt.Client(client_id=MQTT_CLIENT_ID)

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
    except Exception as e:
        print(f"[MQTT] Gagal inisialisasi koneksi awal: {e}")

    return client


def publish_json(client, topic, payload_dict):
    if not mqtt_connected:
        print(f"[MQTT] Belum terhubung, data ke '{topic}' dilewati.")
        return
    result = client.publish(topic, json.dumps(payload_dict), qos=1)
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        print(f"[RASPI -> MQTT] Publish ke '{topic}': {json.dumps(payload_dict)}")
    else:
        print(f"[MQTT] Gagal publish ke '{topic}', rc={result.rc}")


# ============================================================================
# FUNGSI PEMINDAIAN QR CODE VIA WEBCAM
# ============================================================================
def scan_qr_webcam(timeout_sec=SCAN_TIMEOUT_SEC):
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[KAMERA] Gagal membuka webcam indeks {CAMERA_INDEX}.")
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    # Buang beberapa frame awal agar pencahayaan/auto-exposure webcam stabil
    for _ in range(5):
        cap.read()

    detector = cv2.QRCodeDetector()
    start_time = time.time()
    hasil_qr = None

    print(f"[KAMERA] Mulai memindai QR (timeout {timeout_sec} detik)...")

    while time.time() - start_time < timeout_sec:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        data, _, _ = detector.detectAndDecode(frame)
        if data:
            hasil_qr = data.strip()
            break

        if SHOW_CAMERA_WINDOW:
            cv2.imshow("Scanner Silo", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        time.sleep(0.05)  # Kurangi beban CPU Raspberry Pi 3

    cap.release()
    if SHOW_CAMERA_WINDOW:
        cv2.destroyAllWindows()

    return hasil_qr


def parse_json_line(raw_line):
    """
    Mengekstrak objek JSON dari baris serial.
    Aman baik untuk jalur UART2 bersih maupun jalur USB yang punya awalan '[TELEMETRY -> RASPI]: '.
    """
    start_idx = raw_line.find("{")
    end_idx = raw_line.rfind("}")
    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        return None
    try:
        return json.loads(raw_line[start_idx : end_idx + 1])
    except json.JSONDecodeError:
        return None


# ============================================================================
# PROGRAM UTAMA
# ============================================================================
def main():
    global item_already_handled, jumlah_barang

    print("=== MEMULAI RASPBERRY PI SILO GATEWAY ===")
    mqtt_client = setup_mqtt()
    connect_serial()

    try:
        while True:
            try:
                if ser.in_waiting == 0:
                    time.sleep(0.05)
                    continue

                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not raw_line:
                    continue

            except serial.SerialException as e:
                print(f"[SERIAL] Koneksi serial terputus: {e}")
                ser.close()
                connect_serial()
                continue

            telemetry = parse_json_line(raw_line)
            if telemetry is None:
                continue

            # TODO: sesuaikan struktur field payload telemetri ini dengan struct Go di backend/internal/model/model.go
            telemetry_payload = {
                "device_id": DEVICE_ID,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **telemetry,
            }
            publish_json(mqtt_client, TOPIC_TELEMETRY, telemetry_payload)

            conv_phase = telemetry.get("conv_phase", "")

            # Reset penanda begitu barang sudah bergerak meninggalkan titik webcam
            if conv_phase != "AT_WEBCAM":
                item_already_handled = False
                continue

            # Jika barang baru berhenti tepat di depan kamera (AT_WEBCAM), lakukan scan 1 kali
            if conv_phase == "AT_WEBCAM" and not item_already_handled:
                item_already_handled = True  # Kunci agar tidak scan ulang terus-menerus

                qr_text = scan_qr_webcam(timeout_sec=SCAN_TIMEOUT_SEC)

                if qr_text:
                    # Cek validasi prefix jika diaktifkan
                    is_valid = (not QR_REQUIRED_PREFIX) or qr_text.startswith(QR_REQUIRED_PREFIX)

                    if is_valid:
                        jumlah_barang += 1
                        # TODO: sesuaikan nama field JSON data barang ini dengan yang diterima backend/dashboard
                        barcode_payload = {
                            "device_id": DEVICE_ID,
                            "barcode": qr_text,
                            "status": "PASS",
                            "jumlah_barang": jumlah_barang,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        print(f"[SUKSES] QR terbaca: {qr_text} | Total barang: {jumlah_barang}")
                        publish_json(mqtt_client, TOPIC_BARCODE, barcode_payload)
                        kirim_perintah_ke_esp({"cmd": "SCAN_PASS"})
                    else:
                        # TODO: sesuaikan penanganan jika barang tidak sesuai format (REJECT)
                        reject_payload = {
                            "device_id": DEVICE_ID,
                            "barcode": qr_text,
                            "status": "REJECT",
                            "message": "Format QR tidak sesuai standar",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        print(f"[REJECT] QR tidak valid: {qr_text}. Menunggu tindakan operator.")
                        publish_json(mqtt_client, TOPIC_ALERT, reject_payload)
                else:
                    # TODO: sesuaikan format JSON alert timeout dengan backend/dashboard
                    alert_payload = {
                        "device_id": DEVICE_ID,
                        "barcode": None,
                        "status": "TIMEOUT",
                        "message": "Gagal memindai QR hingga batas waktu, menunggu operator",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    print("[GAGAL] Timeout scan QR! Alert dikirim, menunggu operator.")
                    publish_json(mqtt_client, TOPIC_ALERT, alert_payload)

    except KeyboardInterrupt:
        print("\n[SISTEM] Program dihentikan oleh pengguna.")
    finally:
        if ser is not None and ser.is_open:
            ser.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("[SISTEM] Koneksi serial dan MQTT ditutup dengan aman.")


if __name__ == "__main__":
    main()