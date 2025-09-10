import socket
import threading
import json
import discordpresence
import time
import struct
import os

# Debug toggle (set to False to disable logging)
DEBUG = False

HEADER = 64
FORMAT = 'utf-8'
DISCONNECT_MESSAGE = "!DISCONNECT"
SERVER = os.environ.get('DISCORD_RP_SERVER', input("Enter server IP address (default 192.168.0.100): ") or '192.168.0.1')
PORT = int(os.environ.get('DISCORD_RP_PORT', input("Enter server port (default 5050): ") or '5050'))
ADDR = (SERVER, PORT)

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

presence = discordpresence.DiscordIpcClient.for_platform('1413362051098873857')

LAST_RECEIVE_TIME = time.time()
CLEAR_TIMEOUT = 10  # Seconds of inactivity before clearing presence

def listen_for_message(cc):
    if DEBUG:
        print("[DEBUG] Starting listen_for_message thread.")
    retry_delay = 1
    max_delay = 32
    MAX_LENGTH = 10240  # Sanity max for JSON
    global LAST_RECEIVE_TIME
    while True:
        try:
            cc.close()
            cc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if DEBUG:
                print(f"[DEBUG] Attempting to connect to {ADDR}")
            cc.connect(ADDR)
            cc.settimeout(5.0)
            if DEBUG:
                print(f"[DEBUG] Connected to {ADDR}")
            retry_delay = 1
            LAST_RECEIVE_TIME = time.time()  # Reset on connect
            while True:
                try:
                    if DEBUG:
                        print("[DEBUG] Waiting for length bytes...")
                    len_bytes = cc.recv(4)
                    if not len_bytes:
                        if DEBUG:
                            print("[DEBUG] Received empty length bytes.")
                        raise ConnectionResetError("Empty length; connection closed")
                    length = struct.unpack(">I", len_bytes)[0]  # Big-endian
                    if DEBUG:
                        print(f"[DEBUG] Received length: {length}")
                    if length > MAX_LENGTH or length == 0:
                        if DEBUG:
                            print(f"[DEBUG] Invalid length ({length}); skipping.")
                        continue
                    data_bytes = b""
                    while len(data_bytes) < length:
                        if DEBUG:
                            print(f"[DEBUG] Receiving data chunk (remaining: {length - len(data_bytes)})...")
                        chunk = cc.recv(min(1024, length - len(data_bytes)))  # Smaller chunks
                        if not chunk:
                            if DEBUG:
                                print("[DEBUG] Received empty chunk during data receive.")
                            raise ConnectionResetError("Incomplete data; connection closed")
                        data_bytes += chunk
                    data = data_bytes.decode('utf-8')
                    
                    if DEBUG:
                        print(f"[DEBUG] Received raw data: {data}")
                    
                    LAST_RECEIVE_TIME = time.time()  # Update on successful receive
                    
                    if data == "{}":
                        if DEBUG:
                            print("[DEBUG] Clearing Discord activity.")
                        presence.clear_activity()
                    else:
                        data_json = json.loads(data)
                        if DEBUG:
                            print(f"[DEBUG] Parsed JSON: {data_json}")
                        print("[DEBUG] Setting Discord activity.")
                        presence.set_activity(data_json)
                except socket.timeout:
                    if DEBUG:
                        print("[DEBUG] Timeout waiting for data, continuing to listen...")
                    # Removed the inactivity clear here to prevent clearing during ongoing playback
                    continue
                except json.JSONDecodeError as e:
                    if DEBUG:
                        print(f"[DEBUG] JSON decode error: {str(e)}. Ignoring bad data.")
                    continue
                except (ConnectionResetError, OSError) as e:
                    if DEBUG:
                        print(f"[DEBUG] Connection error during receive: {str(e)}. Clearing activity and reconnecting in {retry_delay}s...")
                    presence.clear_activity()  # Clear on disconnect
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_delay)
                    break
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            if DEBUG:
                print(f"[DEBUG] Connection error: {str(e)}. Clearing activity and reconnecting in {retry_delay}s...")
            presence.clear_activity()  # Clear on connection failure
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Unexpected error: {str(e)}. Clearing activity and reconnecting in {retry_delay}s...")
            presence.clear_activity()
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
        finally:
            try:
                cc.close()
                if DEBUG:
                    print("[DEBUG] Socket closed.")
            except:
                pass

def send(msg):
    client.send(msg.encode(FORMAT))

thread = threading.Thread(target=listen_for_message, args=(client,))
thread.start()

input()
send(DISCONNECT_MESSAGE)