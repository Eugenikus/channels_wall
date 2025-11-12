import socket
import json
import cv2
import numpy as np
import time
from datetime import datetime
import subprocess
import os
import base64
from contextlib import contextmanager
import struct

# Настройки для захвата видео
BUFFER_TIMEOUT = 30
os.environ['GST_DEBUG'] = '0'
os.environ['FFREPORT'] = 'file=ffreport.log:level=0'
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "stimeout;5000"

@contextmanager
def suppress_ffmpeg_output():
    """Полное подавление вывода FFmpeg"""
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs = {
            'startupinfo': startupinfo, 
            'creationflags': subprocess.CREATE_NO_WINDOW,
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL
        }
    else:
        kwargs = {
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL
        }
    
    original_stderr = os.dup(2)
    original_stdout = os.dup(1)
    
    with open(os.devnull, 'w') as fnull:
        os.dup2(fnull.fileno(), 2)
        os.dup2(fnull.fileno(), 1)
        
        try:
            yield
        finally:
            os.dup2(original_stderr, 2)
            os.dup2(original_stdout, 1)
            os.close(original_stderr)
            os.close(original_stdout)

def send_message(sock, data):
    """Отправка сообщения с длиной префикса"""
    try:
        message = json.dumps(data).encode('utf-8')
        # Добавляем префикс с длиной сообщения (4 байта)
        message_length = struct.pack('!I', len(message))
        sock.sendall(message_length + message)
    except Exception as e:
        print(f"[Send Error] {e}")

def receive_message(sock):
    """Прием сообщения с длиной префикса"""
    try:
        # Сначала читаем длину сообщения
        length_data = b''
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        
        message_length = struct.unpack('!I', length_data)[0]
        
        # Читаем само сообщение
        message_data = b''
        while len(message_data) < message_length:
            chunk = sock.recv(min(4096, message_length - len(message_data)))
            if not chunk:
                return None
            message_data += chunk
        
        return json.loads(message_data.decode('utf-8'))
    except Exception as e:
        print(f"[Receive Error] {e}")
        return None

def capture_frame_with_screenshot(stream_url):
    """Захват кадра и создание скриншота"""
    cap = None
    start_time = time.time()
    
    with suppress_ffmpeg_output():
        try:
            # Пытаемся открыть поток с таймаутом
            while time.time() - start_time < BUFFER_TIMEOUT:
                cap = cv2.VideoCapture(stream_url, apiPreference=cv2.CAP_FFMPEG, 
                                     params=[cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
                if cap.isOpened():
                    break
                time.sleep(0.1)
                
            if not cap or not cap.isOpened():
                return None, None, time.time() - start_time
            
            # Пытаемся получить кадр в оставшееся время
            remaining_time = BUFFER_TIMEOUT - (time.time() - start_time)
            if remaining_time > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, 0)
                start_read_time = time.time()
                while time.time() - start_read_time < remaining_time:
                    ret, frame = cap.read()
                    if ret:
                        # Конвертируем кадр в base64 с уменьшением качества для экономии трафика
                        success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 30])
                        if success:
                            screenshot_base64 = base64.b64encode(buffer).decode('utf-8')
                        else:
                            screenshot_base64 = None
                        return frame, screenshot_base64, time.time() - start_time
                    time.sleep(0.1)
            
            return None, None, time.time() - start_time
            
        except Exception as e:
            print(f"[Client Error] Capture failed: {e}")
            return None, None, time.time() - start_time
        finally:
            if cap is not None:
                cap.release()

def check_channel(stream_url):
    """Проверить доступность канала и сделать скриншот"""
    print(f"[Client] Checking stream: {stream_url}")
    
    frame, screenshot_base64, response_time = capture_frame_with_screenshot(stream_url)
    is_working = frame is not None
    
    status = "✅ Working" if is_working else "❌ Failed"
    print(f"[Client] Stream check: {status} ({response_time:.2f}s)")
    
    return is_working, response_time, screenshot_base64

class StreamCheckerClient:
    def __init__(self, server_host='localhost', server_port=8888):
        self.server_host = server_host
        self.server_port = server_port
        self.running = False
    
    def connect_to_server(self):
        """Подключение к серверу"""
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.connect((self.server_host, self.server_port))
            print(f"[Client] Connected to server {self.server_host}:{self.server_port}")
            return client_socket
        except Exception as e:
            print(f"[Client Error] Failed to connect: {e}")
            return None
    
    def start_client(self):
        """Запуск клиента"""
        self.running = True
        
        while self.running:
            client_socket = self.connect_to_server()
            if not client_socket:
                print("[Client] Retrying in 5 seconds...")
                time.sleep(5)
                continue
            
            try:
                while self.running:
                    # Получаем задание от сервера
                    request = receive_message(client_socket)
                    if not request:
                        print("[Client] Server disconnected")
                        break
                    
                    if request.get('action') == 'check_channel':
                        channel = request['channel']
                        stream_url = channel['url']
                        channel_num = channel['number']
                        channel_name = channel['name']
                        
                        print(f"[Client] Checking channel {channel_num} - {channel_name}")
                        
                        # Проверяем канал и делаем скриншот
                        is_working, response_time, screenshot_base64 = check_channel(stream_url)
                        
                        # Отправляем результат на сервер
                        response = {
                            'action': 'check_result',
                            'channel_num': channel_num,
                            'channel_name': channel_name,
                            'is_working': is_working,
                            'response_time': response_time,
                            'screenshot_base64': screenshot_base64,
                            'timestamp': datetime.now().isoformat()
                        }
                        
                        send_message(client_socket, response)
                        
                    elif request.get('error'):
                        print(f"[Client] Server error: {request['error']}")
                        break
                        
            except ConnectionError:
                print("[Client] Connection lost")
            except Exception as e:
                print(f"[Client Error] {e}")
            finally:
                client_socket.close()
            
            if self.running:
                print("[Client] Reconnecting in 3 seconds...")
                time.sleep(3)
    
    def stop_client(self):
        """Остановка клиента"""
        self.running = False

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Stream Checker Client')
    parser.add_argument('--host', default='localhost', help='Server host')
    parser.add_argument('--port', type=int, default=8888, help='Server port')
    
    args = parser.parse_args()
    
    client = StreamCheckerClient(args.host, args.port)
    
    try:
        client.start_client()
    except KeyboardInterrupt:
        print("\n[Client] Shutting down...")
        client.stop_client()

if __name__ == "__main__":
    main()