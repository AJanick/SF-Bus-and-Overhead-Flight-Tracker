# ============================================================
# Combined code.py
# - Starts in Program 2 (Flight) at boot
# - UP: Bus mode (Program 1)
# - DOWN: Flight mode (Program 2)
#
# Bus:
# - ONLY Route 1X inbound, one line, NO SCROLL
#
# Flight:
#   * Speed (mph number only) shown on TOP ROW in light green, right-aligned
#   * Speed hidden while TOP ROW scrolls
#   * Altitude (number only, no "ft") shown on BOTTOM ROW in light green, right-aligned
#   * Altitude hidden while BOTTOM ROW scrolls
# ============================================================

import time
import os
import gc
import json

import board
import displayio
import framebufferio
import rgbmatrix
import terminalio
from adafruit_display_text import label

import busio
from digitalio import DigitalInOut, Direction, Pull
import neopixel

from microcontroller import watchdog as w
from watchdog import WatchDogMode, WatchDogTimeout

from adafruit_esp32spi import adafruit_esp32spi
import adafruit_connection_manager
import adafruit_requests

# -----------------------------
# Watchdog (same as Program 2)
# -----------------------------
w.timeout = 16
w.mode = WatchDogMode.RESET

FONT = terminalio.FONT

# -----------------------------
# Secrets
# -----------------------------
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
password = os.getenv("CIRCUITPY_WIFI_PASSWORD")

API_KEY_511 = os.getenv("API_KEY_511")
AGENCY = "SF"

BOUNDS_BOX = os.getenv("bounds_box") or ""
status_led_value = os.getenv("status_leds", "True").lower()
USE_LEDS = status_led_value in ["true", "1", "yes", "on"]

# -----------------------------
# Buttons (UP/DOWN preferred, A/B fallback)
# -----------------------------
def _get_pin(name_primary, name_fallback):
    if hasattr(board, name_primary):
        return getattr(board, name_primary)
    if hasattr(board, name_fallback):
        return getattr(board, name_fallback)
    return None

PIN_UP = _get_pin("BUTTON_UP", "BUTTON_A")
PIN_DOWN = _get_pin("BUTTON_DOWN", "BUTTON_B")
if PIN_UP is None or PIN_DOWN is None:
    raise RuntimeError("Could not find BUTTON_UP/DOWN or BUTTON_A/B pins on this board.")

btn_up = DigitalInOut(PIN_UP)
btn_up.direction = Direction.INPUT
btn_up.pull = Pull.UP

btn_down = DigitalInOut(PIN_DOWN)
btn_down.direction = Direction.INPUT
btn_down.pull = Pull.UP

def up_pressed():
    return not btn_up.value

def down_pressed():
    return not btn_down.value

# -----------------------------
# Display setup (64x32)
# -----------------------------
displayio.release_displays()
matrix = rgbmatrix.RGBMatrix(
    width=64,
    height=32,
    bit_depth=3,
    rgb_pins=[
        board.MTX_R1, board.MTX_G1, board.MTX_B1,
        board.MTX_R2, board.MTX_G2, board.MTX_B2,
    ],
    addr_pins=[board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC, board.MTX_ADDRD],
    clock_pin=board.MTX_CLK,
    latch_pin=board.MTX_LAT,
    output_enable_pin=board.MTX_OE,
)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=True)

# -----------------------------
# ESP32SPI radio (shared)
# -----------------------------
esp32_cs = DigitalInOut(board.ESP_CS)
esp32_ready = DigitalInOut(board.ESP_BUSY)
esp32_reset = DigitalInOut(board.ESP_RESET)
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
radio = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)

# -----------------------------
# NeoPixel status LED (same idea as Program 2)
# -----------------------------
status_light = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2)

LED_COLORS = {
    'red': (255, 0, 0),
    'green': (0, 255, 0),
    'blue': (0, 0, 255),
    'yellow': (255, 255, 0),
    'purple': (255, 0, 255),
    'white': (255, 255, 255),
    'off': (0, 0, 0)
}

