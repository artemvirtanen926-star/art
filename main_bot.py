
import asyncio
import logging
import os
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
import threading
from dotenv import load_dotenv

# Веб-сервер для keep-alive
from aiohttp import web, web_response
import aiohttp

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, 
                          InlineKeyboardButton)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Токены и настройки
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8326095098:AAHVE8r5qaS8V2raYQgvi1Gz9dPEbUZ9ll8")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_API_TOKEN")

# НАСТРОЙКИ КАНАЛОВ ДЛЯ ПОДПИСКИ (ТВОИ КАНАЛЫ!)
REQUIRED_CHANNELS = [
    {
        "id": os.getenv("CHANNEL_1", "@kanal1kkal"), 
        "url": "https://t.me/kanal1kkal", 
        "name": "🏛️ Artemius AI",
        "description": "Главный канал искусственного интеллекта"
    },
    {
        "id": os.getenv("CHANNEL_2", "@kanal2kkal"), 
        "url": "https://t.me/kanal2kkal", 
        "name": "📢 AI Новости",
        "description": "Новости и обновления AI технологий"
    }
]

# Инициализация
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Состояния FSM
class BotStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_image_prompt = State()
    waiting_for_music_prompt = State()
    waiting_for_video_prompt = State()
    waiting_for_document = State()

# ЛИМИТЫ
FREE_LIMITS = {
    'chat': 3,
    'images': 1,
    'music': 1,
    'video': 1,
    'documents': 2
}

VIP_LIMITS = {
    'chat': 25,
    'images': 10,
    'music': 5,
    'video': 3,
    'documents': 8
}

# Глобальные переменные для статистики
subscription_cache = {}
user_stats = {}
user_limits = {}

# KEEP-ALIVE ВЕБ-СЕРВЕР
async def keep_alive_handler(request):
    """Эндпоинт для поддержания бота в рабочем состоянии"""
    uptime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return web_response.json_response({
        "status": "alive",
        "bot": "Artemius AI",
        "uptime": uptime,
        "message": "🏛️ Artemius AI работает на Replit!",
        "channels": [ch["id"] for ch in REQUIRED_CHANNELS]
    })

async def status_handler(request):
    """Статус бота"""
    total_users = len(user_stats)
    return web_response.json_response({
        "bot_name": "Artemius AI",
        "status": "running",
        "total_users": total_users,
        "channels": REQUIRED_CHANNELS
    })

# Создаем веб-приложение
web_app = web.Application()
web_app.router.add_get('/', keep_alive_handler)
web_app.router.add_get('/ping', keep_alive_handler)
web_app.router.add_get('/status', status_handler)
web_app.router.add_get('/health', keep_alive_handler)

