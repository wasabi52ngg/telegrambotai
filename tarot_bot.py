import logging
import requests
import re
import json
import os
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext, ConversationHandler
from help_handler import help_command
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import tiktoken
import speech_recognition as sr
from telegram import File
import io
from pydub import AudioSegment

# Загрузка переменных из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка токенов
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
PROXY_API_KEY = os.getenv('PROXY_API_KEY')
PROXY_API_URL = os.getenv('PROXY_API_URL')
USER_DATA_FILE = 'user_data.json'
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
CHAT_HISTORY_FILE = 'user_chat_history.json'
CHANNEL_IDS = os.getenv('CHANNEL_IDS').split(',')

# Настройки модели
MODEL_NAME = os.getenv('MODEL_NAME')
MAX_TOKENS = int(os.getenv('MAX_TOKENS'))
TEMPERATURE = float(os.getenv('TEMPERATURE'))

# Функция для загрузки данных из JSON-файла
def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as file:
            return [json.loads(line) for line in file]
    return []

# Функция для сохранения всех данных в JSON-файл
def save_user_data(user_data):
    with open(USER_DATA_FILE, 'w', encoding='utf-8') as file:
        for user in user_data:
            json.dump(user, file, ensure_ascii=False)
            file.write('\n')


# Функция для отправки уведомлений администратору
async def notify_admin(context: CallbackContext, message: str):
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)

def load_chat_history(user_id, limit=10):
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as file:
            chat_history = json.load(file)
        return chat_history.get(str(user_id), [])[-limit:]
    return []

# Функция для добавления нового пользователя или обновления данных существующего
def add_or_update_user(user_data, user_id, username, context: CallbackContext, tokens_used=0, date_of_birth=None,
                       time_of_birth=None, place_of_birth=None):
    user_found = False
    for user in user_data:
        if isinstance(user, dict) and user['user_id'] == user_id:
            user['username'] = username
            user['last_active'] = datetime.now().strftime('%d-%m-%Y')
            user['tokens_used'] = user.get('tokens_used', 0) + tokens_used
            if date_of_birth:
                user['date_of_birth'] = date_of_birth
            if time_of_birth:
                user['time_of_birth'] = time_of_birth
            if place_of_birth:
                user['place_of_birth'] = place_of_birth
            if 'subscribe' not in user:
                user['subscribe'] = True
            user_found = True
            break

    if not user_found:
        new_user = {
            'user_id': user_id,
            'username': username,
            'registration_date': datetime.now().strftime('%d-%m-%Y'),  # Дата регистрации
            'last_active': datetime.now().strftime('%d-%m-%Y'),
            'tokens_used': tokens_used,
            'date_of_birth': date_of_birth,
            'time_of_birth': time_of_birth,
            'place_of_birth': place_of_birth,
            'subscribe': True
        }
        user_data.append(new_user)
        context.application.create_task(notify_admin(context, f"Новый пользователь: {username} (ID: {user_id})"))

    save_user_data(user_data) # Сохраняем все данные после любого изменения


