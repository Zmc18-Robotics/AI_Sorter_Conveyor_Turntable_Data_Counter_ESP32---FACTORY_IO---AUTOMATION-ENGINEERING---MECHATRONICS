"""
Conveyor Sorting System v6
===========================
Factory IO Vision Sensor = 1 sensor per warna (bit input)

Mapping:
  INPUT 5 = Vision Sensor HIJAU  → LURUS (atas)
  INPUT 6 = Vision Sensor BIRU   → KIRI
  INPUT 7 = Vision Sensor ABU    → KANAN

  COIL 1  = Emitter
  COIL 2  = Belt Conveyor 1
  COIL 3  = Belt Conveyor 2
  COIL 4  = Belt Conveyor 3
  COIL 5  = Belt Conveyor 4
  COIL 6  = Belt Conveyor 5
  COIL 7  = Belt Conveyor 6
  COIL 8  = Turntable Turn
  COIL 9  = Turntable Roll (+)
  COIL 10 = Turntable Roll (-)
  COIL 11 = Curved Belt
  COIL 14 = Warning Light 1 (Capacitive)
  COIL 15 = Warning Light 2 (Vision)
"""

import time
import threading
import serial
import serial.tools.list_ports
from pymodbus.client import ModbusTcpClient

# ========================== KONFIGURASI ==========================
MODBUS_HOST     = "192.168.8.110"
MODBUS_PORT     = 502
MODBUS_SLAVE_ID = 1

# --- INPUT ---
INPUT_CAPACITIVE    = 0
INPUT_LIMIT_0       = 1
INPUT_LIMIT_90      = 2
INPUT_BACK_LIMIT    = 3
INPUT_FRONT_LIMIT   = 4
INPUT_VISION_GREEN  = 5
INPUT_VISION_BLUE   = 6
INPUT_VISION_GRAY   = 7

# --- COIL ---
COIL_EMITTER     = 1
COIL_CONVEYOR_1  = 2
COIL_CONVEYOR_2  = 3
COIL_CONVEYOR_3  = 4
COIL_CONVEYOR_4  = 5
COIL_CONVEYOR_5  = 6
COIL_CONVEYOR_6  = 7
COIL_TT_TURN     = 8
COIL_TT_ROLL_POS = 9
COIL_TT_ROLL_NEG = 10
COIL_CURVED_BELT  = 11
COIL_SCALE_POS    = 12
COIL_SCALE_NEG    = 13
COIL_WARN_CAP     = 14   # Warning Light 1 → Capacitive sensor
COIL_WARN_VISION  = 15   # Warning Light 2 → Vision sensor

ALWAYS_ON_COILS = [
    COIL_EMITTER,
    COIL_CONVEYOR_1, COIL_CONVEYOR_2, COIL_CONVEYOR_3, COIL_CONVEYOR_4,
    COIL_CONVEYOR_5, COIL_CONVEYOR_6,
    COIL_CURVED_BELT,
    COIL_SCALE_POS,
]

INPUT_CONVEYORS = [
    COIL_CONVEYOR_1, COIL_CONVEYOR_2,
    COIL_CONVEYOR_3, COIL_CONVEYOR_4,
]

# --- TIMING ---
POLL_INTERVAL       = 0.05
COIL_REFRESH_RATE   = 0.5
TURNTABLE_TURN_TIME = 4.0
ROLL_ENTRY_TIME     = 3.0   # roll maju untuk masukkan barang ke turntable
ROLL_TURN_DELAY     = 2.0   # delay roll setelah turntable mulai putar
ROLL_SORT_TIME      = 6.0   # roll aktif saat turntable menyesuaikan

BLINK_ON            = 0.2   # detik nyala per kedip
BLINK_OFF           = 0.2   # detik mati per kedip
BLINK_COUNT_CAP     = 3     # jumlah kedip warning capacitive
BLINK_COUNT_VISION  = 3     # jumlah kedip warning vision

# --- SERIAL ---
ESP32_BAUD = 115200
ESP32_PORT = None
# =================================================================

client             = None
_client_lock       = threading.Lock()
_coil_state        = {}
_coil_lock         = threading.Lock()
_keepalive_running = False


# ====================== MODBUS LOW-LEVEL =========================

