import time
import network
import machine
import ujson
from machine import Pin, ADC
from umqtt.simple import MQTTClient

# ---------- Configuration ----------
WIFI_SSID     = " "   
WIFI_PASSWORD = " "
MQTT_SERVER   = "broker.hivemq.com"
MQTT_PORT     = 1883
MQTT_TOPIC    = b"lab4/task2/data"

MQ5_PIN     = 34   
WINDOW_SIZE = 5    

# Classification thresholds (applied to the averaged value)
WARNING_LEVEL = 2100   # 2100 - 2599 -> WARNING
DANGER_LEVEL  = 2600   # >= 2600     -> DANGER

# ---------- ADC setup ----------
adc = ADC(Pin(MQ5_PIN))
adc.atten(ADC.ATTN_11DB)     # full 0 - 3.3V range
adc.width(ADC.WIDTH_12BIT)   # 0 - 4095

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
    client_id = "ESP32-Task2-" + machine.unique_id().hex()
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
client = connect_mqtt()

while True:
    raw = adc.read()
    average = moving_average(raw)
    risk_level = classify_gas(average)

    packet = {
        "gas_raw": raw,
        "gas_avg": round(average, 2),
        "risk_level": risk_level,
    }
    msg = ujson.dumps(packet)
    print("Raw: {}\tAverage: {:.2f}\tRisk: {}".format(raw, average, risk_level))

    try:
        client.publish(MQTT_TOPIC, msg)
    except OSError:
        client = connect_mqtt()

    time.sleep(1)