async def unsubscribe(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id  # Оставляем user_id в виде числа
    user_data = load_user_data()

    user_found = False
    for user in user_data:
        if user['user_id'] == user_id:
            user['subscribe'] = False
            user_found = True
            break

    if user_found:
        save_user_data(user_data)
        await update.message.reply_text("Вы успешно отписались от ежедневной рассылки гороскопов.")
    else:
        await update.message.reply_text("Пользователь не найден.")

# Функция для сохранения истории чатов в отдельный файл
def save_chat_history(user_id, message, role):
    chat_history = {}
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as file:
            chat_history = json.load(file)

    if str(user_id) not in chat_history:
        chat_history[str(user_id)] = []

    chat_history[str(user_id)].append({
        'timestamp': datetime.now().strftime('%d-%m-%Y %H:%M:%S'),
        'message': message,
        'role': role
    })

    with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as file:
        json.dump(chat_history, file, ensure_ascii=False, indent=4)


# Функция для подсчета токенов
def count_tokens(text):
    encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    tokens = encoding.encode(text)
    return len(tokens)

# Обертка для передачи контекста
async def send_daily_horoscopes(context: CallbackContext):
    user_data = load_user_data()
    today_date = datetime.now().strftime('%Y-%m-%d')  # Получаем сегодняшнюю дату в формате ГГГГ-ММ-ДД
    for user in user_data:
        if user.get('subscribe') and all(user.get(k) for k in ('date_of_birth', 'time_of_birth', 'place_of_birth')):
            user_id = user['user_id']
            date_of_birth = user['date_of_birth']
            time_of_birth = user['time_of_birth']
            place_of_birth = user['place_of_birth']
            prompt = f"Представь, что ты астролог. Моя дата рождения {date_of_birth}, время рождения {time_of_birth}, место рождения {place_of_birth}. Дай мне астрологический прогноз на {today_date}. В ответе давай меньше теории и воды, дай только выжимку самой важной интерпретации прогноза - для каких дел день благоприятный, чего стоит опасаться, какие есть рекомендации."
            try:
                response = send_openai_request(prompt)
                await context.bot.send_message(chat_id=user_id, text=response)
            except Exception as e:
                logger.error(f"Error generating astrology forecast for user {user_id}: {e}")

# Функция проверки подписки
async def check_subscription(user_id: int, bot_token: str, channel_id: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/getChatMember?chat_id={channel_id}&user_id={user_id}"
    logger.info(f"Проверка подписки пользователя {user_id} на канал {channel_id}")
    response = requests.get(url)
    logger.info(f"Ответ от API: {response.text}")
    result = response.json()
    status = result.get("result", {}).get("status", "")
    logger.info(f"Статус подписки: {status}")
    return status in ["member", "administrator", "creator"]

# Функция проверки подписки на один из нескольких каналов
async def check_subscription_multiple(user_id: int, bot_token: str, channel_ids: list) -> bool:
    for channel_id in channel_ids:
        if await check_subscription(user_id, bot_token, channel_id):
            return True
    return False

# Функции для обработки команд
async def start(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    is_subscribed = await check_subscription_multiple(user_id, TELEGRAM_TOKEN, CHANNEL_IDS)
    if not is_subscribed:
        await update.message.reply_text(
            "Данные бот работает для вас абсолютно бесплатно. Пожалуйста, подпишитесь на один из предложенных каналов, который может быть вам интересен и продолжите использование бота.\n\n"
            "Ссылки на каналы:\n"
            "1. [Канал про психологию](https://t.me/psikholog_onlajn_besplatno_chat)\n"
            "2. [Психолог, работа с тревогой](https://t.me/juliakoyash)\n",
            parse_mode='Markdown'
        )
        return
    user_data = load_user_data()
    add_or_update_user(user_data, user_id, username, context)
    keyboard = [
        [InlineKeyboardButton("🧠 Психолог", callback_data="psychologist")],
        [InlineKeyboardButton("💼 Карьерный консультант", callback_data="career_consultant")],
        [InlineKeyboardButton("🔮 Астролог", callback_data="astrology")],
        [InlineKeyboardButton("🔢 Нумеролог", callback_data="numerology")],
        [InlineKeyboardButton("🚀 Коуч по саморазвитию", callback_data="self_development_coach")],
        [InlineKeyboardButton("🃏 Таро", callback_data="tarot")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🌟 Приветствую! Я твой личный гид по поиску лучших решений, балансу, крепкой веры в себя и доверию миру."
    "Я являюсь искусственным интеллектом и могу выступать в роли психолога, коуча, карьерного консультанта, астролога, нумеролога и таролога."
    "Выбери одну из ролей ниже, чтобы начать:\n\n"
    "🧠 Психолог: Психолог поможет в работе с тревогой и депрессивными мыслями, построением гармоничных отношений.\n"
    "🚀 Коуч по саморазвитию: Достижение целей и личностный рост вместе со мной.\n"
    "📈 Карьерный консультант: Советы по карьерному развитию и профессиональному росту.\n"
    "🃏 ТАРО: Иногда на ситуацию надо посмотреть с неожиданной стороны.\n"
    "Просто нажми на одну из кнопок ниже, чтобы начать свое увлекательное путешествие!\n\n"
    "Ознакомиться с инструкцией и возможностями бота можно здесь /help",
        reply_markup=reply_markup
    )

async def button_click(update: Update, context: CallbackContext, choice=None) -> None:
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        choice = query.data
    else:
        query = update.message
        choice = choice or query.text[1:]

    if choice == "tarot":
        context.user_data['role'] = 'tarot'
        await query.edit_message_text(text="✨ Добро пожаловать в мир ТАРО! ✨\n\n"
       "🃏 Карты Таро могут помочь вам раскрыть скрытые аспекты вашей жизни, получить ценные советы и посмотреть на ситуацию с новой стороны.\n\n"
       "1. Задайте любой вопрос, который у вас на сердце — это может быть вопрос о любви, карьере, здоровье или будущем.\n"
       "2. Постарайтесь быть конкретным в своём вопросе, чтобы карты могли дать вам наиболее точный ответ.\n\n"
       "🔮 Примеры вопросов:\n"
       "- Какие шаги мне следует предпринять для карьерного роста?\n"
       "- Какое решение будет наилучшим в текущей ситуации?\n"
       "- Как бы я хотела выстроить эти отношения?\n\n"
       "Не стесняйтесь, задайте свой вопрос, и пусть карты ТАРО откроют вам свою мудрость!")
    elif choice == "astrology":
        context.user_data['role'] = 'astrology'
        if 'date_of_birth' in context.user_data:
            if 'time_of_birth' in context.user_data:
                if 'place_of_birth' in context.user_data:
                    await query.edit_message_text(text="Все данные уже введены. Введите ваш вопрос для астролога:")
                else:
                    await query.edit_message_text(text="Введите место рождения:")
            else:
                await query.edit_message_text(text="Введите время рождения (в формате ЧЧ:ММ):")
        else:
            await query.edit_message_text(text="Вы выбрали роль астролога. Введите вашу дату рождения (в формате ДД.ММ.ГГГГ):")
    elif choice == "numerology":
        context.user_data['role'] = 'numerology'
        if 'date_of_birth' in context.user_data:
            await query.edit_message_text(text="Дата рождения уже введена. Введите ваш вопрос для нумеролога:")
        else:
            await query.edit_message_text(
                text="Вы выбрали роль нумеролога. Введите свою дату рождения (в формате ДД.ММ.ГГГГ):")
    elif choice == "self_development_coach":
        context.user_data['role'] = 'self_development_coach'
        prompt = "Представь, что ты коуч по саморазвитию, а я у тебя на приеме. Я впервые на приеме у коуча по саморазвитию, поэтому возьми инициативу по диалогу в свои руки. Разговор должен быть интерактивным, вовлекающим"
        try:
            response = send_openai_request(prompt)
            await query.edit_message_text(response)
        except Exception as e:
            logger.error(f"Error generating self-development coach response: {e}")
            await query.edit_message_text("Произошла ошибка при получении ответа. Попробуйте еще раз позже.")
    elif choice == "fun_tarot":
        context.user_data['role'] = 'fun_tarot'
        await query.edit_message_text(text="Вы выбрали роль Нескучного Таро. Задайте свой вопрос.")
    elif choice == "psychologist":
        context.user_data['role'] = 'psychologist'
        keyboard = [
            [InlineKeyboardButton("Когнитивно-поведенческая", callback_data="cbt"), InlineKeyboardButton("Психодинамическая", callback_data="psychodynamic")],
            [InlineKeyboardButton("Гештальт-терапия", callback_data="gestalt"), InlineKeyboardButton("Не разбираюсь", callback_data="unsure")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Вы выбрали роль психолога. Выберите методику терапии или нажмите 'не разбираюсь':", reply_markup=reply_markup)
    elif choice == "career_consultant":
        context.user_data['role'] = 'career_consultant'
        await query.edit_message_text(text="💼 Добро пожаловать к Карьерному консультанту!\n\n"
                                           "Я могу помочь вам с профессиональными советами, планированием карьеры и достижением ваших карьерных целей.\n\n"
                                           "❓ **Как это работает:**\n"
                                           "1. Опишите вашу текущую профессиональную ситуацию или задайте конкретный вопрос о карьере.\n"
                                           "2. Я дам вам рекомендации и советы, чтобы помочь вам продвинуться в вашей карьере.\n\n"
                                           "🔮 **Примеры вопросов:**\n"
                                           "- Как мне улучшить свои навыки для повышения?\n"
                                           "- Как подготовиться к собеседованию на новую работу?\n"
                                           "- Как достичь баланса между работой и личной жизнью?\n\n"
                                           "Не стесняйтесь, задайте свой вопрос, и я помогу вам найти наилучшее решение!")

async def handle_date_of_birth(update: Update, context: CallbackContext) -> bool:
    date_of_birth = update.message.text
    if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', date_of_birth):
        await update.message.reply_text("Неправильный формат даты. Введите дату рождения в формате ДД.ММ.ГГГГ:")
        return False
    context.user_data['date_of_birth'] = date_of_birth
    return True
    await update.message.reply_text("Введите время вашего рождения (в формате ЧЧ:ММ):")

async def handle_time_of_birth(update: Update, context: CallbackContext) -> bool:
    time_of_birth = update.message.text
    if not re.match(r'^\d{2}:\d{2}$', time_of_birth):
        await update.message.reply_text("Неправильный формат времени. Введите время рождения в формате ЧЧ:ММ:")
        return False
    context.user_data['time_of_birth'] = time_of_birth
    return True
    await update.message.reply_text("Введите место вашего рождения (город или населенный пункт):")

async def handle_place_of_birth(update: Update, context: CallbackContext) -> None:
    place_of_birth = update.message.text
    context.user_data['place_of_birth'] = place_of_birth

    date_of_birth = context.user_data['date_of_birth']
    time_of_birth = context.user_data['time_of_birth']
    place_of_birth = context.user_data['place_of_birth']

    # Сохранение данных пользователя после обновления
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    user_data = load_user_data()
    add_or_update_user(user_data, user_id, username, context, date_of_birth=context.user_data['date_of_birth'],
                       time_of_birth=context.user_data['time_of_birth'],
                       place_of_birth=place_of_birth)  # Обновление всех данных о рождении

    prompt = f"Представь, что ты астролог. Моя дата рождения {date_of_birth}, время рождения {time_of_birth}, место рождения {place_of_birth}. Дай мне ответы на мои вопросы на основе моей натальной карты. Общайся так, чтобы казалось, что человек на реальном приеме у профессионального астролога. В ответах давай меньше воды и больше полезной информации и интерпретаций. Не говори о том, что ты не можешь рассчитать что-то и тем более не нужно рекомендовать посетить какие-то сайты."

    try:
        response = send_openai_request(prompt)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error generating astrology forecast for {date_of_birth}, {time_of_birth}, {place_of_birth}: {e}")
        await update.message.reply_text("Произошла ошибка при получении прогноза. Попробуйте еще раз позже.")

async def handle_psychologist_choice(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    method = query.data
    context.user_data['psychology_method'] = method

    # Проверка наличия update.message или использование update.callback_query.message
    if update.message:
        user_id = update.message.from_user.id
        username = update.message.from_user.username
        message_text = update.message.text
    elif update.callback_query.message:
        user_id = update.callback_query.message.chat.id
        username = update.callback_query.message.chat.username
        message_text = update.callback_query.message.text
    else:
        logger.error("No message found in update or callback_query")
        await query.edit_message_text(text="Произошла ошибка. Попробуйте еще раз.")
        return

    user_data = load_user_data()
    tokens_used = count_tokens(message_text)
    add_or_update_user(user_data, user_id, username, context, tokens_used)

    # Загрузка истории чатов
    chat_history = load_chat_history(user_id, limit=10)

    # Сохранение текущего сообщения в историю чатов
    save_chat_history(user_id, message_text, 'user')

    # Формирование текста истории чатов
    chat_history_text = "\n".join([f"{entry['role']}: {entry['message']}" for entry in chat_history])

    method_text = {
        "cbt": "когнитивно-поведенческая",
        "psychodynamic": "психодинамическая",
        "gestalt": "гештальт-терапия",
        "unsure": "которая будет эффективна в моем случае"
    }.get(method, "не разбираюсь")
    if chat_history:
        prompt = f"Ты - психолог, использующий методику {method_text}. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
    else:
        prompt = f"Представь, что ты психолог, использующий методику {method_text}.Возьми на себя инициативу по диалогу.Держи ответы неформальными, но точными. Используй технические термины и концепции свободно — считай, что собеседник в теме. Будь прямым. Избавься от вежливых формальностей и лишней вежливости.Приводи примеры только когда уместно.Подстраивай глубину и длину ответов под контекст. Сначала точность, но без лишней воды. Короткие, четкие фразы — нормально.Дай своей личности проявиться, но не затми суть.Не старайся быть «супер-помощником» в каждом предложении."

    try:
        response = send_openai_request(prompt)
        await query.edit_message_text(response)
    except Exception as e:
        logger.error(f"Error generating psychology response for method {method_text}: {e}")
        await query.edit_message_text("Произошла ошибка при получении ответа. Попробуйте еще раз позже.")

# Обработчик сообщений пользователя
async def handle_message(update: Update, context: CallbackContext, recognized_text: str = None) -> None:
    # Если recognized_text передан, используем его, иначе берем текст из update.message.text
    message_text = recognized_text if recognized_text is not None else update.message.text

    user_id = update.message.from_user.id
    username = update.message.from_user.username
    user_data = load_user_data()
    tokens_used = count_tokens(message_text)
    add_or_update_user(user_data, user_id, username, context, tokens_used)

    # Загрузка истории чатов
    chat_history = load_chat_history(user_id, limit=10)

    if 'role' in context.user_data:
        role = context.user_data['role']
    else:
        role = 'default'

    # Сохранение текущего сообщения в историю чатов
    save_chat_history(user_id, message_text, role)

    # Формирование текста истории чатов
    chat_history_text = "\n".join([f"{entry['role']}: {entry['message']}" for entry in chat_history])

    # Проверка на роль пользователя и создание промпта для OpenAI с учетом истории
    if role == 'tarot':
        if chat_history:
            prompt = f"Ты - гадалка на картах ТАРО. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты гадалка на картах ТАРО. Выложи 3 карты и дай предсказание на вопрос: {message_text}. Давай меньше воды, теории и больше интерпретации. Рассказывай так, чтобы читателю было интересно и создавалось впечатление, что человек на реальном приеме у гадалки"
        waiting_message = await update.message.reply_text("🔮Достаю карты...🔮", disable_notification=True)
    elif role == 'astrology':
        if 'date_of_birth' not in context.user_data:
            if not await handle_date_of_birth(update, context):
                return
            await update.message.reply_text("Введите время рождения (в формате ЧЧ:ММ):")
            return
        if 'time_of_birth' not in context.user_data:
            if not await handle_time_of_birth(update, context):
                return
            await update.message.reply_text("Введите место рождения:")
            return
        if 'place_of_birth' not in context.user_data:
            await handle_place_of_birth(update, context)
            return
        if chat_history:
            prompt = f"Ты - астролог. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            if 'date_of_birth' not in context.user_data:
                if not await handle_date_of_birth(update, context):
                    return
            prompt = f"Представь, что ты астролог. Моя дата рождения {context.user_data['date_of_birth']}, время рождения {context.user_data['time_of_birth']}, место рождения {context.user_data['place_of_birth']}. Дай мне ответ как астролог на основе моей натальной карты на мой вопрос: {message_text}. Общайся так, чтобы казалось, что человек на реальном приеме у профессионального астролога. В ответах давай меньше воды и больше полезной информации и интерпретаций. Не говори о том, что ты не можешь рассчитать что-то и тем более не нужно рекомендовать посетить какие-то сайты."
        waiting_message = await update.message.reply_text("🌘Составляю карту планет...🌘", disable_notification=True)
    elif role == 'numerology':
        if 'date_of_birth' not in context.user_data:
            if not await handle_date_of_birth(update, context):
                return
        else:
            await update.message.reply_text("Введите ваш вопрос для нумеролога:")
        if chat_history:
            prompt = f"Ты - Нумеролог. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты нумеролог. Я пришел к тебе на прием. Моя дата рождения {context.user_data['date_of_birth']}. Я впервые у нумеролога, поэтому возьми инициативу по диалогу на себя. Дай прогноз на мой вопрос: {message_text}. Или предложи мне несколько популярных вопросов, с которых мы можем начать.В ответах давай меньше воды и вступительных слов, а больше полезной информации и интерпритаций."
        waiting_message = await update.message.reply_text("🔢Считаю цифры...🔢", disable_notification=True)
    elif role == 'self_development_coach':
        if chat_history:
            prompt = f"Ты - Коуч по саморазвитию. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты коуч по саморазвитию. Ответь на вопрос: {message_text}."
        waiting_message = await update.message.reply_text("💪Составляю ответ...💪", disable_notification=True)
    elif role == 'fun_tarot':
        if chat_history:
            prompt = f"Ты - невоспитанная хабалистая гадалка на картах ТАРО. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты гадалка на картах ТАРО. Выложи карты и дай предсказание на вопрос: {message_text}. Не пытайся быть осторожной и интеллигентной в ответах, используй элементы юмора, непристойности, пикантности или интимные подробности, словно ты хабалка."
        waiting_message = await update.message.reply_text("🔮Достаю карты...🔮", disable_notification=True)
    elif role == 'psychologist':
        method = context.user_data.get('psychology_method')
        if not method:
            await update.message.reply_text(
                "Пожалуйста, выберите методику, нажав /start и выбрав роль психолога снова.")
            return
        method_text = {
            "cbt": "когнитивно-поведенческая",
            "psychodynamic": "психодинамическая",
            "gestalt": "гештальт-терапия",
            "unsure": "которая будет эффективна в моем случае"
        }.get(method, "которая будет эффективна в моем случае")
        if chat_history:
            prompt = f"Ты - Психолог, работающий по методике {method_text}. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты психолог, использующий методику {method_text}. Ответь на вопрос: {message_text}. Держи ответы неформальными, но точными. Используй технические термины и концепции свободно — считай, что собеседник в теме. Будь прямым. Избавься от вежливых формальностей и лишней вежливости.Приводи примеры только когда уместно.Подстраивай глубину и длину ответов под контекст. Сначала точность, но без лишней воды. Короткие, четкие фразы — нормально.Дай своей личности проявиться, но не затми суть.Не старайся быть «супер-помощником» в каждом предложении."
        waiting_message = await update.message.reply_text("🧠Составляю ответ...🧠", disable_notification=True)
    elif role == 'career_consultant':
        if chat_history:
            prompt = f"Ты - карьерный консультант. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты опытный карьерный консультант. Пользователь пришел к тебе на прием впервые, веди интерактивный диалог. Ответь на вопрос: {message_text}."
        waiting_message = await update.message.reply_text("💼Составляю ответ...💼", disable_notification=True)
    else:
        await update.message.reply_text("Пожалуйста, выберите роль, нажав /start")
        return

    try:
        response = send_openai_request(prompt)
        await waiting_message.delete()
        await update.message.reply_text(response)
        save_chat_history(user_id, response, 'bot')  # Сохранение ответа бота в историю чатов
    except Exception as e:
        logger.error(f"Error generating response for role {role}: {e}")
        await update.message.reply_text("Произошла ошибка при получении ответа. Попробуйте еще раз позже.")


# Функция для отправки запросов к OpenAI
def send_openai_request(prompt: str, max_tokens: int = MAX_TOKENS) -> str:
    headers = {
        'Authorization': f'Bearer {PROXY_API_KEY}',
        'Content-Type': 'application/json'
    }
    data = {
        'model': MODEL_NAME,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': MAX_TOKENS
    }
    response = requests.post(PROXY_API_URL, headers=headers, json=data)
    response_data = response.json()
    return response_data['choices'][0]['message']['content']
# Подсчет токенов для ответа и обновление данных о пользователе
    response_tokens_used = count_tokens(reply_text)
    return reply_text, response_tokens_used
    add_or_update_user(user_data, user_id, username, response_tokens_used)

#Обработка команд
async def astrology_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'astrology')

async def tarot_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'tarot')

async def numerology_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'numerology')

async def self_development_coach_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'self_development_coach')

async def fun_tarot_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'fun_tarot')

async def psychologist_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'psychologist')

async def career_consultant_command(update: Update, context: CallbackContext) -> None:
    await handle_role_selection(update, context, 'career_consultant')


async def handle_role_selection(update: Update, context: CallbackContext, choice: str) -> None:
    context.user_data['role'] = choice
    if choice == "tarot":
        await update.message.reply_text(
            "🟨 /tarot\n\n"
            "✨ Добро пожаловать в мир ТАРО! ✨\n\n"
            "🃏 Карты Таро могут помочь вам раскрыть скрытые аспекты вашей жизни, получить ценные советы и посмотреть на ситуацию с новой стороны.\n\n"
            "1. Задайте любой вопрос, который у вас на сердце — это может быть вопрос о любви, карьере, здоровье или будущем.\n"
            "2. Постарайтесь быть конкретным в своём вопросе, чтобы карты могли дать вам наиболее точный ответ.\n\n"
            "🔮 Примеры вопросов:\n"
            "- Какие шаги мне следует предпринять для карьерного роста?\n"
            "- Какое решение будет наилучшим в текущей ситуации?\n"
            "- Как бы я хотела выстроить эти отношения?\n\n"
            "Не стесняйтесь, задайте свой вопрос, и пусть карты ТАРО откроют вам свою мудрость!"
        )
    elif choice == "astrology":
        if 'date_of_birth' in context.user_data:
            if 'time_of_birth' in context.user_data:
                if 'place_of_birth' in context.user_data:
                    await update.message.reply_text("Все данные уже введены. Введите ваш вопрос для астролога:")
                else:
                    await update.message.reply_text("Введите место рождения в свободной форме (Например: Казань или Выборг, Ленинградская обл. и тд):")
            else:
                await update.message.reply_text("Введите время рождения в формате ЧЧ:ММ (например 07:20 или 19:00):")
        else:
            await update.message.reply_text(
                "🟨 Астролог\n\n"
                "Добро пожаловать в мир астрологии! ✨\n\n"
                "Как астролог, я помогу вам понять, как звезды и планеты могут влиять на вашу жизнь.\n\n"
                "Мне понадобятся данные о вашей дате, времени и месте рождения, чтобы я мог составить ваш персональный гороскоп и поделиться с вами удивительными астрологическими прогнозами.\n\n"
                "Вот несколько примеров вопросов, которые вы можете задать:\n"
                "- Что говорит мой солнечный знак обо мне?\n"
                "- Какие планеты влияют на мою карьеру и отношения?\n"
                "- Как лунные фазы могут повлиять на мое настроение и энергию?\n\n"
                "Но, возможно, у вас есть свой вопрос, который больше вас интересует. Жду вашего запроса и готов помочь вам раскрыть тайны вашего астрологического пути.\n\n"
                "Введите вашу дату рождения в формате ДД.ММ.ГГГГ (Например: 01.01.1995):"
            )
    elif choice == "numerology":
        if 'date_of_birth' in context.user_data:
            await update.message.reply_text("Дата рождения уже введена. Введите ваш вопрос для нумеролога:")
        else:
            await update.message.reply_text(
                "🟨 Нумеролог\n\n"
                "Добро пожаловать в мир нумерологии! 🌟\n\n"
                "Как нумеролог, я помогу вам раскрыть тайны чисел, которые могут пролить свет на вашу личность, судьбу и жизненные пути.\n\n"
                "Мне понадобится дата вашего рождения, чтобы я мог провести анализ и поделиться с вами удивительными инсайтами о вашем жизненном пути и предназначении.\n\n"
                "Не стесняйтесь задавать вопросы о том, как числа могут влиять на вашу жизнь и как использовать эту информацию для личного роста и развития. Вот несколько примеров вопросов, которые вы можете задать:\n"
                "- Какое значение имеет мое число судьбы?\n"
                "- Как числа влияют на мою карьеру и личные отношения?\n\n"
                "Или можете задать любой другой вопрос, а я постараюсь помочь вам узнать больше о себе через призму чисел.\n\n"
                "Для продолжения введите свою дату рождения в формате ДД.ММ.ГГГГ (например: 01.01.1995):"
            )
    elif choice == "self_development_coach":
        prompt = "Представь, что ты коуч по саморазвитию, а я у тебя на приеме. Я впервые на приеме у коуча по саморазвитию, поэтому возьми инициативу по диалогу в свои руки."
        try:
            response = send_openai_request(prompt)
            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Error generating self-development coach response: {e}")
            await update.message.reply_text("Произошла ошибка при получении ответа. Попробуйте еще раз позже.")
    elif choice == "psychologist":
        keyboard = [
            [InlineKeyboardButton("Когнитивно-поведенческая", callback_data="cbt"), InlineKeyboardButton("Психодинамическая", callback_data="psychodynamic")],
            [InlineKeyboardButton("Гештальт-терапия", callback_data="gestalt"), InlineKeyboardButton("Не разбираюсь", callback_data="unsure")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Вы выбрали роль психолога. Выберите методику терапии или нажмите 'не разбираюсь':", reply_markup=reply_markup)
    elif choice == "career_consultant":
        await update.message.reply_text(
            "🟨 Карьерный консультант\n\n"
            "💼 Добро пожаловать к Карьерному консультанту!\n\n"
            "Я могу помочь вам с профессиональными советами, планированием карьеры и достижением ваших карьерных целей.\n\n"
            "❓ **Как это работает:**\n"
            "1. Опишите вашу текущую профессиональную ситуацию или задайте конкретный вопрос о карьере.\n"
            "2. Я дам вам рекомендации и советы, чтобы помочь вам продвинуться в вашей карьере.\n\n"
            "🔮 **Примеры вопросов:**\n"
            "- Как мне улучшить свои навыки для повышения?\n"
            "- Как подготовиться к собеседованию на новую работу?\n"
            "- Как достичь баланса между работой и личной жизнью?\n\n"
            "Не стесняйтесь, задайте свой вопрос, и я помогу вам найти наилучшее решение!"
        )

# Обработчик для голосовых сообщений
async def handle_voice_message(update: Update, context: CallbackContext) -> None:
    waiting_message = await update.message.reply_text("Слушаю ваше голосовое сообщение, пожалуйста, дождитесь ответа.")

    # Получаем голосовое сообщение
    voice = await context.bot.get_file(update.message.voice.file_id)

    # Загрузка аудио файла в память
    file = io.BytesIO(await voice.download_as_bytearray())

    # Конвертация OGG в WAV с помощью pydub
    try:
        audio = AudioSegment.from_file(file, format="ogg")
        wav_io = io.BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)
    except Exception as e:
        await update.message.reply_text("Ошибка при конвертации аудиофайла. Попробуйте снова.")
        return

    recognizer = sr.Recognizer()

    # Конвертация аудио в текст
    try:
        with sr.AudioFile(wav_io) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="ru-RU")  # Выбор русского языка
            await waiting_message.delete()
            await handle_message(update, context, recognized_text=text)
    except sr.UnknownValueError:
        await update.message.reply_text("Не удалось распознать речь. Попробуйте снова.")
    except sr.RequestError:
        await update.message.reply_text("Ошибка сервиса распознавания речи. Попробуйте снова позже.")

# Определяем состояния для разговора
FEEDBACK = range(1)

async def feedback_command(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Пожалуйста, оставьте ваш отзыв или предложение. Введите ваше сообщение:",
    )
    return FEEDBACK

async def receive_feedback(update: Update, context: CallbackContext) -> None:
    user_feedback = update.message.text
    admin_chat_id = '517173111'

    # Отправка обратной связи администратору
    await context.bot.send_message(
        chat_id=admin_chat_id,
        text=f"Новая обратная связь от пользователя {update.message.from_user.username} ({update.message.from_user.id}):\n\n{user_feedback}"
    )
    await update.message.reply_text("Спасибо за ваш отзыв! Он был отправлен администратору.")
    return ConversationHandler.END

async def cancel_feedback(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Отмена отправки отзыва.")
    return ConversationHandler.END

async def clear_birth_data_command(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    user_data = load_user_data()
    user_found = False
    for user in user_data:
        if user['user_id'] == user_id:
            user.pop('date_of_birth', None)
            user.pop('time_of_birth', None)
            user.pop('place_of_birth', None)
            user_found = True
            break

    if user_found:
        save_user_data(user_data)
        context.user_data.pop('date_of_birth', None)
        context.user_data.pop('time_of_birth', None)
        context.user_data.pop('place_of_birth', None)

        await update.message.reply_text("Ваши данные о рождении были удалены. Теперь вы можете ввести новые данные.")

        # Проверяем текущую роль пользователя и автоматически вызываем нужную команду
        current_role = context.user_data.get('role')
        if current_role == 'astrology':
            await astrology_command(update, context)
        elif current_role == 'numerology':
            await numerology_command(update, context)
    else:
        await update.message.reply_text("Ваши данные о рождении не найдены.")

# Основная функция запуска бота
def main() -> None:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    # Обработчики команд
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('astrology', astrology_command))
    application.add_handler(CommandHandler('tarot', tarot_command))
    application.add_handler(CommandHandler('numerology', numerology_command))
    application.add_handler(CommandHandler('self_development_coach', self_development_coach_command))
    application.add_handler(CommandHandler('psychologist', psychologist_command))
    application.add_handler(CommandHandler('career_consultant', career_consultant_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("clear_birth_data", clear_birth_data_command))

    feedback_handler = ConversationHandler(
        entry_points=[CommandHandler('feedback', feedback_command)],
        states={
            FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_feedback)],
        },
        fallbacks=[CommandHandler('cancel', cancel_feedback)],
    )
    application.add_handler(feedback_handler)

    # Обработчики нажатий кнопок
    application.add_handler(CallbackQueryHandler(button_click, pattern='^(tarot|astrology|numerology|self_development_coach|psychologist|career_consultant)$'))
    application.add_handler(CallbackQueryHandler(handle_psychologist_choice, pattern='^(cbt|psychodynamic|gestalt|unsure)$'))

    # Обработчики сообщений пользователя
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()