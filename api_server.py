from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from functools import wraps
from typing import Dict
import logging
import time
from bot_core import OptimizedHomeBot
from config import SECRET_KEY, ALLOWED_ORIGINS, MINI_APP_PORT, DEBUG

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=ALLOWED_ORIGINS)

# Инициализация бота
bot = OptimizedHomeBot()

# Простая аутентификация для API
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        # Для разработки можно отключить аутентификацию
        if DEBUG:
            return f(*args, **kwargs)
            
        if not auth_header:
            return jsonify({'error': 'Требуется авторизация'}), 401
            
        # Проверка токена (простая проверка)
        expected_token = f"Bearer {SECRET_KEY}"
        if auth_header != expected_token:
            return jsonify({'error': 'Неверный токен'}), 403
            
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    """Главная страница мини-приложения"""
    return send_file('miniapp.html')

@app.route('/health')
def health():
    """Проверка здоровья сервера"""
    return jsonify({
        'status': 'ok',
        'timestamp': time.time(),
        'service': 'home-assistant-miniapp'
    })

@app.route('/api/areas', methods=['GET'])
@require_auth
def get_areas():
    """Получение списка всех пространств"""
    try:
        areas = bot.get_all_areas_for_api()
        return jsonify({
            'success': True,
            'data': areas,
            'timestamp': time.time()
        })
    except Exception as e:
        logger.error(f"Error in get_areas: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/area/<path:area_name>', methods=['GET'])
@require_auth
def get_area(area_name):
    """Получение детальной информации о пространстве"""
    try:
        area_data = bot.get_area_data_for_api(area_name)
        return jsonify({
            'success': True,
            'data': area_data,
            'timestamp': time.time()
        })
    except Exception as e:
        logger.error(f"Error in get_area: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/device/control', methods=['POST'])
@require_auth
def control_device():
    """Управление устройством"""
    try:
        data = request.json
        if not data or 'entity_id' not in data or 'action' not in data:
            return jsonify({
                'success': False,
                'error': 'Неверные данные'
            }), 400
        
        entity_id = data['entity_id']
        action = data['action']
        
        if action not in ['on', 'off', 'toggle']:
            return jsonify({
                'success': False,
                'error': 'Неверное действие'
            }), 400
        
        # Если toggle, определяем текущее состояние
        if action == 'toggle':
            # Нужно получить текущее состояние устройства
            current_state = bot.get_device_state(entity_id)
            action = 'off' if current_state == 'on' else 'on'
        
        success = bot.control_device(entity_id, action)
        
        if success:
            # Очищаем кэш после успешного управления
            bot._clear_cache()
            
            # Получаем обновленное состояние
            time.sleep(0.3)  # Даем Home Assistant время на обновление
            updated_state = bot.get_device_state(entity_id)
            
            return jsonify({
                'success': True,
                'entity_id': entity_id,
                'action_performed': action,
                'current_state': updated_state,
                'timestamp': time.time()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Не удалось выполнить действие'
            }), 500
            
    except Exception as e:
        logger.error(f"Error in control_device: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/device/<entity_id>', methods=['GET'])
@require_auth
def get_device(entity_id):
    """Получение информации об устройстве"""
    try:
        device_data = bot.get_device_state(entity_id)
        return jsonify({
            'success': True,
            'data': device_data,
            'timestamp': time.time()
        })
    except Exception as e:
        logger.error(f"Error in get_device: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/batch/devices', methods=['POST'])
@require_auth
def batch_get_devices():
    """Получение состояния нескольких устройств за один запрос"""
    try:
        data = request.json
        if not data or 'entity_ids' not in data:
            return jsonify({
                'success': False,
                'error': 'Неверные данные'
            }), 400
        
        entity_ids = data['entity_ids']
        if not isinstance(entity_ids, list) or len(entity_ids) > 20:
            return jsonify({
                'success': False,
                'error': 'Слишком много устройств (максимум 20)'
            }), 400
        
        results = {}
        for entity_id in entity_ids:
            try:
                device_data = bot.get_device_state(entity_id)
                results[entity_id] = device_data
            except Exception as e:
                results[entity_id] = {'error': str(e)}
        
        return jsonify({
            'success': True,
            'data': results,
            'timestamp': time.time()
        })
    except Exception as e:
        logger.error(f"Error in batch_get_devices: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/refresh', methods=['POST'])
@require_auth
def refresh_cache():
    """Принудительное обновление кэша"""
    try:
        bot._clear_cache()
        return jsonify({
            'success': True,
            'message': 'Кэш очищен',
            'timestamp': time.time()
        })
    except Exception as e:
        logger.error(f"Error in refresh_cache: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Добавляем недостающий метод в класс бота
def get_device_state(self, entity_id: str) -> Dict:
    """Получение состояния конкретного устройства"""
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

# Добавляем метод к классу бота
OptimizedHomeBot.get_device_state = get_device_state

def run_api_server():
    """Запуск API сервера"""
    logger.info(f"Запуск API сервера на порту {MINI_APP_PORT}")
    app.run(host='0.0.0.0', port=MINI_APP_PORT, debug=DEBUG, threaded=True)

if __name__ == '__main__':
    run_api_server()
