import urllib.request
import urllib.parse
import json
import time
import re
import logging
from functools import lru_cache
from typing import Dict, List, Optional, Set
import threading
from dataclasses import dataclass, asdict
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация (будет импортирована из config.py)
from config import BOT_TOKEN, HA_TOKEN, HA_URL, MINI_APP_PORT, SECRET_KEY

# Константы
CONTROLLABLE_DOMAINS: Set[str] = {'light', 'switch', 'climate', 'cover', 'fan', 'input_boolean'}
INVALID_STATES: Set[str] = {'unavailable', 'unknown', 'None', ''}
WORDS_TO_REMOVE: Set[str] = {
    'свет', 'light', 'switch', 'выключатель', 'розетка', 'лампа', 
    'lamp', 'управление', 'control', 'temperature humidity sensor'
}

SENSORS_MAP: Dict[str, Dict[str, str]] = {
    'Домик 1': {
        'battery': 'sensor.temperature_humidity_sensor_battery',
        'temperature_indoor': 'sensor.temperature_humidity_sensor_temperature',
        'humidity': 'sensor.temperature_humidity_sensor_humidity',
        'temperature_outdoor': 'sensor.kws_306wf_t1_temperature_2',
        'voltage_l1': 'sensor.kws_306wf_t1_voltage_a_2',
        'voltage_l2': 'sensor.kws_306wf_t1_voltage_b_2',
        'voltage_l3': 'sensor.kws_306wf_t1_voltage_c_2',
        'total_energy': 'sensor.kws_306wf_t1_total_energy'
    }
}

@dataclass
class Device:
    entity_id: str
    name: str
    short_name: str
    state: str
    domain: str
    attributes: Dict
    
@dataclass
class Area:
    name: str
    devices: List[Device]
    sensors: Dict
    stats: Dict

