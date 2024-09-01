import asyncio
import ujson
import usocket as socket
import ubinascii
import machine
import utime
from wifimgr import WifiManager
from hcsr04 import HCSR04
import urequests

wm = WifiManager()
wm.connect()

sensor = HCSR04(trigger_pin=5, echo_pin=18, echo_timeout_us=10000)

green_led_pin = 16
red_led_pin = 17
green_led = machine.Pin(green_led_pin, machine.Pin.OUT)
red_led = machine.Pin(red_led_pin, machine.Pin.OUT)
green_led.value(0)
red_led.value(0)

start_url = 'http://192.168.8.100/v1/device/start_read_data/CAMERA_1'
stop_url = 'http://192.168.8.100/v1/device/stop_read_data/CAMERA_1'
ws_url = 'ws://192.168.8.100/v1/notification/ws/DOOR_LOCK_2'

distance_threshold = 50

start_request_made = False

last_check_time_min_distance = utime.ticks_ms()
last_check_time_max_distance = utime.ticks_ms()

ws = None

def connect_websocket():
    global ws
    ws_url_parts = ws_url[5:].split('/', 1)
    host, path = ws_url_parts[0], '/' + ws_url_parts[1]

    addr = socket.getaddrinfo(host, 80)[0][-1]

    s = socket.socket()
    s.connect(addr)

    key = ubinascii.b2a_base64(utime.ticks_ms().to_bytes(16, 'big')).decode().strip()
    headers = {
        'Host': host,
        'Upgrade': 'websocket',
        'Connection': 'Upgrade',
        'Sec-WebSocket-Key': key,
        'Sec-WebSocket-Version': '13'
    }

    req = "GET {} HTTP/1.1\r\n".format(path)
    req += "\r\n".join(["{}: {}".format(k, v) for k, v in headers.items()])
    req += "\r\n\r\n"

    s.send(req.encode())

    response = s.recv(1024)
    if b"101 Switching Protocols" not in response:
        raise Exception("WebSocket handshake failed")

    ws = s
    print("WebSocket connected.")
    return ws

async def websocket_handler():
    global ws
    ping_interval = 4 
    last_ping_time = utime.ticks_ms()

    while True:
        try:
            if ws is None:
                ws = connect_websocket()
            msg = ws.recv(1024)
            if msg:
                if msg[0] & 0x0F == 0x01:  # Text frame
                    length = msg[1] & 127
                    if length == 126:
                        length = int.from_bytes(msg[2:4], 'big')
                    elif length == 127:
                        length = int.from_bytes(msg[2:10], 'big')

                    data = msg[2:2 + length]
                    print("WebSocket message received:", data)
                    try:
                        message_data = ujson.loads(data.decode())
                        if message_data.get("is_authenticated"):
                            print("Door opened:")
                            red_led.value(1)
                        else:
                            print("Door closed")
                            red_led.value(0)
                    except Exception as e:
                        print("Error processing WebSocket message:", e)

            current_time = utime.ticks_ms()
            if utime.ticks_diff(current_time, last_ping_time) >= ping_interval * 1000:
                try:
                    ws.send(b'\x89\x00')  # WebSocket ping frame
                    last_ping_time = current_time
                    print("Ping sent")
                except Exception as e:
                    print("Failed to send ping:", e)
                    ws = None

        except Exception as e:
            print("WebSocket communication error:", e)
            ws = None 
        await asyncio.sleep(0.5)


async def main():
    global start_request_made, last_check_time_min_distance, last_check_time_max_distance
    asyncio.create_task(websocket_handler())

    while True:
        if wm.is_connected():
            distance = sensor.distance_cm()
            print('Distance from sensor:', distance, 'cm')
            current_time = utime.ticks_ms()

            if distance < distance_threshold and not start_request_made:
                if utime.ticks_diff(current_time, last_check_time_max_distance) >= 500:
                    try:
                        response = urequests.post(start_url, headers={'accept': 'application/json'})
                        response_data = response.json()
                        print('Start request status:', response.status_code)
                        print('Response:', response.text)
                        response.close()
                        
                        if response_data.get("is_error") == False:
                            start_request_made = True
                            green_led.value(1)
                            print('Start process successful. LED ON.')
                        else:
                            print('Start process failed:', response_data.get("message", "Unknown error"))
                    
                    except Exception as e:
                        print('Start request failed:', e)

            elif distance > distance_threshold and start_request_made:
                if utime.ticks_diff(current_time, last_check_time_min_distance) >= 5000:
                    distance = sensor.distance_cm()
                    if distance > distance_threshold:
                        try:
                            response = urequests.post(stop_url, headers={'accept': 'application/json'})
                            response_data = response.json()
                            print('Stop request status:', response.status_code)
                            print('Response:', response.text)
                            response.close()
                            
                            if response_data.get("is_error") == False:
                                start_request_made = False 
                                green_led.value(0) 
                                print('Stop process successful. LED OFF.')
                            else:
                                print('Stop process failed:', response_data.get("message", "Unknown error"))
                        
                        except Exception as e:
                            print('Stop request failed:', e)
                        last_check_time_min_distance = current_time

            if distance < distance_threshold:
                last_check_time_min_distance = current_time

            if distance > distance_threshold:
                last_check_time_max_distance = current_time

        else:
            print('Disconnected!')
            wm.connect()

        await asyncio.sleep(2)

asyncio.run(main())

