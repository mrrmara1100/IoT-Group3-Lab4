import time
import network
import machine
from machine import Pin, ADC
from umqtt.simple import MQTTClient

# ---------- Configuration ----------
WIFI_SSID     = " "   
WIFI_PASSWORD = " "
MQTT_SERVER   = "broker.hivemq.com"  
MQTT_PORT     = 1883
MQTT_TOPIC    = b"lab4/task1/gas_avg"

MQ5_PIN     = 34   
WINDOW_SIZE = 5    

# ---------- ADC setup ----------
adc = ADC(Pin(MQ5_PIN))
adc.atten(ADC.ATTN_11DB)     
adc.width(ADC.WIDTH_12BIT)   

# ---------- Moving average buffer ----------
readings = [0] * WINDOW_SIZE
read_index = 0
total = 0
count = 0                  


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)   
    time.sleep(0.5)
    wlan.active(True)
    while True:
        print("Connecting to WiFi '{}'".format(WIFI_SSID), end="")
        try:
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except OSError as e:
            print(" error:", e, end="")
        for _ in range(30):              
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
    client_id = "ESP32-Task1-" + machine.unique_id().hex()
    while True:
        try:
            print("Connecting to MQTT...", end="")
            client = MQTTClient(client_id, MQTT_SERVER, port=MQTT_PORT)
            client.connect()
            print("connected")
            return client
        except OSError as e:
            print("failed:", e, "retrying in 2s")
            time.sleep(2)


connect_wifi()
client = connect_mqtt()

while True:
    # 1. Read raw value
    raw = adc.read()

    # 2. Store in circular buffer
    total -= readings[read_index]
    readings[read_index] = raw
    total += raw
    read_index = (read_index + 1) % WINDOW_SIZE
    if count < WINDOW_SIZE:
        count += 1

    # 3. Compute moving average
    average = total / count

    # 4. Print raw and averaged value
    print("Raw: {}\tAverage: {:.2f}".format(raw, average))

    # 5. Send averaged value to Node-RED
    try:
        client.publish(MQTT_TOPIC, "{:.2f}".format(average))
    except OSError:
        client = connect_mqtt()

    time.sleep(1)