def set_led_color(status_light, color_name):
    if not USE_LEDS or status_light is None:
        color_name = "off"
    if color_name.lower() in LED_COLORS:
        status_light[0] = LED_COLORS[color_name.lower()]
        status_light.show()
        return True
    return False

# -----------------------------
# requests session (shared)
# -----------------------------
pool = None
ssl_context = None
requests = None

def rebuild_requests():
    global pool, ssl_context, requests
    pool = adafruit_connection_manager.get_radio_socketpool(radio)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(radio)
    requests = adafruit_requests.Session(pool, ssl_context)

# ============================================================
# Shared reusable buffers (avoid allocations)
# ============================================================

json_size = 14336
json_bytes = None  # allocated only while flight mode runs
json_bytes_len = 0

# ============================================================
# Program 2 (Flight)
# ============================================================

QUERY_DELAY = 30

ROW_ONE_COLOUR = 0xEE82EE
ROW_TWO_COLOUR = 0x4B0082
ROW_THREE_COLOUR = 0xFFA500
PLANE_COLOUR = 0x4B0082

# Light green for speed/alt numbers only
NUM_LIGHT_GREEN = 0x66FF99

PAUSE_BETWEEN_LABEL_SCROLLING = 3
PLANE_SPEED = 0.04
TEXT_SPEED = 0.04

FLIGHT_SEARCH_HEAD = "https://data-cloud.flightradar24.com/zones/fcgi/feed.js?bounds="
FLIGHT_SEARCH_TAIL = "&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=0&air=1&vehicles=0&estimated=0&maxage=14400&gliders=0&stats=0&ems=1&limit=1"
FLIGHT_SEARCH_URL = FLIGHT_SEARCH_HEAD + BOUNDS_BOX + FLIGHT_SEARCH_TAIL
FLIGHT_LONG_DETAILS_HEAD = "https://data-live.flightradar24.com/clickhandler/?flight="

rheaders = {
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
     "cache-control": "no-store, no-cache, must-revalidate, post-check=0, pre-check=0",
     "accept": "application/json",
     "Connection": "close"
}

def should_exit_flight():
    return up_pressed()

# labels
label1 = label.Label(FONT, color=ROW_ONE_COLOUR, text="")
label1.x = 1; label1.y = 4

# speed label (right-aligned on top row)
label1_speed = label.Label(FONT, color=NUM_LIGHT_GREEN, text="")
label1_speed.y = 4

label2 = label.Label(FONT, color=ROW_TWO_COLOUR, text="")
label2.x = 1; label2.y = 15

label3 = label.Label(FONT, color=ROW_THREE_COLOUR, text="")
label3.x = 1; label3.y = 25

# altitude label (right-aligned on bottom row)
label3_alt = label.Label(FONT, color=NUM_LIGHT_GREEN, text="")
label3_alt.y = 25

label1_short = ''
label1_long = ''
label2_short = ''
label2_long = ''
label3_short = ''
label3_long = ''

# speed/alt texts stored separately so we can hide/show while scrolling
flight_speed_text = ""
flight_alt_text = ""

flight_group = displayio.Group()
flight_group.append(label1)
flight_group.append(label1_speed)
flight_group.append(label2)
flight_group.append(label3)
flight_group.append(label3_alt)