def _read_input(addr):
    for fn in [
        lambda: client.read_discrete_inputs(addr, count=1, slave=MODBUS_SLAVE_ID),
        lambda: client.read_discrete_inputs(addr, count=1, unit=MODBUS_SLAVE_ID),
        lambda: client.read_discrete_inputs(addr, 1),
        lambda: client.read_discrete_inputs(addr),
    ]:
        try:
            r = fn()
            if r and not r.isError():
                return bool(r.bits[0])
        except Exception:
            continue
    return None


def _write_coil(addr, value):
    for fn in [
        lambda: client.write_coil(addr, value, slave=MODBUS_SLAVE_ID),
        lambda: client.write_coil(addr, value, unit=MODBUS_SLAVE_ID),
        lambda: client.write_coil(addr, value),
    ]:
        try:
            r = fn()
            if r and not r.isError():
                return True
        except Exception:
            continue
    return False


def safe_read_input(addr):
    with _client_lock:
        return _read_input(addr)


def coil_on(addr):
    with _coil_lock:
        _coil_state[addr] = True
    with _client_lock:
        _write_coil(addr, True)


def coil_off(addr):
    with _coil_lock:
        _coil_state.pop(addr, None)
    with _client_lock:
        _write_coil(addr, False)


# ==================== KEEPALIVE THREAD ===========================

def _keepalive_worker():
    while _keepalive_running:
        time.sleep(COIL_REFRESH_RATE)
        with _coil_lock:
            snapshot = dict(_coil_state)
        for addr, value in snapshot.items():
            with _client_lock:
                _write_coil(addr, value)


def start_keepalive():
    global _keepalive_running
    _keepalive_running = True
    t = threading.Thread(target=_keepalive_worker, daemon=True, name="CoilKeepalive")
    t.start()
    print(f"[KEEPALIVE] Aktif, refresh tiap {COIL_REFRESH_RATE}s")


def stop_keepalive():
    global _keepalive_running
    _keepalive_running = False


# ====================== CONVEYOR CONTROL =========================

def start_all_conveyors():
    print("[CONV] Semua conveyor + curved belt → ON")
    for c in ALWAYS_ON_COILS:
        coil_on(c)


def stop_all_conveyors():
    print("[CONV] Semua → OFF")
    for c in ALWAYS_ON_COILS:
        coil_off(c)
    coil_off(COIL_TT_TURN)
    coil_off(COIL_TT_ROLL_POS)
    coil_off(COIL_TT_ROLL_NEG)
    coil_off(COIL_WARN_CAP)
    coil_off(COIL_WARN_VISION)


# ====================== WARNING LIGHT ============================

def blink_warning(coil_addr, count=3):
    """Blink warning light sebanyak `count` kali di thread terpisah (non-blocking)"""
    def _blink():
        for _ in range(count):
            with _client_lock:
                _write_coil(coil_addr, True)
            time.sleep(BLINK_ON)
            with _client_lock:
                _write_coil(coil_addr, False)
            time.sleep(BLINK_OFF)
    t = threading.Thread(target=_blink, daemon=True)
    t.start()




def wait_limit(input_addr, timeout, label):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if safe_read_input(input_addr):
            print(f"[TT] {label} tercapai ({time.time()-t0:.2f}s)")
            return True
        time.sleep(0.05)
    print(f"[WARN] {label} timeout {timeout}s, lanjut...")
    return False