# Функции для бота (аналогичны предыдущим)
async def check_subscription(user_id: int) -> bool:
    """Проверить подписку на ВСЕ каналы"""
    try:
        now = datetime.now()
        if user_id in subscription_cache:
            cached_time, is_subscribed = subscription_cache[user_id]
            if (now - cached_time).total_seconds() < 300:
                return is_subscribed

        all_subscribed = True
        for channel in REQUIRED_CHANNELS:
            try:
                member = await bot.get_chat_member(channel["id"], user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    all_subscribed = False
                    break
            except:
                all_subscribed = False
                break

        subscription_cache[user_id] = (now, all_subscribed)
        return all_subscribed

    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False

async def check_individual_subscriptions(user_id: int) -> Dict[str, bool]:
    """Проверить подписку на каждый канал отдельно"""
    try:
        subscriptions = {}
        for channel in REQUIRED_CHANNELS:
            try:
                member = await bot.get_chat_member(channel["id"], user_id)
                subscriptions[channel["id"]] = member.status in ['member', 'administrator', 'creator']
            except:
                subscriptions[channel["id"]] = False
        return subscriptions
    except:
        return {ch["id"]: False for ch in REQUIRED_CHANNELS}

def get_user_stats(user_id: int) -> dict:
    if user_id not in user_stats:
        user_stats[user_id] = {
            'total_messages': 0,
            'total_images': 0,
            'total_music': 0,
            'total_videos': 0,
            'total_documents': 0,
            'first_seen': datetime.now().isoformat()
        }
    return user_stats[user_id]

def get_daily_usage(user_id: int) -> dict:
    today = datetime.now().date().isoformat()
    if user_id not in user_limits:
        user_limits[user_id] = {}
    if today not in user_limits[user_id]:
        user_limits[user_id][today] = {
            'chat': 0,
            'images': 0,
            'music': 0,
            'video': 0,
            'documents': 0
        }
    return user_limits[user_id][today]

async def check_limit(user_id: int, feature: str) -> bool:
    is_vip = await check_subscription(user_id)
    daily = get_daily_usage(user_id)
    limits = VIP_LIMITS if is_vip else FREE_LIMITS
    return daily[feature] < limits[feature]

def use_feature(user_id: int, feature: str):
    daily = get_daily_usage(user_id)
    daily[feature] += 1
    stats = get_user_stats(user_id)
    stats[f'total_{feature}'] = stats.get(f'total_{feature}', 0) + 1

# Клавиатуры
async def get_main_menu(user_id: int):
    is_vip = await check_subscription(user_id)
    status_emoji = "⭐" if is_vip else "🔒"
    status_text = "VIP режим" if is_vip else "Базовый доступ"

    keyboard = [
        [KeyboardButton(text="💬 Чат с Artemius"), KeyboardButton(text="🎨 Создать картинку")],
        [KeyboardButton(text="🎵 Создать песню"), KeyboardButton(text="🎬 Создать видео")],
        [KeyboardButton(text="📄 Документ"), KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text=f"{status_emoji} {status_text}"), KeyboardButton(text="📢 Получить VIP")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

async def get_subscription_menu(user_id: int):
    individual_subs = await check_individual_subscriptions(user_id)
    keyboard = []

    for channel in REQUIRED_CHANNELS:
        is_subscribed = individual_subs.get(channel["id"], False)
        status_emoji = "✅" if is_subscribed else "📢"
        status_text = "Подписан" if is_subscribed else "Подписаться"

        keyboard.append([InlineKeyboardButton(
            text=f"{status_emoji} {channel['name']} • {status_text}", 
            url=channel["url"]
        )])

    keyboard.append([InlineKeyboardButton(text="➖ ➖ ➖ ➖ ➖", callback_data="separator")])
    keyboard.append([InlineKeyboardButton(text="🔄 Проверить подписки и получить VIP", callback_data="check_subscriptions")])
    keyboard.append([InlineKeyboardButton(text="⏭️ Продолжить с базовым доступом", callback_data="skip_subscriptions")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_menu():
    keyboard = [[KeyboardButton(text="🏠 Главное меню")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# Обработчики бота
@dp.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    is_vip = await check_subscription(user_id)

    if is_vip:
        welcome_text = f"""🏛️ **Добро пожаловать, VIP-пользователь!**

⭐ **VIP СТАТУС АКТИВЕН!** 
Спасибо за подписку на наши каналы!

🚀 **ВАШИ VIP-ЛИМИТЫ:**
💬 Диалоги — **{VIP_LIMITS['chat']} в день**
🎨 Картинки — **{VIP_LIMITS['images']} в день**  
🎵 Музыка — **{VIP_LIMITS['music']} в день**
🎬 Видео — **{VIP_LIMITS['video']} в день**
📄 Документы — **{VIP_LIMITS['documents']} в день**

🏛️ Работаем на Replit 24/7!"""

        reply_markup = await get_main_menu(user_id)
    else:
        welcome_text = f"""🏛️ **Добро пожаловать в Artemius AI!**

🤖 Я ваш персональный ИИ-помощник на Replit!

🔒 **ТЕКУЩИЕ ЛИМИТЫ (базовый):**
💬 Диалоги — **{FREE_LIMITS['chat']} в день**
🎨 Картинки — **{FREE_LIMITS['images']} в день**  
🎵 Музыка — **{FREE_LIMITS['music']} в день**
🎬 Видео — **{FREE_LIMITS['video']} в день**
📄 Документы — **{FREE_LIMITS['documents']} в день**

⭐ **ПОЛУЧИТЕ VIP СТАТУС:**
💬 **{VIP_LIMITS['chat']} диалогов** (+{VIP_LIMITS['chat'] - FREE_LIMITS['chat']})
🎨 **{VIP_LIMITS['images']} картинок** (+{VIP_LIMITS['images'] - FREE_LIMITS['images']})
🎵 **{VIP_LIMITS['music']} композиций** (+{VIP_LIMITS['music'] - FREE_LIMITS['music']})
🎬 **{VIP_LIMITS['video']} видео** (+{VIP_LIMITS['video'] - FREE_LIMITS['video']})
📄 **{VIP_LIMITS['documents']} документов** (+{VIP_LIMITS['documents'] - FREE_LIMITS['documents']})

📢 **Подпишитесь на оба канала!**"""

        reply_markup = await get_subscription_menu(user_id)

    await message.answer(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def show_limit_exhausted(message: types.Message, feature: str):
    user_id = message.from_user.id
    is_vip = await check_subscription(user_id)

    feature_names = {
        'chat': 'диалогов',
        'images': 'генераций изображений',
        'music': 'создания музыки', 
        'video': 'видеопроизводства',
        'documents': 'анализа документов'
    }

    if is_vip:
        text = f"🚫 **Вы исчерпали лимит {feature_names[feature]}!**\n\n⏰ Лимиты обновятся завтра в 00:00 МСК"
        await message.answer(text, parse_mode="Markdown")
    else:
        text = f"""🚫 **Вы исчерпали лимит {feature_names[feature]}!**

⭐ **Чтобы получить VIP статус, подпишитесь на каналы!**

🚀 **ВСЕ VIP БОНУСЫ:**
💬 {VIP_LIMITS['chat']} диалогов (+{VIP_LIMITS['chat'] - FREE_LIMITS['chat']})
🎨 {VIP_LIMITS['images']} картинок (+{VIP_LIMITS['images'] - FREE_LIMITS['images']})
🎵 {VIP_LIMITS['music']} композиций (+{VIP_LIMITS['music'] - FREE_LIMITS['music']})
🎬 {VIP_LIMITS['video']} видео (+{VIP_LIMITS['video'] - FREE_LIMITS['video']})
📄 {VIP_LIMITS['documents']} документов (+{VIP_LIMITS['documents'] - FREE_LIMITS['documents']})"""

        await message.answer(text, reply_markup=await get_subscription_menu(user_id), parse_mode="Markdown")

# Обработчики функций
@dp.message(F.text == "💬 Чат с Artemius")
async def chat_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await check_limit(user_id, 'chat'):
        await show_limit_exhausted(message, 'chat')
        return

    await state.set_state(BotStates.waiting_for_text)
    await message.answer("🏛️ **Artemius готов к диалогу!**\n\nЗадавайте вопросы!", reply_markup=get_back_menu(), parse_mode="Markdown")

@dp.message(F.text == "🎨 Создать картинку")
async def image_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await check_limit(user_id, 'images'):
        await show_limit_exhausted(message, 'images')
        return

    await state.set_state(BotStates.waiting_for_image_prompt)
    await message.answer("🎨 **Artemius Art Studio!**\n\nОпишите что создать:", reply_markup=get_back_menu(), parse_mode="Markdown")

@dp.message(F.text == "🎵 Создать песню")
async def music_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await check_limit(user_id, 'music'):
        await show_limit_exhausted(message, 'music')
        return

    await state.set_state(BotStates.waiting_for_music_prompt)
    await message.answer("🎵 **Artemius Music!**\n\nОпишите какую музыку создать:", reply_markup=get_back_menu(), parse_mode="Markdown")

@dp.message(F.text == "🎬 Создать видео")
async def video_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await check_limit(user_id, 'video'):
        await show_limit_exhausted(message, 'video')
        return

    await state.set_state(BotStates.waiting_for_video_prompt)
    await message.answer("🎬 **Artemius Video!**\n\nОпишите какое видео создать:", reply_markup=get_back_menu(), parse_mode="Markdown")

@dp.message(F.text == "📄 Документ")
async def document_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await check_limit(user_id, 'documents'):
        await show_limit_exhausted(message, 'documents')
        return

    await state.set_state(BotStates.waiting_for_document)
    await message.answer("📄 **Artemius OCR!**\n\nОтправьте фото документа:", reply_markup=get_back_menu(), parse_mode="Markdown")

@dp.message(F.text == "👤 Мой профиль")
async def profile_handler(message: types.Message):
    user_id = message.from_user.id
    is_vip = await check_subscription(user_id)
    daily = get_daily_usage(user_id)
    limits = VIP_LIMITS if is_vip else FREE_LIMITS

    status = "⭐ VIP АКТИВЕН!" if is_vip else "🔒 Базовый доступ"

    profile_text = f"""👤 **Профиль Artemius AI**

{status}

📊 **Использовано сегодня:**
💬 Диалоги: {daily['chat']}/{limits['chat']}
🎨 Изображения: {daily['images']}/{limits['images']}  
🎵 Музыка: {daily['music']}/{limits['music']}
🎬 Видео: {daily['video']}/{limits['video']}
📄 Документы: {daily['documents']}/{limits['documents']}

🏛️ Работает на Replit 24/7!"""

    reply_markup = None if is_vip else await get_subscription_menu(user_id)
    await message.answer(profile_text, reply_markup=reply_markup, parse_mode="Markdown")

@dp.message(F.text.in_(["⭐ VIP режим", "🔒 Базовый доступ", "📢 Получить VIP"]))
async def subscription_info_handler(message: types.Message):
    user_id = message.from_user.id
    is_vip = await check_subscription(user_id)

    if is_vip:
        await message.answer("⭐ **VIP СТАТУС АКТИВЕН!**\n\nСпасибо за подписку!", parse_mode="Markdown")
    else:
        await message.answer("⭐ **Получите VIP статус!**\n\nПодпишитесь на каналы:", reply_markup=await get_subscription_menu(user_id), parse_mode="Markdown")

# Коллбеки
@dp.callback_query(F.data == "check_subscriptions")
async def check_subscriptions_callback(callback: types.CallbackQuery):
    await callback.answer("Проверяю...")
    user_id = callback.from_user.id

    if user_id in subscription_cache:
        del subscription_cache[user_id]

    is_vip = await check_subscription(user_id)

    if is_vip:
        await callback.message.answer("✅ **VIP СТАТУС АКТИВИРОВАН!**", reply_markup=await get_main_menu(user_id), parse_mode="Markdown")
    else:
        await callback.message.answer("❌ **Подписка не найдена**\n\nПодпишитесь на ОБА канала!", reply_markup=await get_subscription_menu(user_id), parse_mode="Markdown")

@dp.callback_query(F.data == "skip_subscriptions")
async def skip_subscriptions_callback(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    await callback.message.answer("🔒 **Базовый режим активен**", reply_markup=await get_main_menu(user_id))

@dp.callback_query(F.data == "separator")
async def separator_callback(callback: types.CallbackQuery):
    await callback.answer()

@dp.message(F.text == "🏠 Главное меню")
async def main_menu_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await message.answer("🏛️ **Главное меню**", reply_markup=await get_main_menu(user_id))

# AI функции (заглушки)
@dp.message(StateFilter(BotStates.waiting_for_text))
async def process_chat_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    use_feature(user_id, 'chat')
    await message.answer(f"🏛️ **Artemius:** Получил запрос \"{message.text}\"\n\nВ полной версии здесь умный ответ!", parse_mode="Markdown")

@dp.message(StateFilter(BotStates.waiting_for_image_prompt))
async def process_image_generation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    use_feature(user_id, 'images')
    await message.answer(f"🎨 **Создаю:** {message.text}\n\nВ полной версии здесь изображение!", parse_mode="Markdown")

@dp.message(StateFilter(BotStates.waiting_for_music_prompt))
async def process_music_generation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    use_feature(user_id, 'music')
    await message.answer(f"🎵 **Создаю музыку:** {message.text}\n\nВ полной версии здесь композиция!", parse_mode="Markdown")

@dp.message(StateFilter(BotStates.waiting_for_video_prompt))
async def process_video_generation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    use_feature(user_id, 'video')
    await message.answer(f"🎬 **Создаю видео:** {message.text}\n\nВ полной версии здесь HD видео!", parse_mode="Markdown")

@dp.message(StateFilter(BotStates.waiting_for_document), F.photo)
async def process_document_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    use_feature(user_id, 'documents')
    await message.answer("📄 **Анализирую...**\n\nВ полной версии распознаю весь текст!", parse_mode="Markdown")

@dp.message()
async def handle_unknown_message(message: types.Message):
    user_id = message.from_user.id
    await message.answer("🤔 **Не понял команду**", reply_markup=await get_main_menu(user_id))

# MAIN ФУНКЦИЯ ДЛЯ REPLIT
async def start_web_server():
    """Запуск веб-сервера для keep-alive"""
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("🌐 Веб-сервер запущен на порту 8080")

async def main():
    """Главная функция для запуска на Replit"""
    try:
        # Запуск веб-сервера в фоне
        asyncio.create_task(start_web_server())

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🏛️ ARTEMIUS AI - ЗАПУЩЕН НА REPLIT!")
        logger.info(f"📢 Каналы: {[ch['id'] for ch in REQUIRED_CHANNELS]}")
        logger.info("🌐 Keep-alive сервер активен!")

        print("🏛️ ===== ARTEMIUS AI - REPLIT VERSION =====")
        print(f"📢 VIP каналы: @kanal1kkal и @kanal2kkal")  
        print("🆓 БЕСПЛАТНЫЙ ХОСТИНГ С KEEP-ALIVE!")
        print("🌐 Доступен по адресу: https://your-repl-name.your-username.repl.co")

        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Artemius AI остановлен")
