import time
import network
import machine
import ujson
from machine import Pin, I2C
from umqtt.simple import MQTTClient
import mlx90614

# ---------- Configuration ----------
WIFI_SSID     = " "  
WIFI_PASSWORD = " "
MQTT_SERVER   = "broker.hivemq.com"
MQTT_PORT     = 1883
MQTT_TOPIC    = b"lab4/task3/data"

I2C_SDA = 21
I2C_SCL = 22

FEVER_THRESHOLD = 32.5   

# ---------- Sensor setup ----------
i2c = I2C(0, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=100000)
print("I2C devices:", [hex(a) for a in i2c.scan()])   
mlx = mlx90614.MLX90614(i2c)


def fever_check(body_temp):
    return 1 if body_temp >= FEVER_THRESHOLD else 0


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
    client_id = "ESP32-Task3-" + machine.unique_id().hex()
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
    try:
        body_temp = mlx.read_object_temp()
        ambient = mlx.read_ambient_temp()
    except OSError as e:
        print("MLX90614 read error:", e)
        time.sleep(1)
        continue

    fever_flag = fever_check(body_temp)

    packet = {
        "body_temp": round(body_temp, 2),
        "ambient_temp": round(ambient, 2),
        "fever_flag": fever_flag,
    }
    msg = ujson.dumps(packet)
    print("Body: {:.2f} C\tAmbient: {:.2f} C\tfever_flag: {}{}".format(
        body_temp, ambient, fever_flag, "  << FEVER" if fever_flag else ""))

    try:
        client.publish(MQTT_TOPIC, msg)
    except OSError:
        client = connect_mqtt()

    time.sleep(1)