# plane bitmap
planeBmp = displayio.Bitmap(12, 12, 2)
planePalette = displayio.Palette(2)
planePalette[1] = PLANE_COLOUR
planePalette[0] = 0x000000
planeBmp[6,0]=planeBmp[6,1]=planeBmp[5,1]=planeBmp[4,2]=planeBmp[5,2]=planeBmp[6,2]=1
planeBmp[9,3]=planeBmp[5,3]=planeBmp[4,3]=planeBmp[3,3]=1
planeBmp[1,4]=planeBmp[2,4]=planeBmp[3,4]=planeBmp[4,4]=planeBmp[5,4]=planeBmp[6,4]=planeBmp[7,4]=planeBmp[8,4]=planeBmp[9,4]=1
planeBmp[1,5]=planeBmp[2,5]=planeBmp[3,5]=planeBmp[4,5]=planeBmp[5,5]=planeBmp[6,5]=planeBmp[7,5]=planeBmp[8,5]=planeBmp[9,5]=1
planeBmp[9,6]=planeBmp[5,6]=planeBmp[4,6]=planeBmp[3,6]=1
planeBmp[6,9]=planeBmp[6,8]=planeBmp[5,8]=planeBmp[4,7]=planeBmp[5,7]=planeBmp[6,7]=1
planeTg = displayio.TileGrid(planeBmp, pixel_shader=planePalette)
planeG = displayio.Group(x=display.width + 12, y=10)
planeG.append(planeTg)

def plane_animation():
    display.root_group = planeG
    for i in range(display.width + 24, -12, -1):
        planeG.x = i
        w.feed()
        if should_exit_flight():
            return False
        time.sleep(PLANE_SPEED)
    return True

def scroll(line):
    line.x = display.width
    for i in range(display.width + 1, 0 - line.bounding_box[2], -1):
        line.x = i
        w.feed()
        if should_exit_flight():
            return False
        time.sleep(TEXT_SPEED)
    return True

def _right_align_label(lbl, right_pad=1):
    text_w = lbl.bounding_box[2]
    x = display.width - right_pad - text_w
    if x < 0:
        x = 0
    lbl.x = x

def display_flight():
    global label1_short, label1_long, label2_short, label2_long, label3_short, label3_long
    global flight_speed_text, flight_alt_text

    display.root_group = flight_group

    # SHORT display: show speed + altitude (right-aligned)
    label1.text = label1_short
    label2.text = label2_short
    label3.text = label3_short

    label1_speed.text = flight_speed_text or ""
    _right_align_label(label1_speed)

    label3_alt.text = flight_alt_text or ""
    _right_align_label(label3_alt)

    time.sleep(PAUSE_BETWEEN_LABEL_SCROLLING)

    # Top row scroll: HIDE speed during scroll
    label1_speed.text = ""
    _right_align_label(label1_speed)

    label1.x = display.width + 1
    label1.text = label1_long
    if not scroll(label1): return False
    label1.text = label1_short
    label1.x = 1

    # Restore speed after top scroll
    label1_speed.text = flight_speed_text or ""
    _right_align_label(label1_speed)

    time.sleep(PAUSE_BETWEEN_LABEL_SCROLLING)

    # Middle row scroll unchanged
    label2.x = display.width + 1
    label2.text = label2_long
    if not scroll(label2): return False
    label2.text = label2_short
    label2.x = 1
    time.sleep(PAUSE_BETWEEN_LABEL_SCROLLING)

    # Bottom row scroll: HIDE altitude during scroll
    label3_alt.text = ""
    _right_align_label(label3_alt)

    label3.x = display.width + 1
    label3.text = label3_long
    if not scroll(label3): return False
    label3.text = label3_short
    label3.x = 1

    # Restore altitude after bottom scroll
    label3_alt.text = flight_alt_text or ""
    _right_align_label(label3_alt)

    time.sleep(PAUSE_BETWEEN_LABEL_SCROLLING)
    return True

def clear_flight():
    label1.text = ""
    label2.text = ""
    label3.text = ""
    label1_speed.text = ""
    label3_alt.text = ""

def clear_json_bytes():
    global json_bytes
    if json_bytes is None:
        return
    mv = memoryview(json_bytes)
    chunk = b"\x00" * 256
    for i in range(0, json_size, 256):
        mv[i:i+256] = chunk