def sort_item(color):
    """
    Alur sorting:
      1. Conv 1 & Conv 2 berhenti (Scale tetap ON)
      2. Roll maju 3 detik → barang masuk turntable
      3. Roll mati
      4. Turntable mulai putar ke posisi sesuai warna
         → delay 0.5 detik
         → roll aktif (maju/mundur sesuai arah) selama 6 detik
      5. Roll mati → Turntable mati
      6. Turntable balik ke posisi 0
      7. Conv 1, Conv 2 nyala kembali
    """
    print(f"[SORT] Mulai sorting: {color}")

    # STEP 1: Conv 1 & 2 berhenti, Scale tetap ON
    print("[SORT] Step 1 - Conv 1 & 2 berhenti | Scale tetap ON")
    coil_off(COIL_CONVEYOR_1)
    coil_off(COIL_CONVEYOR_2)
    coil_on(COIL_SCALE_POS)

    # STEP 2: Roll maju 3 detik → barang masuk turntable
    print(f"[SORT] Step 2 - Roll maju {ROLL_ENTRY_TIME}s → barang masuk turntable")
    coil_off(COIL_TT_ROLL_NEG)
    coil_on(COIL_TT_ROLL_POS)
    time.sleep(ROLL_ENTRY_TIME)

    # STEP 3: Roll mati
    print("[SORT] Step 3 - Roll mati")
    coil_off(COIL_TT_ROLL_POS)
    coil_off(COIL_TT_ROLL_NEG)

    # STEP 4: Turntable putar dulu → 0.5s delay → roll menyusul
    if color == "GREEN":
        # Tidak perlu putar, langsung roll maju saja dengan delay
        print(f"[SORT] Step 4 - GREEN: lurus, delay {ROLL_TURN_DELAY}s → roll maju {ROLL_SORT_TIME}s")
        time.sleep(ROLL_TURN_DELAY)
        coil_on(COIL_TT_ROLL_POS)
        time.sleep(ROLL_SORT_TIME)
        coil_off(COIL_TT_ROLL_POS)

    elif color == "GRAY":
        print(f"[SORT] Step 4 - GRAY: turntable putar kanan → {ROLL_TURN_DELAY}s → roll maju {ROLL_SORT_TIME}s")
        coil_on(COIL_TT_TURN)                  # turntable mulai putar
        time.sleep(ROLL_TURN_DELAY)            # delay 0.5s
        coil_on(COIL_TT_ROLL_POS)             # roll menyusul
        time.sleep(ROLL_SORT_TIME)             # keduanya jalan 6 detik
        coil_off(COIL_TT_ROLL_POS)
        coil_off(COIL_TT_TURN)

    elif color == "BLUE":
        print(f"[SORT] Step 4 - BLUE: turntable putar kiri → {ROLL_TURN_DELAY}s → roll mundur {ROLL_SORT_TIME}s")
        coil_on(COIL_TT_TURN)                  # turntable mulai putar
        time.sleep(ROLL_TURN_DELAY)            # delay 0.5s
        coil_on(COIL_TT_ROLL_NEG)             # roll mundur menyusul
        time.sleep(ROLL_SORT_TIME)             # keduanya jalan 6 detik
        coil_off(COIL_TT_ROLL_NEG)
        coil_off(COIL_TT_TURN)

    else:
        print(f"[WARN] Warna tidak dikenal: {color}, default lurus")
        time.sleep(ROLL_TURN_DELAY)
        coil_on(COIL_TT_ROLL_POS)
        time.sleep(ROLL_SORT_TIME)
        coil_off(COIL_TT_ROLL_POS)

    # STEP 5: Pastikan semua roll & turn benar-benar mati
    print("[SORT] Step 5 - Roll & Turn mati semua")
    coil_off(COIL_TT_ROLL_POS)
    coil_off(COIL_TT_ROLL_NEG)
    coil_off(COIL_TT_TURN)
    time.sleep(0.3)

    # STEP 6: Turntable balik ke posisi 0
    if color != "GREEN":
        print("[SORT] Step 6 - Turntable balik ke posisi 0")
        coil_on(COIL_TT_TURN)
        wait_limit(INPUT_LIMIT_0, TURNTABLE_TURN_TIME, "Limit 0 home")
        coil_off(COIL_TT_TURN)
        time.sleep(0.3)
    print("[SORT] Turntable posisi 0 siap")

    # STEP 7: Conv 1, Conv 2, Scale nyala kembali
    print("[SORT] Step 7 - Conv 1, Conv 2, Scale nyala kembali")
    coil_on(COIL_CONVEYOR_1)
    coil_on(COIL_CONVEYOR_2)
    coil_on(COIL_SCALE_POS)
    print(f"[SORT] Selesai {color} - siap barang berikutnya")


# ======================== SERIAL / LCD ===========================

def find_esp32_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = p.description or ""
        if any(k in desc for k in ["CP210", "CH340", "CH9102", "USB Serial", "USB-SERIAL", "UART"]):
            return p.device
    for p in ports:
        try:
            t = serial.Serial(p.device, ESP32_BAUD, timeout=0.5)
            t.close()
            return p.device
        except:
            continue
    return ESP32_PORT


def connect_serial():
    port = find_esp32_port()
    if not port:
        print("[WARN] ESP32 tidak ditemukan. Jalan tanpa LCD.")
        return None
    try:
        ser = serial.Serial()
        ser.port = port; ser.baudrate = ESP32_BAUD
        ser.timeout = 1; ser.dtr = False; ser.rts = False
        ser.open()
        time.sleep(1)
        ser.reset_input_buffer(); ser.reset_output_buffer()
        print(f"[OK] Serial ESP32 → {port}")
        return ser
    except Exception as e:
        print(f"[WARN] Serial gagal: {e}")
        return None


