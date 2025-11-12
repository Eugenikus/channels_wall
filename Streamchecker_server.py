import socket
import threading
import json
import csv
import time
import os
import requests
from datetime import datetime
import base64
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import struct

# Конфигурация
CONFIG_FILE = "streams.csv"
STATE_FILE = "channel_states.json"
OUTPUT_DIR = "screenshots"
INTERVAL = 10
MAX_CLIENTS = 10
MAX_FILES_PER_CHANNEL = 2

# Настройки сервера - слушаем на всех интерфейсах
SERVER_HOST = '0.0.0.0'  # Все интерфейсы
SERVER_PORT = 8888

# Настройки Telegram
TELEGRAM_BOT_TOKEN = "123123123:ASDASDASDASDASDASDASD"
TELEGRAM_CHAT_ID = "-123123123123123"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"

# Настройки прокси
#proxies = {
#    'http': 'http://192.168.11.7:3128',
#    'https': 'http://192.168.11.7:3128'
#}

os.makedirs(OUTPUT_DIR, exist_ok=True)

class ChannelState:
    def __init__(self):
        self.states = self.load_states()
    
    def load_states(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        return {}
    
    def save_states(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.states, f, indent=2)
    
    def is_channel_down(self, channel_num):
        return self.states.get(str(channel_num), {}).get('down', False)
    
    def get_last_message_id(self, channel_num):
        return self.states.get(str(channel_num), {}).get('last_message_id')
    
    def get_failure_count(self, channel_num):
        return self.states.get(str(channel_num), {}).get('failure_count', 0)
    
    def set_channel_state(self, channel_num, is_down, message_id=None, failure_count=0, screenshot_path=None, channel_name=None):
        if str(channel_num) not in self.states:
            self.states[str(channel_num)] = {
                'last_notified': None, 
                'last_message_id': None,
                'failure_count': 0,
                'last_screenshot': None,
                'name': None
            }
        
        prev_state = self.states[str(channel_num)].get('down', False)
        self.states[str(channel_num)]['down'] = is_down
        self.states[str(channel_num)]['failure_count'] = failure_count
        
        if screenshot_path:
            self.states[str(channel_num)]['last_screenshot'] = screenshot_path
        
        if channel_name:
            self.states[str(channel_num)]['name'] = channel_name
        
        if message_id:
            self.states[str(channel_num)]['last_message_id'] = message_id

        if is_down and not prev_state:
            self.states[str(channel_num)]['last_notified'] = datetime.now().isoformat()
        elif not is_down:
            self.states[str(channel_num)]['last_message_id'] = None
            self.states[str(channel_num)]['failure_count'] = 0
        
        self.save_states()

def pin_chat_message(message_id, disable_notification=True):
    try:
        response = requests.post(
            TELEGRAM_API_URL + "pinChatMessage",
            json={
                'chat_id': TELEGRAM_CHAT_ID,
                'message_id': message_id,
                'disable_notification': disable_notification
            }, #proxies=proxies,
            timeout=10
        )
        service_message_id = int(message_id) + 1
        if disable_notification:
            response = requests.post(
                TELEGRAM_API_URL + "deleteMessage",
                json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'message_id': service_message_id
                }, #proxies=proxies,
                timeout=10
            )
    except Exception as e:
        print(f"[Pin Error] {e}")

def unpin_chat_message(message_id):
    try:
        response = requests.post(
            TELEGRAM_API_URL + "unpinChatMessage",
            json={
                'chat_id': TELEGRAM_CHAT_ID,
                'message_id': message_id
            }, #proxies=proxies,
            timeout=10
        )
    except Exception as e:
        print(f"[Unpin Error] {e}")