def get_flight_details(fn):
    global json_bytes, json_size, json_bytes_len
    byte_counter = 0
    chunk_length = 1024

    clear_json_bytes()

    response = None
    try:
        gc.collect()
        response = requests.get(url=FLIGHT_LONG_DETAILS_HEAD + fn, headers=rheaders, timeout=12)
        for chunk in response.iter_content(chunk_size=chunk_length):
            w.feed()
            if (byte_counter + len(chunk) <= json_size):
                json_bytes[byte_counter:byte_counter + len(chunk)] = chunk  # type: ignore
            else:
                print("Exceeded max string size while parsing JSON")
                return False

            trail_start = json_bytes.find((b"\"trail\":"))  # type: ignore
            byte_counter += len(chunk)

            if trail_start != -1:
                trail_end = json_bytes[trail_start:].find((b"}"))  # type: ignore
                if trail_end != -1:
                    trail_end += trail_start
                    closing_bytes = b'}]}'
                    json_bytes[trail_end:trail_end + len(closing_bytes)] = closing_bytes  # type: ignore
                    json_bytes_len = trail_end + 3
                    print("Details lookup saved " + str(trail_end) + " bytes.")
                    return True

    except (RuntimeError, OSError, WatchDogTimeout) as e:
        w.feed()
        print("Error--------------------------------------------------")
        print(e)
        return False
    finally:
        if response is not None:
            response.close()

    print("Failed to find a valid trail entry in JSON")
    return False

def parse_details_json():
    global label1_short, label1_long, label2_short, label2_long, label3_short, label3_long
    global flight_speed_text, flight_alt_text

    try:
        if json_bytes is None:
            return False

        # Parse using memoryview to avoid copying the buffer
        long_json = json.loads(memoryview(json_bytes)[:json_bytes_len])

        number_info = long_json["identification"]["number"]
        flight_number = number_info["default"] if number_info else None
        flight_callsign = long_json["identification"]["callsign"]
        aircraft_code = long_json["aircraft"]["model"]["code"]
        aircraft_model = long_json["aircraft"]["model"]["text"]
        airline_name = long_json["airline"]["name"]
        airport_origin_name = long_json["airport"]["origin"]["name"].replace(" Airport","")
        airport_origin_code = long_json["airport"]["origin"]["code"]["iata"]
        airport_destination_name = long_json["airport"]["destination"]["name"].replace(" Airport","")
        airport_destination_code = long_json["airport"]["destination"]["code"]["iata"]

        altitude = long_json["trail"][0]["alt"]
        speed_knots = long_json["trail"][0]["spd"]
        speed_mph = int(speed_knots * 115078 // 100000)  # mph integer, no float allocs

        # Free the parsed dict immediately after extracting fields
        long_json = None

        if flight_number:
            print("Flight is called " + flight_number)
        elif flight_callsign:
            print("No flight number, callsign is " + flight_callsign)
        else:
            print("No number or callsign for this flight.")

        label1_short = (flight_number or flight_callsign or "")
        label1_long  = airline_name or ""

        flight_speed_text = str(speed_mph)
        label2_short = (airport_origin_code + "-" + airport_destination_code) if airport_origin_code and airport_destination_code else ""
        label2_long  = (airport_origin_name + "-" + airport_destination_name) if airport_origin_name and airport_destination_name else ""

        label3_short = aircraft_code or ""
        flight_alt_text = str(altitude)
        label3_long  = aircraft_model or ""
        return True

    except (KeyError, ValueError, TypeError, IndexError) as e:
        print("JSON error")
        print(e)
        return False

def checkConnection():
    print("Connecting to AP...")
    attempts = 0
    while not radio.is_connected:
        try:
            w.feed()
            radio.connect_AP(ssid, password)
        except (RuntimeError, ConnectionError) as e:
            attempts += 1
            print("could not connect to AP, retrying:", e)
            if attempts % 3 == 0:
                radio.reset()
                time.sleep(2)
            else:
                time.sleep(1)
            w.feed()
    print("Connected")
    set_led_color(status_light, 'green')

def get_flights():
    gc.collect()
    with requests.get(url=FLIGHT_SEARCH_URL, headers=rheaders, timeout=12) as response:
        data = response.json()
        if len(data) == 3:
            for flight_id, flight_info in data.items():
                if not (flight_id == "version" or flight_id == "full_count"):
                    if len(flight_info) > 13:
                        return flight_id
        return False

def run_flight_mode():
    global json_bytes

    set_led_color(status_light, 'yellow')
    checkConnection()
    rebuild_requests()

    if json_bytes is None:
        gc.collect()
        json_bytes = bytearray(json_size)

    display.root_group = flight_group
    clear_flight()

    last_flight = ''
    while True:
        if should_exit_flight() or should_auto_bus():
            return

        if not radio.is_connected:
            set_led_color(status_light, 'yellow')
            checkConnection()
            rebuild_requests()

        w.feed()

        flight_id = None
        try:
            flight_id = get_flights()
        except Exception as e:
            w.feed()
            print("Flight search error:", e)
            rebuild_requests()
            flight_id = False

        w.feed()
        if should_exit_flight():
            return

        if flight_id:
            if flight_id == last_flight:
                print("Same flight found, so keep showing it")
            else:
                print("New flight " + flight_id + " found, clear display")
                clear_flight()
                if get_flight_details(flight_id):
                    w.feed()
                    gc.collect()
                    if parse_details_json():
                        gc.collect()
                        if not plane_animation():
                            return
                        if not display_flight():
                            return
                        last_flight = flight_id
                    else:
                        print("error parsing JSON, skip displaying this flight")
                else:
                    w.feed()
                    print("error loading details, skip displaying this flight")
        else:
            clear_flight()

        time.sleep(5)
        for i in range(0, QUERY_DELAY, +5):
            if should_exit_flight() or should_auto_bus():
                return
            time.sleep(5)
            w.feed()
        gc.collect()

# ============================================================
# Program 1 (Bus) - ONLY Route 1X inbound, NO SCROLL
# ============================================================

STOP_1X_IN = "13876"  # inbound stop for 1X
MAX_STOP_VISITS = 10
HEADERS_511 = {"Accept-Encoding": "identity", "Connection": "close", "Accept": "application/json"}
BUS_REFRESH_SECONDS = 120

LEFT_MARGIN = 0
BUS_MID_LIGHTBLUE = 0x66CCFF

bus_group = displayio.Group()
bus_title = label.Label(FONT, text="JEN BUS ALERT", color=0xFF6600, x=LEFT_MARGIN, y=5)
row1x    = label.Label(FONT, text="1X:--,--,--",   color=BUS_MID_LIGHTBLUE, x=LEFT_MARGIN, y=16)
bus_time_lbl = label.Label(FONT, text="--:--",     color=0xFFFFFF, x=LEFT_MARGIN, y=26)
bus_group.append(bus_title)
bus_group.append(row1x)
bus_group.append(bus_time_lbl)

def _is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)