class OptimizedHomeBot:
    def init(self):
        self.last_id = 0
        self.user_states: Dict[int, Dict] = {}
        self.devices_cache: Dict[str, List] = {}
        self.sensors_cache: Dict[str, Dict] = {}
        self._all_devices: Optional[List[Dict]] = None
        self._cache_timestamp: Dict[str, float] = {}
        self.CACHE_TTL = 2  # Уменьшили TTL для мини-приложения
        
        # Предкомпилированные regex паттерны
        self._compile_patterns()
    
    def _compile_patterns(self) -> None:
        """Компиляция всех regex паттернов один раз при инициализации"""
        self.HOUSE_PATTERNS: Dict[str, List[re.Pattern]] = {}
        for i in range(1, 11):
            patterns = [
                re.compile(rf'дом\s*{i}', re.IGNORECASE),
                re.compile(rf'домик\s*{i}', re.IGNORECASE),
                re.compile(rf'коттедж\s*{i}', re.IGNORECASE),
                re.compile(rf'house\s*{i}', re.IGNORECASE),
                re.compile(rf'cottage\s*{i}', re.IGNORECASE),
                re.compile(rf'\b{i}\b', re.IGNORECASE),
                re.compile(rf't{i}', re.IGNORECASE),
                re.compile(rf'kws-306wf t{i}', re.IGNORECASE)
            ]
            self.HOUSE_PATTERNS[f'Домик {i}'] = patterns
        
        self.HOUSE_PATTERNS['Домик 1'].append(re.compile('temperature humidity sensor', re.IGNORECASE))
        
        # Паттерн для извлечения device_short_name
        self.device_name_pattern = re.compile(r'вкл|выкл', re.IGNORECASE)
        
    def _make_request(self, url: str, data: Optional[Dict] = None, headers: Optional[Dict] = None) -> Optional[Dict]:
        """Универсальный метод для HTTP запросов"""
        try:
            if data and isinstance(data, (dict, list)):
                data = json.dumps(data).encode('utf-8')
                headers = headers or {}
                headers.setdefault('Content-Type', 'application/json')
            elif data and isinstance(data, str):
                data = data.encode('utf-8')
            
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None
    def telegram(self, method: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """Вызов Telegram API"""
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        
        if data and 'parse_mode' in data:
            data = urllib.parse.urlencode(data).encode('utf-8')
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            return self._make_request(url, data, headers)
        return self._make_request(url, data)

    def home_assistant(self, endpoint: str, data: Optional[Dict] = None, method: str = "GET") -> Optional[Dict]:
        """Вызов Home Assistant API"""
        url = f"{HA_URL}/api/{endpoint}"
        headers = {"Authorization": f"Bearer {HA_TOKEN}"}
        return self._make_request(url, data, headers)

    def get_sensor_state(self, entity_id: str) -> Optional[Dict]:
        """Получение состояния датчика"""
        result = self.home_assistant(f"states/{entity_id}")
        return self._parse_sensor_data(result) if result else None

    def _parse_sensor_data(self, result: Dict) -> Optional[Dict]:
        """Парсинг данных сенсора"""
        if 'state' not in result:
            return None
        return {
            'value': result['state'],
            'unit': result['attributes'].get('unit_of_measurement', ''),
            'name': result['attributes'].get('friendly_name', ''),
            'entity_id': result['entity_id']
        }

    def get_kws_sensors_data(self, area_name: str) -> Optional[Dict]:
        """Получение данных с датчиков KWS"""
        if area_name not in SENSORS_MAP:
            return None
            
        kws_data = {}
        for sensor_type, entity_id in SENSORS_MAP[area_name].items():
            sensor_data = self.get_sensor_state(entity_id)
            if sensor_data and self._is_numeric_value(sensor_data['value']):
                kws_data[sensor_type] = sensor_data
        
        self.sensors_cache[area_name] = kws_data
        return kws_data

    def _is_numeric_value(self, value: str) -> bool:
        """Проверка числового значения"""
        return value and value not in INVALID_STATES and self._is_float(value)

    def _is_float(self, value: str) -> bool:
        """Проверка возможности преобразования в float"""
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def format_kws_data(self, kws_data: Optional[Dict]) -> str:
        """Форматирование данных KWS"""
        if not kws_data:
            return "Данные с датчиков недоступны"
        
        lines = ["<b>Данные с датчиков KWS:</b>"]
        
        # Температуры
        if kws_data.get('temperature_indoor'):
            t = kws_data['temperature_indoor']
            lines.append(f"Комнатная температура: {t['value']} {t['unit']}")
        if kws_data.get('temperature_outdoor'):
            t = kws_data['temperature_outdoor']
            lines.append(f"Уличная температура: {t['value']} {t['unit']}")
        
        # Влажность и батарея
        if kws_data.get('humidity'):
            h = kws_data['humidity']
            lines.append(f"Влажность: {h['value']} {h['unit']}")
        if kws_data.get('battery'):
            b = kws_data['battery']
            lines.append(f"Батарея: {b['value']} {b['unit']}")
        
        # Напряжения
        voltages = []
        for phase in ['l1', 'l2', 'l3']:
            if kws_data.get(f'voltage_{phase}'):
                v = kws_data[f'voltage_{phase}']
                voltages.append(f"{phase.upper()}: {v['value']}{v['unit']}")
        if voltages:
            lines.append(f"Напряжение: {', '.join(voltages)}")
        
        # Энергия
        if kws_data.get('total_energy'):
            e = kws_data['total_energy']
            lines.append(f"Общая энергия: {e['value']} {e['unit']}")
        
        return "\n".join(lines) if len(lines) > 1 else "Данные временно недоступны"
    def get_all_devices(self) -> List[Dict]:
        """Получение всех устройств с кэшированием"""
        if self._all_devices is None:
            self._all_devices = self.home_assistant("states") or []
        return self._all_devices

    def _is_controllable_device(self, entity_id: str) -> bool:
        """Проверка, является ли устройство управляемым"""
        return entity_id.split('.')[0] in CONTROLLABLE_DOMAINS

    def _assign_device_to_house(self, friendly_name: str) -> str:
        """Определение принадлежности устройства к домику"""
        friendly_name_lower = friendly_name.lower()
        for house_name, patterns in self.HOUSE_PATTERNS.items():
            if any(pattern.search(friendly_name_lower) for pattern in patterns):
                return house_name
        return 'Общие устройства'

    def manual_area_grouping(self, force_refresh: bool = False) -> Dict[str, List[Dict]]:
        """Группировка устройств по домикам"""
        if force_refresh:
            self._all_devices = None
        
        all_devices = self.get_all_devices()
        houses = {f'Домик {i}': [] for i in range(1, 11)}
        houses['Общие устройства'] = []
        
        # Фильтрация управляемых устройств
        controllable_devices = [
            device for device in all_devices 
            if self._is_controllable_device(device['entity_id'])
        ]
        
        # Группировка по домикам
        for device in controllable_devices:
            friendly_name = device['attributes'].get('friendly_name', '')
            house_name = self._assign_device_to_house(friendly_name)
            houses[house_name].append(device)
        
        return {k: v for k, v in houses.items() if v}

    def load_areas_with_devices(self, force_refresh: bool = False) -> Dict[str, List[Dict]]:
        """Загрузка пространств с устройствами с проверкой TTL"""
        area_name = "all"  # Для общего кэша
        
        # Проверяем, нужно ли обновлять кэш
        current_time = time.time()
        cache_valid = (
            not force_refresh and 
            area_name in self._cache_timestamp and 
            (current_time - self._cache_timestamp[area_name]) < self.CACHE_TTL and
            area_name in self.devices_cache
        )
        
        if not cache_valid:
            self.devices_cache = self.manual_area_grouping(force_refresh)
            self._cache_timestamp[area_name] = current_time
            if force_refresh:
                logger.info("Кэш устройств принудительно обновлен")
            else:
                logger.info(f"Загружено {len(self.devices_cache)} пространств")
        
        return self.devices_cache

    def get_area_devices(self, area_name: str, force_refresh: bool = False) -> List[Dict]:
        """Получение устройств пространства с обновлением по необходимости"""
        # Принудительное обновление для статуса
        if force_refresh:
            self.devices_cache = {}
            self._all_devices = None
            self._cache_timestamp = {}
            self.get_all_areas_list.cache_clear()
        
        return self.load_areas_with_devices(force_refresh).get(area_name, [])

    @lru_cache(maxsize=1)
    def get_all_areas_list(self) -> List[str]:
        """Кэшированный список всех пространств"""
        areas = list(self.load_areas_with_devices().keys())
        houses = sorted(
            [a for a in areas if 'Домик' in a], 
            key=lambda x: int(re.search(r'\d+', x).group())
        )
        others = [a for a in areas if 'Домик' not in a]
        return houses + others

    def shorten_name(self, name: str, area_name: str = "") -> str:
        """Сокращение имени устройства"""
        # Удаляем название домика
        clean_name = re.sub(rf'{re.escape(area_name)}', '', name, flags=re.IGNORECASE)# Удаляем common words
        for word in WORDS_TO_REMOVE:
            clean_name = clean_name.replace(word, '')
        
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        return clean_name[:12] + "..." if len(clean_name) > 12 else clean_name or "Устройство"

    def create_house_menu(self, area_name: str) -> Dict:
        """Создание меню для домика"""
        # Используем актуальные данные без кэширования для меню
        devices = self.get_area_devices(area_name, force_refresh=False)
        if not devices:
            keyboard = [["Назад"]]
            return {"keyboard": keyboard, "resize_keyboard": True}
        
        keyboard = [["Статус", "Данные"]]
        
        for device in devices[:6]:  # Ограничиваем 6 устройствами
            name = device['attributes'].get('friendly_name', device['entity_id'])
            state_indicator = "ВКЛ" if device['state'] == 'on' else "ВЫКЛ"
            
            short_name = self.shorten_name(name, area_name)
            
            # Статус устройства с индикатором
            keyboard.append([f"{state_indicator} {short_name}"])
            
            # Кнопки управления
            action_text = "ВЫКЛ" if device['state'] == 'on' else "ВКЛ"
            keyboard.append([f"{action_text} {short_name}"])
        
        keyboard.append(["Назад"])
        
        return {"keyboard": keyboard, "resize_keyboard": True}

    def create_main_menu(self) -> Dict:
        """Создание главного меню"""
        areas = self.get_all_areas_list()
        keyboard = []
        
        if not areas:
            keyboard.append(["Обновить"])
            return {"keyboard": keyboard, "resize_keyboard": True}
        
        houses = [a for a in areas if 'Домик' in a]
        others = [a for a in areas if 'Домик' not in a]
        
        for i in range(0, len(houses), 2):
            row = houses[i:i+2]
            keyboard.append(row)
        
        for area in others:
            keyboard.append([area])
        
        keyboard.append(["Обновить", "Статус всех"])
        
        return {"keyboard": keyboard, "resize_keyboard": True}

    def send_message(self, chat_id: int, text: str, menu_type: str = "main", area_name: Optional[str] = None) -> bool:
        """Отправка сообщения с меню"""
        try:
            if menu_type == "house" and area_name:
                menu = self.create_house_menu(area_name)
            else:
                menu = self.create_main_menu()
            
            message_data = {
                "chat_id": chat_id, 
                "text": text, 
                "parse_mode": "HTML",
                "reply_markup": json.dumps(menu)
            }
            
            result = self.telegram("sendMessage", message_data)
            
            if result is None:
                logger.error("Ошибка отправки сообщения в Telegram")
                return False
            return True
            
        except Exception as e:
            logger.error(f"Send message error: {e}")
            return False

    def set_user_state(self, chat_id: int, state: str, data: Optional[str] = None) -> None:
        """Устанавливаем состояние пользователя"""
        self.user_states[chat_id] = {'state': state, 'data': data}

    def get_user_state(self, chat_id: int) -> Dict:
        """Получаем состояние пользователя"""
        return self.user_states.get(chat_id, {'state': 'main'})

    def handle_message(self, chat_id: int, text: str) -> None:
        """Обработка входящих сообщений"""
        try:
            text_lower = text.lower()
            user_state = self.get_user_state(chat_id)
            
            logger.info(f"Получено сообщение: {text} (состояние: {user_state['state']})")
            
            # Обработка команд сброса состоянияif text_lower in ['/start', 'обновить', 'назад', 'статус всех']:
            self.set_user_state(chat_id, 'main')
            user_state = {'state': 'main'}
            # При обновлении сбрасываем кэш
            if text_lower == 'обновить':
                self.devices_cache = {}
                self._all_devices = None
                self._cache_timestamp = {}
                self.get_all_areas_list.cache_clear()
            
            if text_lower == '/start' or text_lower == 'обновить':
                areas = self.load_areas_with_devices(force_refresh=True)
                house_count = len([a for a in areas if 'Домик' in a])
                total_devices = sum(len(devices) for devices in areas.values())
                
                message = f"""<b>Управление домиками</b>

Найдено домиков: {house_count}
Всего устройств: {total_devices}

Выбери домик для управления:"""
                if not self.send_message(chat_id, message):
                    logger.error("Не удалось отправить сообщение")
            
            elif text_lower == 'статус всех':
                self.show_all_houses_status(chat_id)
            
            elif user_state['state'] == 'main':
                # Главное меню - обработка выбора домика
                if any(area in text for area in ['Домик', 'Общие']):
                    area_name = text
                    devices = self.get_area_devices(area_name, force_refresh=False)
                    
                    if devices:
                        on_count = len([d for d in devices if d['state'] == 'on'])
                        status = f"""<b>{area_name}</b>

Устройств: {len(devices)}
Включено: {on_count}
Выключено: {len(devices) - on_count}

Управление устройствами:"""
                        
                        self.set_user_state(chat_id, 'house', area_name)
                        if not self.send_message(chat_id, status, "house", area_name):
                            logger.error("Не удалось отправить сообщение")
                    else:
                        if not self.send_message(chat_id, f"В {area_name} нет устройств"):
                            logger.error("Не удалось отправить сообщение")
                else:
                    if not self.send_message(chat_id, "Выбери домик из меню"):
                        logger.error("Не удалось отправить сообщение")
            
            elif user_state['state'] == 'house':
                # Подменю домика - обработка команд управления
                area_name = user_state['data']
                self.handle_house_commands(chat_id, text, area_name)
            
            else:
                if not self.send_message(chat_id, "Неизвестная команда"):
                    logger.error("Не удалось отправить сообщение")

        except Exception as e:
            logger.error(f"Handle message error: {e}")
            if not self.send_message(chat_id, "Ошибка обработки команды"):
                logger.error("Не удалось отправить сообщение об ошибке")

    def handle_house_commands(self, chat_id: int, text: str, area_name: str) -> None:
        """Обработка команд в подменю домика"""
        text_lower = text.lower()
        
        if text_lower == 'назад':
            self.set_user_state(chat_id, 'main')
            if not self.send_message(chat_id, "<b>Выбери домик</b>"):
                logger.error("Не удалось отправить сообщение")
        
        elif text_lower == 'данные':
            self.show_kws_data(chat_id, area_name)
        
        elif text_lower == 'статус':
            self.show_area_status(chat_id, area_name)
        
        elif 'вкл' in text_lower or 'выкл' in text_lower:
            logger.info(f"Команда управления устройством: {text}")
            self.control_single_device(chat_id, text, area_name)
        
        else:
            if not self.send_message(chat_id, "Неизвестная команда", "house", area_name):
                logger.error("Не удалось отправить сообщение")

    def control_single_device(self, chat_id: int, text: str, area_name: str) -> None:
        """Управление отдельным устройством в домике"""
        action = "on" if "вкл" in text.lower() else "off"
        
        # Используем предкомпилированный паттерн для извлечения имени
        device_short_name = self.device_name_pattern.sub('', text).strip()
        
        logger.info(f"Поиск устройства: '{device_short_name}' в {area_name}")
        
        # Получаем актуальные данные без кэша
        devices = self.get_area_devices(area_name, force_refresh=False)
        
        for device in devices:
            full_name = device['attributes'].get('friendly_name', '')
            short_name = self.shorten_name(full_name, area_name)
            
            if short_name == device_short_name:
                entity_id = device['entity_id']
                logger.info(f"Найдено устройство: {full_name} -> {entity_id}")
                
                if self.control_device(entity_id, action):
                    time.sleep(0.5)
                    # Сбрасываем кэш после управления
                    self.devices_cache = {}
                    self._all_devices = None
                    self._cache_timestamp = {}
                    self.get_all_areas_list.cache_clear()
                    
                    state = "ВКЛ" if action == "on" else "ВЫКЛ"
                    if not self.send_message(chat_id, f"{short_name} - {state}", "house", area_name):
                        logger.error("Не удалось отправить сообщение")
                else:
                    if not self.send_message(chat_id, f"Ошибка управления {short_name}", "house", area_name):
                        logger.error("Не удалось отправить сообщение")
                return
        
        logger.warning(f"Устройство не найдено: {device_short_name}")
        if not self.send_message(chat_id, f"Устройство не найдено: {device_short_name}", "house", area_name):
            logger.error("Не удалось отправить сообщение")

    def show_area_status(self, chat_id: int, area_name: str) -> None:
        """Показывает статус конкретного пространства с актуальными данными"""
        # Принудительно обновляем данные для статуса
        devices = self.get_area_devices(area_name, force_refresh=True)
        if not devices:
            if not self.send_message(chat_id, f"В {area_name} нет устройств", "house", area_name):
                logger.error("Не удалось отправить сообщение")
            return
            
        on_count = len([d for d in devices if d['state'] == 'on'])
        
        status_text = f"""<b>Статус {area_name}</b>

Всего устройств: {len(devices)}
Включено: {on_count}
Выключено: {len(devices) - on_count}

Состояния:"""
        
        for device in devices:
            name = device['attributes'].get('friendly_name', device['entity_id'])
            short_name = self.shorten_name(name, area_name)
            state = "ВКЛ" if device['state'] == 'on' else "ВЫКЛ"
            status_text += f"\n• {short_name}: {state}"
        
        if not self.send_message(chat_id, status_text, "house", area_name):
            logger.error("Не удалось отправить сообщение")

    def show_all_houses_status(self, chat_id: int) -> None:
        """Показывает статус всех домиков с актуальными данными"""
        # Обновляем все данные
        self.devices_cache = {}
        self._all_devices = None
        self._cache_timestamp = {}
        self.get_all_areas_list.cache_clear()
        
        areas = self.load_areas_with_devices(force_refresh=True)
        areas_list = self.get_all_areas_list()
        
        status_text = "<b>Статус всех домиков</b>\n\n"
        
        total_devices = 0
        total_on = 0
        
        for area in areas_list:
            devices = areas.get(area, [])
            on_count = len([d for d in devices if d['state'] == 'on'])
            total_devices += len(devices)
            total_on += on_count
            
            status_text += f"<b>{area}</b>: {on_count}/{len(devices)} вкл\n"
        
        status_text += f"\n<b>Итого</b>: {total_on}/{total_devices} устройств включено"
        if not self.send_message(chat_id, status_text):
            logger.error("Не удалось отправить сообщение")

    def show_kws_data(self, chat_id: int, area_name: str) -> None:
        """Показывает данные с датчиков KWS"""
        logger.info(f"Получение данных KWS для {area_name}")
        kws_data = self.get_kws_sensors_data(area_name)
        message = self.format_kws_data(kws_data)
        
        if not self.send_message(chat_id, message, "house", area_name):
            logger.error("Не удалось отправить данные KWS")

    def control_device(self, entity_id: str, action: str) -> bool:
        """Управление устройством"""
        try:
            logger.info(f"Управление устройством: {entity_id} -> {action}")
            
            domain = entity_id.split('.')[0]
            service_data = {"entity_id": entity_id}
            
            result = self.home_assistant(
                f"services/{domain}/turn_{action}",
                service_data,
                "POST"
            )
            
            if result is not None:
                logger.info(f"Успешно: {entity_id} -> {action}")
                return True
            else:
                logger.error(f"Ошибка API: {entity_id} -> {action}")
                return False
                
        except Exception as e:
            logger.error(f"Исключение при управлении {entity_id}: {e}")
            return False

    # Новые методы для API и мобильного приложения
    
    def get_area_data_for_api(self, area_name: str) -> Dict:
        """Получение данных пространства для API"""
        devices_data = self.get_area_devices(area_name, force_refresh=True)
        kws_data = self.get_kws_sensors_data(area_name)
        
        devices = []
        for device_data in devices_data:
            device = Device(
                entity_id=device_data['entity_id'],
                name=device_data['attributes'].get('friendly_name', ''),
                short_name=self.shorten_name(
                    device_data['attributes'].get('friendly_name', ''), 
                    area_name
                ),
                state=device_data['state'],
                domain=device_data['entity_id'].split('.')[0],
                attributes=device_data['attributes']
            )
            devices.append(asdict(device))
        
        return {
            'area_name': area_name,
            'devices': devices,
            'sensors': kws_data if kws_data else {},
            'stats': {
                'total': len(devices),
                'on': len([d for d in devices_data if d['state'] == 'on']),
                'off': len([d for d in devices_data if d['state'] == 'off'])
            },
            'timestamp': time.time()
        }
    
    def get_all_areas_for_api(self) -> Dict:
        """Получение всех пространств для API"""
        areas = self.load_areas_with_devices(force_refresh=True)
        result = {}
        
        for area_name, devices_data in areas.items():
            on_count = len([d for d in devices_data if d['state'] == 'on'])
            devices_list = []
            
            for device_data in devices_data[:4]:  # Берем первые 4 устройства для превью
                device = Device(
                    entity_id=device_data['entity_id'],
                    name=device_data['attributes'].get('friendly_name', ''),
                    short_name=self.shorten_name(
                        device_data['attributes'].get('friendly_name', ''),area_name
                    ),
                    state=device_data['state'],
                    domain=device_data['entity_id'].split('.')[0],
                    attributes=device_data['attributes']
                )
                devices_list.append(asdict(device))
            
            result[area_name] = {
                'device_count': len(devices_data),
                'on_count': on_count,
                'preview_devices': devices_list,
                'last_updated': time.time()
            }
        
        return result
    
    def get_device_state(self, entity_id: str) -> Optional[Dict]:
        """Получение состояния конкретного устройства для API"""
        result = self.home_assistant(f"states/{entity_id}")
        if result:
            return {
                'entity_id': entity_id,
                'state': result.get('state'),
                'attributes': result.get('attributes', {}),
                'last_changed': result.get('last_changed'),
                'last_updated': result.get('last_updated')
            }
        return None
    
    def control_device_api(self, entity_id: str, action: str) -> Dict:
        """Управление устройством через API"""
        try:
            success = self.control_device(entity_id, action)
            
            if success:
                # Даем время на обновление состояния
                time.sleep(0.3)
                updated_state = self.get_device_state(entity_id)
                
                return {
                    'success': True,
                    'entity_id': entity_id,
                    'action_performed': action,
                    'current_state': updated_state['state'] if updated_state else 'unknown',
                    'timestamp': time.time()
                }
            else:
                return {
                    'success': False,
                    'error': 'Не удалось выполнить действие',
                    'entity_id': entity_id,
                    'action': action,
                    'timestamp': time.time()
                }
                
        except Exception as e:
            logger.error(f"API control error: {e}")
            return {
                'success': False,
                'error': str(e),
                'entity_id': entity_id,
                'action': action,
                'timestamp': time.time()
            }
    
    def run_telegram_bot(self):
        """Запуск телеграм бота в отдельном потоке"""
        logger.info("Telegram бот запущен")
        
        error_count = 0
        
        while True:
            try:
                result = self.telegram(f"getUpdates?offset={self.last_id + 1}&timeout=30")
                
                if result and result.get("ok"):
                    updates = result["result"]
                    if updates:
                        logger.info(f"Telegram сообщений: {len(updates)}")
                    
                    for update in updates:
                        self.last_id = update["update_id"]
                        if "message" in update:
                            msg = update["message"]
                            if "text" in msg:
                                self.handle_message(msg["chat"]["id"], msg["text"])
                    
                    error_count = 0
                else:
                    error_count += 1
                    if error_count > 3:
                        logger.warning("Сброс кэша из-за ошибок...")
                        self._clear_cache()
                        error_count = 0
                
                time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("Telegram бот остановлен")
                break
            except Exception as e:logger.error(f"Ошибка Telegram бота: {e}")
            time.sleep(5)
    
    def _clear_cache(self):
        """Очистка всего кэша"""
        self.devices_cache = {}
        self._all_devices = None
        self._cache_timestamp = {}
        self.get_all_areas_list.cache_clear()
    
    def run(self):
        """Основной метод запуска (для обратной совместимости)"""
        logger.info("Оптимизированный бот управления домиками запущен")
        
        print("Проверка подключения к Home Assistant...")
        test_devices = self.get_all_devices()
        if test_devices:
            logger.info(f"Подключение успешно. Устройств: {len(test_devices)}")
        else:
            logger.error("Не удалось подключиться к Home Assistant")
        
        areas = self.load_areas_with_devices()
        house_count = len([a for a in areas if 'Домик' in a])
        logger.info(f"Домиков найдено: {house_count}")
        
        for area_name, devices in areas.items():
            logger.info(f"   {area_name}: {len(devices)} устройств")
        
        logger.info("Бот запущен...")
        
        self.run_telegram_bot()


if __name__ == "main":
    bot = OptimizedHomeBot()
    bot.run()
