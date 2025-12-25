# ============================================================
# Combined code.py
# - Starts in Program 2 (Flight) at boot
# - UP: Bus mode (Program 1)
# - DOWN: Flight mode (Program 2)
#
# Changes:
# - Bus row colors: top blue, middle light blue, bottom orange
# - Flight:
#   * Speed (mph number only) shown on TOP ROW in light green, right-aligned
#   * Speed hidden while TOP ROW scrolls
#   * Altitude (number only, no "ft") shown on BOTTOM ROW in light green, right-aligned
#   * Altitude hidden while BOTTOM ROW scrolls  <-- requested
#   * Rest of the top/bottom row text keeps its original color
# ============================================================

import os
import time
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
from watchdog import WatchDogMode

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

def set_led_color(status_light, color_name):
    if not USE_LEDS or status_light is None:
        color_name = "off"
    colors = {
        'red': (255, 0, 0),
        'green': (0, 255, 0),
        'blue': (0, 0, 255),
        'yellow': (255, 255, 0),
        'purple': (255, 0, 255),
        'white': (255, 255, 255),
        'off': (0, 0, 0)
    }
    if color_name.lower() in colors:
        status_light[0] = colors[color_name.lower()]
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

BUS_JSON_SIZE = 8192  # bump to 12288 if you ever exceed this
_bus_json = bytearray(BUS_JSON_SIZE)

json_size = 14336
json_bytes = None  # allocated only while flight mode runs

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
     "accept": "application/json"
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

    # Bottom row scroll: HIDE altitude during scroll (requested)
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
    global json_bytes, json_size
    byte_counter = 0
    chunk_length = 1024

    clear_json_bytes()

    try:
        response = requests.get(url=FLIGHT_LONG_DETAILS_HEAD + fn, headers=rheaders)
        for chunk in response.iter_content(chunk_size=chunk_length):
            if (byte_counter + len(chunk) <= json_size):
                json_bytes[byte_counter:byte_counter + len(chunk)] = chunk  # type: ignore
            else:
                print("Exceeded max string size while parsing JSON")
                response.close()
                return False

            trail_start = json_bytes.find((b"\"trail\":"))  # type: ignore
            byte_counter += len(chunk)

            if trail_start != -1:
                trail_end = json_bytes[trail_start:].find((b"}"))  # type: ignore
                if trail_end != -1:
                    trail_end += trail_start
                    closing_bytes = b'}]}'
                    json_bytes[trail_end:trail_end + len(closing_bytes)] = closing_bytes  # type: ignore
                    for i in range(trail_end + 3, json_size):
                        json_bytes[i] = 0  # type: ignore
                    print("Details lookup saved " + str(trail_end) + " bytes.")
                    response.close()
                    return True

        response.close()
    except (RuntimeError, OSError) as e:
        print("Error--------------------------------------------------")
        print(e)
        return False

    print("Failed to find a valid trail entry in JSON")
    return False

def parse_details_json():
    global label1_short, label1_long, label2_short, label2_long, label3_short, label3_long
    global flight_speed_text, flight_alt_text

    try:
        if json_bytes is None:
            return False

        long_json = json.loads(json_bytes)

        flight_number = long_json["identification"]["number"]["default"]
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
    while not radio.is_connected:
        try:
            w.feed()
            radio.connect_AP(ssid, password)
        except (RuntimeError, ConnectionError) as e:
            print("could not connect to AP, retrying: ", e)
            continue
    print("Connected")
    set_led_color(status_light, 'green')

def get_flights():
    with requests.get(url=FLIGHT_SEARCH_URL, headers=rheaders) as response:
        data = response.json()
        if len(data) == 3:
            for flight_id, flight_info in data.items():
                if not (flight_id == "version" or flight_id == "full_count"):
                    if len(flight_info) > 13:
                        return flight_id
        else:
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
        if should_exit_flight():
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
                    else:
                        print("error parsing JSON, skip displaying this flight")
                else:
                    w.feed()
                    print("error loading details, skip displaying this flight")
                last_flight = flight_id
        else:
            clear_flight()

        time.sleep(5)
        for i in range(0, QUERY_DELAY, +5):
            if should_exit_flight():
                return
            time.sleep(5)
            w.feed()
        gc.collect()

# ============================================================
# Program 1 (Bus)
# ============================================================

STOP_1_OUT = "13845"
STOP_1_IN = "13846"
STOP_33_IN = "13643"
STOP_33_OUT = "13644"

MAX_STOP_VISITS = 10
HEADERS_511 = {"Accept-Encoding": "identity", "Connection": "close", "Accept": "application/json"}
BUS_REFRESH_SECONDS = 240  # 4 calls/refresh => 60/hour max

SCROLL_SPEED = 0.03
PAUSE_BETWEEN_SCROLLS = 0.8
LEFT_MARGIN = 0

BUS_TOP_BLUE = 0x0000FF
BUS_MID_LIGHTBLUE = 0x66CCFF
BUS_BOTTOM_ORANGE = 0xFFA500

def _is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)

def _days_before_year(y):
    d = 0
    for yy in range(1970, y):
        d += 366 if _is_leap(yy) else 365
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