_year_days_cache = {}

def _days_before_year(y):
    if y in _year_days_cache:
        return _year_days_cache[y]
    d = 0
    for yy in range(1970, y):
        d += 366 if _is_leap(yy) else 365
    _year_days_cache[y] = d
    return d

def _days_before_month(y, m):
    mdays = [31, 28 + (1 if _is_leap(y) else 0), 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    s = 0
    for i in range(m - 1):
        s += mdays[i]
    return s

def iso8601_to_epoch(s):
    y = int(s[0:4]); mo = int(s[5:7]); d = int(s[8:10])
    hh = int(s[11:13]); mm = int(s[14:16]); ss = int(s[17:19])
    tz = s[19:]
    off = 0
    if tz not in ("", "Z"):
        sign = -1 if tz[0] == "-" else 1
        off = sign * (int(tz[1:3]) * 60 + int(tz[4:6]))
    days = _days_before_year(y) + _days_before_month(y, mo) + (d - 1)
    return days * 86400 + hh * 3600 + mm * 60 + ss - off * 60

def norm_route(s):
    if not s:
        return ""
    r = str(s).strip().upper().replace(" ", "")
    if r.endswith("X"):
        base = r[:-1]
        if base.isdigit():
            base = str(int(base))
        return base + "X"
    if r.isdigit():
        return str(int(r))
    return r

def get_pacific_hm_wday(epoch):
    """Return (hour, minute, weekday) in Pacific time. weekday: 0=Mon, 6=Sun."""
    # Find year
    days_total = epoch // 86400
    year = 1970 + days_total // 365
    while _days_before_year(year + 1) <= days_total:
        year += 1
    while _days_before_year(year) > days_total:
        year -= 1
    # Find month (1-indexed)
    day_of_year = days_total - _days_before_year(year)
    mdays = [31, 28 + (1 if _is_leap(year) else 0), 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month = 0
    while month < 11 and day_of_year >= mdays[month]:
        day_of_year -= mdays[month]
        month += 1
    month += 1
    # UTC-7 (PDT) March-October, UTC-8 (PST) November-February
    local_epoch = epoch + (-7 * 3600 if 3 <= month <= 10 else -8 * 3600)
    hh = (local_epoch % 86400) // 3600
    mm = (local_epoch % 3600) // 60
    wday = (local_epoch // 86400 + 3) % 7  # 0=Mon, 4=Fri, 6=Sun
    return int(hh), int(mm), int(wday)

def fmt_pacific_time(epoch):
    hh, mm, _ = get_pacific_hm_wday(epoch)
    ampm = "AM" if hh < 12 else "PM"
    h12 = hh % 12 or 12
    return "{:d}:{:02d}{}".format(h12, mm, ampm)

def fetch_stop_511_raw(stop_code):
    """Fetch 511 API using raw sockets, bypassing adafruit_requests entirely."""
    gc.collect()
    path = (
        "/transit/StopMonitoring?api_key=" + API_KEY_511
        + "&agency=" + AGENCY
        + "&stopCode=" + stop_code
        + "&format=json&MaximumStopVisits=" + str(MAX_STOP_VISITS)
    )
    host = "api.511.org"

    from adafruit_esp32spi.adafruit_esp32spi_socketpool import SocketPool as _SP
    _pool = _SP(radio)
    sock = _pool.socket(_pool.AF_INET, _pool.SOCK_STREAM)
    sock.settimeout(5)

    w.feed()
    addr = radio.get_host_by_name(host)
    sock.connect((addr, 80))
    w.feed()

    request = (
        "GET " + path + " HTTP/1.0\r\n"
        "Host: " + host + "\r\n"
        "Connection: close\r\n"
        "Accept: application/json\r\n"
        "Accept-Encoding: identity\r\n"
        "\r\n"
    )
    sock.send(request.encode())
    w.feed()

    # Read response in chunks
    chunks = []
    while True:
        w.feed()
        try:
            buf = bytearray(1024)
            n = sock.recv_into(buf)
            if n == 0:
                break
            chunks.append(bytes(buf[:n]))
        except OSError:
            break

    sock.close()

    # Join and split headers from body
    raw = b"".join(chunks)
    chunks = None
    header_end = raw.find(b"\r\n\r\n")
    if header_end == -1:
        raise ValueError("No HTTP header end found")
    body = raw[header_end + 4:]
    raw = None
    # Strip BOM if present
    if body[:3] == b"\xef\xbb\xbf":
        body = body[3:]
    return json.loads(body)

def extract_etas_seconds(data, route, n=3):
    sd = data["ServiceDelivery"]
    resp_epoch = iso8601_to_epoch(sd["ResponseTimestamp"])
    visits = sd["StopMonitoringDelivery"]["MonitoredStopVisit"]
    if isinstance(visits, dict):
        visits = [visits]
    secs = []
    for v in visits:
        mvj = v["MonitoredVehicleJourney"]
        if norm_route(mvj.get("LineRef", "")) != route:
            continue
        call = mvj.get("MonitoredCall", {})
        t = call.get("ExpectedArrivalTime") or call.get("AimedArrivalTime")
        if not t:
            continue
        eta = iso8601_to_epoch(t) - resp_epoch
        if 0 <= eta <= 180 * 60:
            secs.append(int(eta))
    secs.sort()
    out = secs[:n]
    while len(out) < n:
        out.append(None)
    return out

def tick_etas(dt, arrays):
    if dt <= 0:
        return
    for arr in arrays:
        for i in range(3):
            v = arr[i]
            if v is None:
                continue
            if v > 0:
                nv = v - dt
                arr[i] = nv if nv > 0 else 0

def fmt3_from_etas(arr):
    out = []
    for i in range(3):
        v = arr[i]
        out.append(str(v // 60) if v is not None else "--")
    return ",".join(out)

def run_bus_mode(auto=False):
    global json_bytes

    print("BUS: enter auto=" + str(auto))
    # Free big flight buffer while bus runs
    json_bytes = None
    gc.collect()
    w.feed()
    # Reset ESP32 to clear all held socket slots from flight mode HTTPS connections
    radio.reset()
    w.feed()
    checkConnection()
    w.feed()
    print("BUS: gc done")

    row1x.text = "1X:--,--,--"
    bus_time_lbl.text = "--:--"
    bus_title.x = display.width  # start off-screen right, will scroll in
    display.root_group = bus_group
    print("BUS: display set")

    etas_1xi = [None, None, None]

    # Time base: synced from 511 API ResponseTimestamp on each successful fetch
    time_base = [None, time.monotonic()]  # [utc_epoch, monotonic_at_sync]

    def current_time_str():
        if time_base[0] is None:
            return "--:--"
        elapsed = int(time.monotonic() - time_base[1])
        return fmt_pacific_time(time_base[0] + elapsed)

    # Title scroll state
    title_x = display.width
    last_scroll_t = time.monotonic()

    def advance_title():
        nonlocal title_x, last_scroll_t
        now = time.monotonic()
        if now - last_scroll_t >= TEXT_SPEED:
            last_scroll_t = now
            title_x -= 1
            if title_x < -bus_title.bounding_box[2]:
                title_x = display.width
            bus_title.x = title_x

    last_fetch = -999999
    last_tick = time.monotonic()
    last_eta_snap = None
    last_time_str = None

    def update_labels():
        nonlocal last_eta_snap, last_time_str, etas_1xi
        snap = tuple((v // 60) if v is not None else None for v in etas_1xi)
        if snap != last_eta_snap:
            last_eta_snap = snap
            row1x.text = "1X:" + fmt3_from_etas(etas_1xi)
        t = current_time_str()
        if t != last_time_str:
            last_time_str = t
            bus_time_lbl.text = t

    def tick_and_update():
        nonlocal last_tick, etas_1xi
        now = time.monotonic()
        dt = int(now - last_tick)
        if dt:
            last_tick += dt
            tick_etas(dt, (etas_1xi,))
            update_labels()

    auto_entered = auto

    while True:
        w.feed()
        if down_pressed():
            break
        if auto_entered and not should_auto_bus():
            print("BUS: auto-bus window ended, returning to flight")
            break

        tick_and_update()
        advance_title()

        if time.monotonic() - last_fetch >= BUS_REFRESH_SECONDS:
            last_fetch = time.monotonic()
            gc.collect()

            if not radio.is_connected:
                set_led_color(status_light, 'yellow')
                checkConnection()
                w.feed()

            w.feed()
            try:
                d_in = fetch_stop_511_raw(STOP_1X_IN)
                try:
                    resp_ts = d_in["ServiceDelivery"]["ResponseTimestamp"]
                    time_base[0] = iso8601_to_epoch(resp_ts)
                    time_base[1] = time.monotonic()
                except Exception:
                    pass
                etas_1xi = extract_etas_seconds(d_in, "1X")
            except (RuntimeError, OSError, MemoryError, KeyError, ValueError, TypeError, WatchDogTimeout) as e:
                w.feed()
                print("Bus fetch error:", e)
                try:
                    radio.reset()
                    w.feed()
                    checkConnection()
                except Exception as e2:
                    print("Bus recovery error:", e2)
                w.feed()

            last_eta_snap = None
            last_time_str = None
            update_labels()

        time.sleep(0.05)

# ============================================================
# MAIN: start in Flight mode at boot
# ============================================================

# Global time tracking: synced once at startup via 511 API, then tracked with monotonic()
_time_sync = [None, 0.0]  # [utc_epoch, monotonic_at_sync]

def sync_time_from_511():
    """Quick 511 fetch — extract ResponseTimestamp without full JSON parse."""
    try:
        gc.collect()
        path = (
            "/transit/StopMonitoring?api_key=" + API_KEY_511
            + "&agency=" + AGENCY
            + "&stopCode=" + STOP_1X_IN
            + "&format=json&MaximumStopVisits=1"
        )
        host = "api.511.org"
        from adafruit_esp32spi.adafruit_esp32spi_socketpool import SocketPool as _SP
        _pool = _SP(radio)
        sock = _pool.socket(_pool.AF_INET, _pool.SOCK_STREAM)
        sock.settimeout(5)
        w.feed()
        addr = radio.get_host_by_name(host)
        sock.connect((addr, 80))
        w.feed()
        req = (
            "GET " + path + " HTTP/1.0\r\n"
            "Host: " + host + "\r\n"
            "Connection: close\r\n"
            "Accept-Encoding: identity\r\n"
            "\r\n"
        )
        sock.send(req.encode())
        w.feed()
        # Read into fixed buffer to avoid fragmentation
        raw = bytearray(2048)
        raw_len = 0
        while raw_len < 2048:
            w.feed()
            try:
                mv = memoryview(raw)[raw_len:]
                n = sock.recv_into(mv)
                if n == 0:
                    break
                raw_len += n
            except OSError:
                break
        sock.close()
        # Find ResponseTimestamp in raw bytes
        marker = b'"ResponseTimestamp":"'
        idx = raw.find(marker, 0, raw_len)
        if idx == -1:
            print("TIME SYNC: no timestamp found")
            return
        start = idx + len(marker)
        end = raw.index(b'"', start)
        ts = raw[start:end].decode()
        raw = None
        _time_sync[0] = iso8601_to_epoch(ts)
        _time_sync[1] = time.monotonic()
        hh, mm, wday = get_pacific_hm_wday(_time_sync[0])
        print("TIME SYNC: " + str(hh) + ":" + str(mm) + " wday=" + str(wday))
        gc.collect()
    except Exception as e:
        print("TIME SYNC ERROR:", e)

def current_utc_epoch():
    if _time_sync[0] is None:
        return None
    return _time_sync[0] + int(time.monotonic() - _time_sync[1])

def should_auto_bus():
    """Return True if current Pacific time is in the auto-bus window."""
    epoch = current_utc_epoch()
    if epoch is None:
        return False
    hh, mm, wday = get_pacific_hm_wday(epoch)
    is_weekday = wday <= 4  # Mon=0 .. Fri=4
    mins = hh * 60 + mm
    return is_weekday and (7 * 60 + 15) <= mins < (8 * 60 + 15)

checkConnection()
rebuild_requests()

# Pre-allocate the large flight buffer BEFORE time sync to avoid fragmentation
gc.collect()
json_bytes = bytearray(json_size)

sync_time_from_511()

display.root_group = flight_group
set_led_color(status_light, 'purple')

# manual_flight: user pressed DOWN to exit bus mode during auto-bus window;
# stay in flight mode until the window ends or they press UP.
manual_flight = False

while True:
    try:
        w.feed()
        if up_pressed():
            manual_flight = False
            run_bus_mode()
        elif down_pressed():
            manual_flight = True
            run_flight_mode()
        elif should_auto_bus() and not manual_flight:
            run_bus_mode(auto=True)
        else:
            manual_flight = False  # outside auto window; reset override flag
            run_flight_mode()
    except WatchDogTimeout:
        w.feed()
        print("Watchdog timeout at top level")
    except Exception as e:
        w.feed()
        print("Top level error:", e)