def send_telegram_alert(message, image_path=None, reply_to_message_id=None):
    """Отправка уведомления в Telegram"""
    try:
        if image_path and os.path.exists(image_path):
            if reply_to_message_id:
                unpin_chat_message(reply_to_message_id)
            with open(image_path, 'rb') as photo:
                response = requests.post(
                    TELEGRAM_API_URL + "sendPhoto",
                    files={'photo': photo},
                    data={
                        'chat_id': TELEGRAM_CHAT_ID,
                        'caption': message,
                        'reply_to_message_id': reply_to_message_id
                    }, #proxies=proxies,
                    timeout=10
                )
        else:
            response = requests.post(
                TELEGRAM_API_URL + "sendMessage",
                json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': message,
                    'reply_to_message_id': reply_to_message_id
                }, #proxies=proxies,
                timeout=10
            )
        
        if response.status_code == 200:
            message_id = response.json().get('result', {}).get('message_id')
            return message_id
        return None
        
    except Exception as e:
        print(f"[Telegram Error] {e}")
        return None

def check_font():
    """Проверка доступности шрифтов"""
    try:
        linux_fonts = [
            '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        ]
        
        for font_path in linux_fonts:
            if os.path.exists(font_path):
                return font_path
        
        return "arial.ttf"
    except:
        return "arial.ttf"

FONT_PATH = check_font()

def add_overlay(frame, text, timestamp):
    """Добавляет оверлей с текстом"""
    try:
        img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        img_width, img_height = img_pil.size
        margin = 20
        bg_height = 250
        bg_opacity = 76
        
        lines = [str(timestamp), "", str(text) ]
        
        font = None
        font_size = 60
        
        for font_path in [FONT_PATH, "arial.ttf"]:
            try:
                if os.path.exists(font_path):
                    font = ImageFont.truetype(font_path, font_size)
                    break
            except:
                continue
        
        if font is None:
            font = ImageFont.load_default()
        
        optimal_font_size = font_size
        for line in lines:
            for size in range(60, 10, -5):
                try:
                    test_font = ImageFont.truetype(FONT_PATH, size) if os.path.exists(FONT_PATH) else ImageFont.load_default()
                    bbox = draw.textbbox((0, 0), line, font=test_font)
                    line_width = bbox[2] - bbox[0]
                    if line_width <= (img_width - 2 * margin):
                        if size < optimal_font_size:
                            optimal_font_size = size
                        break
                except:
                    continue
        
        try:
            if os.path.exists(FONT_PATH):
                font = ImageFont.truetype(FONT_PATH, optimal_font_size)
            else:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        overlay = Image.new('RGBA', img_pil.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [(0, 0), (img_width, bg_height)],
            fill=(0, 0, 0, bg_opacity)
        )
        
        y_position = 20
        for line in lines:
            try:
                bbox = overlay_draw.textbbox((0, 0), line, font=font)
                line_width = bbox[2] - bbox[0]
                x_position = (img_width - line_width) // 2
                
                overlay_draw.text(
                    (x_position, y_position),
                    line,
                    font=font,
                    fill=(255, 255, 255, 255)
                )
                y_position += optimal_font_size + 10
            except Exception as e:
                print(f"[Overlay Warning] {e}")
                y_position += optimal_font_size + 10
        
        img_pil = Image.alpha_composite(img_pil.convert('RGBA'), overlay)
        return cv2.cvtColor(np.array(img_pil.convert('RGB')), cv2.COLOR_RGB2BGR)
    
    except Exception as e:
        print(f"[Ошибка оверлея] {str(e)}")
        return frame

def save_screenshot_from_base64(base64_data, channel_num, channel_name):
    """Сохранение скриншота из base64 данных"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = f"channel_{channel_num}_{timestamp}"
        
        clean_path = os.path.join(OUTPUT_DIR, f"{base_name}_clean.jpg")
        overlay_path = os.path.join(OUTPUT_DIR, f"{base_name}_overlay.jpg")
        
        image_data = base64.b64decode(base64_data)
        nparr = np.frombuffer(image_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is not None:
            cv2.imwrite(clean_path, frame)
            overlay_frame = add_overlay(frame, f"{channel_num} - {channel_name}", 
                                      datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            cv2.imwrite(overlay_path, overlay_frame)
            
            print(f"[Server] Screenshot saved: {overlay_path}")
            return overlay_path
        return None
        
    except Exception as e:
        print(f"[Screenshot Error] {e}")
        return None

def clean_old_files(channel_num):
    """Очистка старых файлов"""
    try:
        files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith(f"channel_{channel_num}_")]
        if len(files) <= MAX_FILES_PER_CHANNEL * 2:
            return
            
        files.sort(key=lambda x: os.path.getctime(os.path.join(OUTPUT_DIR, x)))
        for f in files[:-(MAX_FILES_PER_CHANNEL * 2)]:
            os.remove(os.path.join(OUTPUT_DIR, f))
    except Exception as e:
        print(f"[Clean Error] {e}")

def send_message(sock, data):
    """Отправка сообщения с длиной префикса"""
    try:
        message = json.dumps(data).encode('utf-8')
        message_length = struct.pack('!I', len(message))
        sock.sendall(message_length + message)
    except Exception as e:
        print(f"[Send Error] {e}")

def receive_message(sock):
    """Прием сообщения с длиной префикса"""
    try:
        length_data = b''
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        
        message_length = struct.unpack('!I', length_data)[0]
        
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

class ChannelManager:
    def __init__(self):
        self.channels = self.load_channels()
        self.current_index = 0
        self.lock = threading.Lock()
        self.channel_state = ChannelState()
        
    def load_channels(self):
        """Загрузка списка каналов"""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                channels = list(csv.DictReader(f))
                print(f"[Server] Loaded {len(channels)} channels")
                return channels
        except Exception as e:
            print(f"[Server Error] Failed to load channels: {e}")
            return []
    
    def get_next_channel(self):
        """Получить следующий канал для проверки"""
        with self.lock:
            if not self.channels:
                return None
            
            channel = self.channels[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.channels)
            if self.current_index == len(self.channels)-1:
                self.channels = self.load_channels()
            return channel
    
    def process_check_result(self, channel_num, is_working, response_time, screenshot_base64=None, client_info=""):
        """Обработка результата проверки канала"""
        channel = next((c for c in self.channels if c['number'] == channel_num), None)
        if not channel:
            return
        
        name = channel['name']
        double_check = channel.get('double', '0') == '2'
        no_check = channel.get('double', '0') == '0'
        if no_check:
            print(f"\n[Server] Processing result for {channel_num} - {name}: Skipped by conf ({response_time:.1f}s) from {client_info}")
            return
        
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n[Server] Processing result for {channel_num} - {name}: {'✅' if is_working else '❌'} ({response_time:.1f}s) from {client_info}")
        
        is_down = not is_working
        was_down = self.channel_state.is_channel_down(channel_num)
        failure_count = self.channel_state.get_failure_count(channel_num)
        current_message_id = self.channel_state.get_last_message_id(channel_num)
        
        screenshot_path = None
        if screenshot_base64 and is_working:
            screenshot_path = save_screenshot_from_base64(screenshot_base64, channel_num, name)
            clean_old_files(channel_num)
        
        if is_down:
            if not was_down:
                print(f"[Alert] Канал {channel_num} - {name} не отвечает")
                
                if double_check:
                    new_failure_count = 1
                    message_id = None
                    print(f"[Double Check] Первое обнаружение, счетчик: {new_failure_count}")
                    if message_id:
                        print(f"[Double Check] message_id: {message_id}")
                else:
                    new_failure_count = 1
                    message_id = send_telegram_alert(f"❌ Канал {channel_num} - {name} не отвечает")
                    print(f"[Single Check] Сообщение отправлено, ID: {message_id}")
                
                self.channel_state.set_channel_state(channel_num, is_down, message_id, new_failure_count, screenshot_path, name)
                
            else:
                if double_check:
                    new_failure_count = failure_count + 1
                    print(f"[Double Check] Счетчик увеличен: {failure_count} -> {new_failure_count}")
                    
                    if new_failure_count == 2:
                        message_id = send_telegram_alert(f"❌ Канал {channel_num} - {name} не отвечает")
                        print(f"[Double Check] Второе обнаружение - сообщение отправлено, ID: {message_id}")
                        self.channel_state.set_channel_state(channel_num, is_down, message_id, new_failure_count, screenshot_path, name)
                    elif new_failure_count == 3:
                        if current_message_id:
                            pin_chat_message(current_message_id, False)
                            print(f"[Double Check] Третье обнаружение - сообщение прикреплено, ID: {current_message_id}")
                        self.channel_state.set_channel_state(channel_num, is_down, current_message_id, new_failure_count, screenshot_path, name)
                    else:
                        print(f"[Double Check] Последующие проверки - тишина (счетчик: {new_failure_count})")
                        self.channel_state.set_channel_state(channel_num, is_down, current_message_id, new_failure_count, screenshot_path, name)
                    
                else:
                    self.channel_state.set_channel_state(channel_num, is_down, current_message_id, 1, screenshot_path, name)
                    if current_message_id:
                        pin_chat_message(current_message_id, False)
        
        else:
            if was_down:
                print(f"[Recovery] Канал {channel_num} - {name} восстановлен")
                if current_message_id:
                    unpin_chat_message(current_message_id)
                
                    message_id = send_telegram_alert(
                        f"✅ Канал {channel_num} - {name} восстановлен", 
                        screenshot_path, 
                        current_message_id
                    )
                    
                self.channel_state.set_channel_state(channel_num, is_down, None, 0, screenshot_path, name)
                
            else:
                self.channel_state.set_channel_state(channel_num, is_down, None, 0, screenshot_path, name)

class StreamCheckerServer:
    def __init__(self, host=SERVER_HOST, port=SERVER_PORT):
        self.host = host
        self.port = port
        self.channel_manager = ChannelManager()
        self.clients = []
        self.running = False
    
    def handle_client(self, client_socket, address):
        """Обработка клиентского соединения"""
        print(f"[Server] New connection from {address}")
        
        try:
            while self.running:
                channel = self.channel_manager.get_next_channel()
                if not channel:
                    send_message(client_socket, {'error': 'No channels available'})
                    break
                
                request = {
                    'action': 'check_channel',
                    'channel': channel
                }
                
                send_message(client_socket, request)
                
                response = receive_message(client_socket)
                if not response:
                    break
                
                if response.get('action') == 'check_result':
                    channel_num = response['channel_num']
                    is_working = response['is_working']
                    response_time = response.get('response_time', 0)
                    screenshot_base64 = response.get('screenshot_base64')
                    
                    self.channel_manager.process_check_result(
                        channel_num, is_working, response_time, screenshot_base64, str(address)
                    )
                
                time.sleep(1)
                
        except ConnectionError:
            print(f"[Server] Client {address} disconnected")
        except Exception as e:
            print(f"[Server Error] Handling client {address}: {e}")
        finally:
            client_socket.close()
            if client_socket in self.clients:
                self.clients.remove(client_socket)
    
    def start_server(self):
        """Запуск сервера"""
        self.running = True
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server_socket.bind((self.host, self.port))
            server_socket.listen(MAX_CLIENTS)
            server_socket.settimeout(1.0)
            
            # Получаем IP адреса сервера
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            
            print(f"[Server] Started on {self.host}:{self.port}")
            print(f"[Server] Local IP: {local_ip}")
            print(f"[Server] Monitoring {len(self.channel_manager.channels)} channels")
            print(f"[Server] Waiting for clients...")
            
            while self.running:
                try:
                    client_socket, address = server_socket.accept()
                    client_thread = threading.Thread(
                        target=self.handle_client, 
                        args=(client_socket, address),
                        daemon=True
                    )
                    self.clients.append(client_socket)
                    client_thread.start()
                    
                except socket.timeout:
                    continue
                except OSError as e:
                    if self.running:
                        print(f"[Server Error] Accept failed: {e}")
                    break
            
        except Exception as e:
            print(f"[Server Error] Failed to start server: {e}")
        finally:
            self.running = False
            server_socket.close()
            
            for client in self.clients:
                try:
                    client.close()
                except:
                    pass
            
            print("[Server] Stopped")

    def stop_server(self):
        """Остановка сервера"""
        self.running = False

def main():
    server = StreamCheckerServer()
    
    try:
        server.start_server()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
        server.stop_server()

if __name__ == "__main__":
    main()