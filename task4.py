import time
import network
import machine
import ujson
from machine import Pin, ADC, I2C, SoftI2C
from umqtt.simple import MQTTClient
import mlx90614
from bmp280 import BMP280

# ---------- Configuration ----------
WIFI_SSID     = " "  
WIFI_PASSWORD = " "
MQTT_SERVER   = "broker.hivemq.com"
MQTT_PORT     = 1883
MQTT_TOPIC    = b"lab4/task4/data"

MQ5_PIN = 34   

MLX_SDA, MLX_SCL = 21, 22   
BMP_SDA, BMP_SCL = 18, 19  
RTC_SDA, RTC_SCL = 25, 26   

WINDOW_SIZE     = 5
WARNING_LEVEL   = 2100     
DANGER_LEVEL    = 2600     
FEVER_THRESHOLD = 32.5     
SEA_LEVEL_HPA   = 1013.25 

SET_RTC_FROM_NTP = False
UTC_OFFSET_HOURS = 7       # Cambodia (UTC+7)

SEND_INTERVAL = 2          # seconds between packets

RISK_CODES = {"SAFE": 0, "WARNING": 1, "DANGER": 2}


# ---------- DS3231 RTC (minimal driver) ----------
class DS3231:
    ADDR = 0x68

    def __init__(self, i2c):
        self.i2c = i2c

    @staticmethod
    def _bcd2dec(b):
        return (b >> 4) * 10 + (b & 0x0F)

    @staticmethod
    def _dec2bcd(d):
        return ((d // 10) << 4) | (d % 10)

    def datetime(self):
        d = self.i2c.readfrom_mem(self.ADDR, 0x00, 7)
        second = self._bcd2dec(d[0] & 0x7F)
        minute = self._bcd2dec(d[1] & 0x7F)
        if d[2] & 0x40:                       # 12-hour mode
            hour = self._bcd2dec(d[2] & 0x1F) % 12
            if d[2] & 0x20:
                hour += 12
        else:
            hour = self._bcd2dec(d[2] & 0x3F)
        day = self._bcd2dec(d[4] & 0x3F)
        month = self._bcd2dec(d[5] & 0x1F)
        year = 2000 + self._bcd2dec(d[6])
        return year, month, day, hour, minute, second

    def set_datetime(self, year, month, day, hour, minute, second, weekday):
        data = bytes([
            self._dec2bcd(second), self._dec2bcd(minute), self._dec2bcd(hour),
            self._dec2bcd(weekday), self._dec2bcd(day), self._dec2bcd(month),
            self._dec2bcd(year - 2000),
        ])
        self.i2c.writeto_mem(self.ADDR, 0x00, data)

    def timestamp(self):
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*self.datetime())


# ---------- Hardware setup ----------
adc = ADC(Pin(MQ5_PIN))
adc.atten(ADC.ATTN_11DB)     # full 0 - 3.3V range
adc.width(ADC.WIDTH_12BIT)   # 0 - 4095

i2c_mlx = I2C(0, scl=Pin(MLX_SCL), sda=Pin(MLX_SDA), freq=100000)
i2c_bmp = I2C(1, scl=Pin(BMP_SCL), sda=Pin(BMP_SDA), freq=100000)
# Internal pull-ups in case the DS3231 board has none (SoftI2C needs them)
i2c_rtc = SoftI2C(scl=Pin(RTC_SCL, Pin.OPEN_DRAIN, Pin.PULL_UP),
                  sda=Pin(RTC_SDA, Pin.OPEN_DRAIN, Pin.PULL_UP), freq=100000)

print("MLX90614 bus:", [hex(a) for a in i2c_mlx.scan()])   # expect 0x5a
print("BMP280 bus:  ", [hex(a) for a in i2c_bmp.scan()])   # expect 0x76 or 0x77
print("DS3231 bus:  ", [hex(a) for a in i2c_rtc.scan()])   # expect 0x68 (0x57 = on-board EEPROM)
if DS3231.ADDR not in i2c_rtc.scan():
    print("WARNING: DS3231 not found on SDA {} / SCL {} - check its wiring".format(RTC_SDA, RTC_SCL))

mlx = mlx90614.MLX90614(i2c_mlx)
bmp = BMP280(i2c_bmp)
rtc = DS3231(i2c_rtc)

# ---------- Moving average buffer ----------
readings = [0] * WINDOW_SIZE
read_index = 0
total = 0
count = 0


def moving_average(raw):
    global read_index, total, count
    total -= readings[read_index]
    readings[read_index] = raw
    total += raw
    read_index = (read_index + 1) % WINDOW_SIZE
    if count < WINDOW_SIZE:
        count += 1
    return total / count


def classify_gas(value):
    if value < WARNING_LEVEL:
        return "SAFE"
    elif value < DANGER_LEVEL:
        return "WARNING"
    return "DANGER"


def fever_check(body_temp):
    return 1 if body_temp >= FEVER_THRESHOLD else 0


def altitude_from_pressure(hpa):
    return 44330.0 * (1.0 - (hpa / SEA_LEVEL_HPA) ** (1 / 5.255))


def sync_rtc_from_ntp():
    import ntptime
    try:
        ntptime.settime()   # sets the ESP32 clock to UTC
        t = time.localtime(time.time() + UTC_OFFSET_HOURS * 3600)
        rtc.set_datetime(t[0], t[1], t[2], t[3], t[4], t[5], t[6] + 1)
        print("DS3231 set from NTP:", rtc.timestamp())
    except Exception as e:
        print("NTP sync failed:", e)


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)   # reset the radio (clears a stuck connection left by the previous run)
    time.sleep(0.5)
    wlan.active(True)
    while True:
        print("Connecting to WiFi '{}'".format(WIFI_SSID), end="")
        try:
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except OSError as e:
            print(" error:", e, end="")
        for _ in range(30):              # wait up to 15 s
            if wlan.isconnected():
                print()
                print("WiFi connected, IP:", wlan.ifconfig()[0])
                return
            time.sleep(0.5)
            print(".", end="")
        print()
        status = wlan.status()
        if status == getattr(network, "STAT_NO_AP_FOUND", -1):
            print("Network not found (ESP32 needs 2.4 GHz). Visible networks:")
            try:
                print([n[0].decode() for n in wlan.scan()])
            except OSError:
                pass
        elif status == getattr(network, "STAT_WRONG_PASSWORD", -1):
            print("Wrong WiFi password.")
        else:
            print("WiFi connection failed (status {}). Retrying...".format(status))
        try:
            wlan.disconnect()
        except OSError:
            pass
        time.sleep(1)