def fetch_stop_511(stop_code):
    url = (
        "http://api.511.org/transit/StopMonitoring"
        + "?api_key=" + API_KEY_511
        + "&agency=" + AGENCY
        + "&stopCode=" + stop_code
        + "&format=json"
        + "&MaximumStopVisits=" + str(MAX_STOP_VISITS)
    )

    gc.collect()

    r = None
    try:
        r = requests.get(url, headers=HEADERS_511, timeout=20)

        idx = 0
        for chunk in r.iter_content(chunk_size=512):
            if not chunk:
                continue
            ln = len(chunk)
            if idx + ln > BUS_JSON_SIZE:
                raise MemoryError("511 response exceeded BUS_JSON_SIZE")
            _bus_json[idx:idx + ln] = chunk
            idx += ln

        start = 3 if idx >= 3 and _bus_json[0:3] == b"\xef\xbb\xbf" else 0
        return json.loads(_bus_json[start:idx].decode("utf-8"))

    finally:
        if r:
            r.close()

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

def run_bus_mode():
    global json_bytes

    # Free big flight buffer while bus runs
    json_bytes = None
    gc.collect()

    bus_group = displayio.Group()
    row1  = label.Label(FONT, text="1  I:--,--,--  O:--,--,--",  color=BUS_TOP_BLUE, x=LEFT_MARGIN, y=8)
    row1x = label.Label(FONT, text="1X I:--,--,--  O:--,--,--", color=BUS_MID_LIGHTBLUE, x=LEFT_MARGIN, y=18)
    row33 = label.Label(FONT, text="33 I:--,--,--  O:--,--,--", color=BUS_BOTTOM_ORANGE, x=LEFT_MARGIN, y=28)
    bus_group.append(row1); bus_group.append(row1x); bus_group.append(row33)
    display.root_group = bus_group

    etas_1i  = [None, None, None]
    etas_1o  = [None, None, None]
    etas_1xi = [None, None, None]
    etas_1xo = [None, None, None]
    etas_33i = [None, None, None]
    etas_33o = [None, None, None]

    last_fetch = -999999
    last_tick = time.monotonic()
    last_snapshot = None

    def update_labels_if_needed():
        nonlocal last_snapshot
        snap = (
            tuple((v // 60) if v is not None else None for v in etas_1i),
            tuple((v // 60) if v is not None else None for v in etas_1o),
            tuple((v // 60) if v is not None else None for v in etas_1xi),
            tuple((v // 60) if v is not None else None for v in etas_1xo),
            tuple((v // 60) if v is not None else None for v in etas_33i),
            tuple((v // 60) if v is not None else None for v in etas_33o),
        )
        if snap == last_snapshot:
            return
        last_snapshot = snap
        row1.text  = "1  I:"  + fmt3_from_etas(etas_1i)  + "  O:" + fmt3_from_etas(etas_1o)
        row1x.text = "1X I:" + fmt3_from_etas(etas_1xi) + "  O:" + fmt3_from_etas(etas_1xo)
        row33.text = "33 I:" + fmt3_from_etas(etas_33i) + "  O:" + fmt3_from_etas(etas_33o)

    def tick_and_update():
        nonlocal last_tick
        now = time.monotonic()
        dt = int(now - last_tick)
        if dt:
            last_tick += dt
            tick_etas(dt, (etas_1i, etas_1o, etas_1xi, etas_1xo, etas_33i, etas_33o))
            update_labels_if_needed()

    def scroll_label(lbl):
        if down_pressed():
            return False
        w.feed()
        tick_and_update()
        wdisp = display.width
        text_w = lbl.bounding_box[2]
        if text_w <= wdisp:
            lbl.x = LEFT_MARGIN
            t0 = time.monotonic()
            while time.monotonic() - t0 < PAUSE_BETWEEN_SCROLLS:
                w.feed()
                if down_pressed():
                    return False
                tick_and_update()
                time.sleep(0.02)
            return True

        lbl.x = wdisp
        end_x = -text_w
        for x in range(wdisp, end_x - 1, -1):
            lbl.x = x
            w.feed()
            if down_pressed():
                return False
            tick_and_update()
            time.sleep(SCROLL_SPEED)

        lbl.x = LEFT_MARGIN
        t0 = time.monotonic()
        while time.monotonic() - t0 < PAUSE_BETWEEN_SCROLLS:
            w.feed()
            if down_pressed():
                return False
            tick_and_update()
            time.sleep(0.02)
        return True

    while True:
        w.feed()
        if down_pressed():
            break

        if time.monotonic() - last_fetch >= BUS_REFRESH_SECONDS:
            last_fetch = time.monotonic()
            gc.collect()

            if not radio.is_connected:
                set_led_color(status_light, 'yellow')
                checkConnection()
                rebuild_requests()

            d1i = fetch_stop_511(STOP_1_IN)
            d1o = fetch_stop_511(STOP_1_OUT)
            d33i = fetch_stop_511(STOP_33_IN)
            d33o = fetch_stop_511(STOP_33_OUT)

            etas_1i  = extract_etas_seconds(d1i,  "1")
            etas_1o  = extract_etas_seconds(d1o,  "1")
            etas_1xi = extract_etas_seconds(d1i,  "1X")
            etas_1xo = extract_etas_seconds(d1o,  "1X")
            etas_33i = extract_etas_seconds(d33i, "33")
            etas_33o = extract_etas_seconds(d33o, "33")

            last_snapshot = None
            update_labels_if_needed()

        if not scroll_label(row1):  break
        if not scroll_label(row1x): break
        if not scroll_label(row33): break

# ============================================================
# MAIN: start in Flight mode at boot
# ============================================================

checkConnection()
rebuild_requests()

display.root_group = flight_group
set_led_color(status_light, 'purple')

while True:
    w.feed()
    if up_pressed():
        run_bus_mode()
    else:
        run_flight_mode()