def send_lcd(ser, count, color_name, direction):
    if ser and ser.is_open:
        msg = f"COUNT:{count},COLOR:{color_name},DIR:{direction}\n"
        ser.write(msg.encode())
        print(f"[LCD] {msg.strip()}")


# ====================== DIAGNOSTIK ==============================

def run_diagnostics():
    print("\n── Startup Diagnostics ──")
    labels = {
        INPUT_CAPACITIVE:   "Capacitive  ",
        INPUT_LIMIT_0:      "TT Limit 0° ",
        INPUT_LIMIT_90:     "TT Limit 90°",
        INPUT_BACK_LIMIT:   "TT BackLimit",
        INPUT_FRONT_LIMIT:  "TT FrontLim ",
        INPUT_VISION_GREEN: "Vision GREEN ",
        INPUT_VISION_BLUE:  "Vision BLUE  ",
        INPUT_VISION_GRAY:  "Vision METAL ",
    }
    for addr, label in labels.items():
        val = _read_input(addr)
        print(f"  Input {addr} ({label}) = {val}")

    ok = _write_coil(COIL_CURVED_BELT, True)
    print(f"  Coil 11 (Curved Belt)  = {'OK' if ok else 'GAGAL'}")
    print("─────────────────────────\n")


# ============================ MAIN ===============================

def main():
    global client

    print("=" * 55)
    print("  CONVEYOR SORTING SYSTEM v6")
    print("  3 Vision Sensor (Input 5/6/7)")
    print(f"  GREEN=Lurus↑ | GRAY=Kanan→ | BLUE=Kiri←")
    print(f"  Entry: {ROLL_ENTRY_TIME}s | Turn delay: {ROLL_TURN_DELAY}s | Sort: {ROLL_SORT_TIME}s")
    print("=" * 55)

    client = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT)
    if not client.connect():
        print("[FATAL] Gagal koneksi Modbus ke", MODBUS_HOST)
        return
    print(f"[OK] Modbus → {MODBUS_HOST}:{MODBUS_PORT}")

    run_diagnostics()
    start_all_conveyors()
    start_keepalive()

    serial_conn = connect_serial()
    total_count = 0

    state_green = False
    state_blue  = False
    state_gray  = False
    state_cap   = False
    busy        = False

    print("[START] Menunggu objek di vision sensor...\n")

    try:
        while True:
            cap = safe_read_input(INPUT_CAPACITIVE)
            if cap and not state_cap:
                total_count += 1
                print(f"[COUNT] Barang ke-{total_count}")
                send_lcd(serial_conn, total_count, "-", "-")  # update count ke ESP32
                blink_warning(COIL_WARN_CAP, BLINK_COUNT_CAP)  # Warning Light 1 blink
            if cap is not None:
                state_cap = cap

            vis_green = safe_read_input(INPUT_VISION_GREEN)
            vis_blue  = safe_read_input(INPUT_VISION_BLUE)
            vis_gray  = safe_read_input(INPUT_VISION_GRAY)

            detected_color = None
            if vis_green and not state_green:
                detected_color = "GREEN"
            elif vis_blue and not state_blue:
                detected_color = "BLUE"
            elif vis_gray and not state_gray:
                detected_color = "GRAY"

            if detected_color and not busy:
                busy = True
                dir_label = {"GREEN": "LURUS", "BLUE": "KIRI", "GRAY": "KANAN"}.get(detected_color, "?")
                lcd_color  = {"GREEN": "GREEN", "BLUE": "BLUE", "GRAY": "METAL"}.get(detected_color, detected_color)
                print(f"\n{'='*45}")
                print(f"[VISION] {detected_color} terdeteksi! -> {dir_label}")
                blink_warning(COIL_WARN_VISION, BLINK_COUNT_VISION)  # Warning Light 2 blink
                send_lcd(serial_conn, total_count, lcd_color, dir_label)  # update warna & arah
                sort_item(detected_color)
                print(f"{'='*45}\n")
                busy = False

            if vis_green is not None: state_green = vis_green
            if vis_blue  is not None: state_blue  = vis_blue
            if vis_gray  is not None: state_gray  = vis_gray

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n[STOP] Total barang: {total_count}")
    finally:
        stop_keepalive()
        stop_all_conveyors()
        with _client_lock:
            client.close()
        if serial_conn:
            serial_conn.close()
        print("[DONE] Sistem dimatikan.")


if __name__ == "__main__":
    main()