def connect_mqtt():
    client_id = "ESP32-Task4-" + machine.unique_id().hex()
    while True:
        try:
            print("Connecting to MQTT...", end="")
            client = MQTTClient(client_id, MQTT_SERVER, port=MQTT_PORT, keepalive=30)
            client.connect()
            print("connected")
            return client
        except OSError as e:
            print("failed:", e, "retrying in 2s")
            time.sleep(2)


connect_wifi()
if SET_RTC_FROM_NTP:
    sync_rtc_from_ntp()
client = connect_mqtt()

while True:
    # 1-2. Gas: filter + classify
    gas_raw = adc.read()
    gas_avg = moving_average(gas_raw)
    risk_level = classify_gas(gas_avg)

    packet = {
        "gas_raw": gas_raw,
        "gas_avg": round(gas_avg, 2),
        "risk_level": risk_level,
        "risk_code": RISK_CODES[risk_level],
    }

    # 3. Body temperature + fever flag
    try:
        body_temp = mlx.read_object_temp()
        packet["body_temp"] = round(body_temp, 2)
        packet["fever_flag"] = fever_check(body_temp)
    except OSError as e:
        print("MLX90614 read error:", e)

    # 4. Pressure + altitude (read temperature first: BMP280 compensation needs it)
    try:
        _ = bmp.temperature
        pressure_hpa = bmp.pressure / 100
        packet["pressure"] = round(pressure_hpa, 2)
        packet["altitude"] = round(altitude_from_pressure(pressure_hpa), 2)
    except OSError as e:
        print("BMP280 read error:", e)

    # 5. DS3231 timestamp
    try:
        packet["timestamp"] = rtc.timestamp()
    except OSError as e:
        print("DS3231 read error:", e)

    msg = ujson.dumps(packet)
    print("Sent:", msg)

    try:
        client.publish(MQTT_TOPIC, msg)
    except OSError:
        client = connect_mqtt()

    time.sleep(SEND_INTERVAL)
