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
import re
from cryptography.fernet import Fernet

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
ADMIN_CHAT_ID = list(map(int, os.getenv('ADMIN_CHAT_ID', '').split(',')))
CHAT_HISTORY_FILE = 'user_chat_history.json'
CHANNEL_IDS = os.getenv('CHANNEL_IDS').split(',')

# Настройки модели
MODEL_NAME = os.getenv('MODEL_NAME')
MAX_TOKENS = int(os.getenv('MAX_TOKENS'))
TEMPERATURE = float(os.getenv('TEMPERATURE'))


#Состояния для разговора
START, AGREE_TO_TERMS = range(2)

# Генерация ключа шифрования (должен быть сохранен в безопасном месте)
ENCRYPTION_KEY = Fernet.generate_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

# def encrypt_data(data: str) -> str:
#     """Шифрует данные."""
#     encrypted_data = cipher_suite.encrypt(data.encode())
#     return encrypted_data.decode()

# def decrypt_data(encrypted_data: str) -> str:
#     """Дешифрует данные."""
#     decrypted_data = cipher_suite.decrypt(encrypted_data.encode())
#     return decrypted_data.decode()

def load_stop_words(file_path):
    """Загружает стоп-слова из файла и возвращает их в виде множества."""
    with open(file_path, 'r', encoding='utf-8') as file:
        stop_words = {line.strip() for line in file}
    return stop_words

def create_stop_words_regex(stop_words):
    """Создает регулярное выражение для поиска стоп-слов в сообщении."""
    # Объединяем стоп-слова в одну строку, разделяя их символом "|" (или)
    stop_words_pattern = '|'.join(map(re.escape, stop_words))
    # Создаем регулярное выражение для поиска стоп-слов в любом месте сообщения
    return re.compile(stop_words_pattern, re.IGNORECASE)

def validate_message(message, stop_words_regex):
    """Проверяет, содержит ли сообщение стоп-слова."""
    # Ищем стоп-слова в сообщении
    if stop_words_regex.search(message):
        return False
    return True

stop_words_file = 'stop_words.txt'  # Путь к файлу со стоп-словами
stop_words = load_stop_words(stop_words_file)
stop_words_regex = create_stop_words_regex(stop_words)


# Функция для загрузки данных из JSON-файла
def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as file:
            user_data = [json.loads(line) for line in file]
            # for user in user_data:
            #     if 'user_id' in user and 'username' in user:
            #         user['user_id'] = decrypt_data(user['user_id'])
            #         user['username'] = decrypt_data(user['username'])
            return user_data
    return []

def save_user_data(user_data):
    with open(USER_DATA_FILE, 'w', encoding='utf-8') as file:
        for user in user_data:
            # if 'user_id' in user and 'username' in user:
            #     user['user_id'] = encrypt_data(user['user_id'])
            #     user['username'] = encrypt_data(user['username'])
            json.dump(user, file, ensure_ascii=False)
            file.write('\n')


# Функция для отправки уведомлений администратору
async def notify_admin(context: CallbackContext, message: str):
    for admin_id in ADMIN_CHAT_ID:
        await context.bot.send_message(chat_id=admin_id, text=message)


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
        # if isinstance(user, dict) and decrypt_data(user['user_id']) == user_id:
        if isinstance(user, dict) and user['user_id'] == user_id:
            # user['username'] = encrypt_data(username)
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
            user['daily_requests'] = user.get('daily_requests', 0)
            user['last_request_date'] = user.get('last_request_date', datetime.now().strftime('%d-%m-%Y'))
            user_found = True
            break

    if not user_found:
        new_user = {
            # 'user_id': encrypt_data(user_id),
            # 'username': encrypt_data(username),
            'user_id': user_id,
            'username': username,
            'registration_date': datetime.now().strftime('%d-%m-%Y'),  # Дата регистрации
            'last_active': datetime.now().strftime('%d-%m-%Y'),
            'tokens_used': tokens_used,
            'date_of_birth': date_of_birth,
            'time_of_birth': time_of_birth,
            'place_of_birth': place_of_birth,
            'subscribe': True,
            'daily_requests': 0,
            'last_request_date': datetime.now().strftime('%d-%m-%Y')
        }
        user_data.append(new_user)
        context.application.create_task(notify_admin(context, f"Новый пользователь: {username} (ID: {user_id})"))

    save_user_data(user_data)  # Сохраняем все данные после любого изменения


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

async def agree_to_terms(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🧠 Психолог", callback_data="psychologist")],
        [InlineKeyboardButton("💼 Карьерный консультант", callback_data="career_consultant")],
        [InlineKeyboardButton("🚀 Коуч по саморазвитию", callback_data="self_development_coach")],
        [InlineKeyboardButton("🃏 Таро", callback_data="tarot")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "Выбери одну из ролей ниже, чтобы начать:\n\n"
        "🧠 Психолог: Психолог поможет в работе с тревогой и депрессивными мыслями, построением гармоничных отношений.\n"
        "🚀 Коуч по саморазвитию: Достижение целей и личностный рост вместе со мной.\n"
        "📈 Карьерный консультант: Советы по карьерному развитию и профессиональному росту.\n"
        "🃏 ТАРО: Иногда на ситуацию надо посмотреть с неожиданной стороны.\n"
        "Просто нажми на одну из кнопок ниже, чтобы начать свое увлекательное путешествие!\n\n"
        "Ознакомиться с инструкцией и возможностями бота можно здесь /help",
        reply_markup=reply_markup
    )

    return ConversationHandler.END
# Функции для обработки команд
async def start(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    is_subscribed = await check_subscription_multiple(user_id, TELEGRAM_TOKEN, CHANNEL_IDS)
    if not is_subscribed:
        await update.message.reply_text(
            "Этот бот работает для вас бесплатно. Пожалуйста, подпишитесь на один из предложенных каналов, который может быть вам интересен и продолжите использование бота.\n\n"
            "Ссылки на каналы:\n"
            "1. [Канал про психологию](https://t.me/psikholog_onlajn_besplatno_chat)\n"
            "2. [Психолог, работа с тревогой](https://t.me/juliakoyash)\n",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    user_data = load_user_data()
    add_or_update_user(user_data, user_id, username, context)

    keyboard = [
        [InlineKeyboardButton("Согласен с условиями, мне 18+ лет", callback_data="agree_to_terms")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🌟 Приветствую! Я твой личный гид по поиску лучших решений, балансу, крепкой веры в себя и доверию миру. Я являюсь искусственным интеллектом и могу выступать в роли психолога, коуча, карьерного консультанта.\n\n"
        "Пожалуйста, ознакомьтесь с важной информацией перед началом использования:\n\n"
        "1. Возрастное ограничение: Этот бот рассчитан только для лиц, достигших 18 лет. Использование сервиса несовершеннолетними не предусмотрено. Если вам нет 18 лет, вам необходимо обратиться в центры, которые специализируются на помощи подросткам.\n\n"
        "2. Какие запросы обрабатывает: Этот бот предоставляет поддержку и советы на основе искусственного интеллекта и скорее предполагает повседневную помощь. Если вы столкнулись с серьёзной или кризисной ситуацией, пожалуйста, обратитесь за помощью к квалифицированному специалисту.\n\n"
        "3. Бот не заменит терапевтических услуг: Консультации, предлагаемые ботом, не могут заменить полноценную терапию или консультацию у специалиста.\n\n"
        "4. Точность ответов: Необходимо понимать, что бот может давать не всегда корректные или подходящие в вашем случае ответы. Мы напоминаем, что ответственность за использование рекомендаций лежит на пользователе.\n\n"
        "5. Бесплатное использование ботом предполагает 5 запросов в день.\n\n"
        "Если у вас возникнут вопросы или предложения, не стесняйтесь обращаться. Берегите себя!\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вам 18+ лет и вы согласны с условиями:",
        reply_markup=reply_markup
    )

    return AGREE_TO_TERMS

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

    # Проверка на наличие стоп-слов
    if not validate_message(message_text, stop_words_regex):
        await update.message.reply_text(
            "Извините, я не могу отвечать на подобные вопросы. Пожалуйста, направьте ваши запросы в безопасное и конструктивное русло.")
        return

    addition_for_prompt = ("Анализируй каждый запрос на предмет содержания. Если запрос "
                           "содержит неадекватные, аморальные, пошлые, агрессивные, деструктивные элементы, ответь "
                           "следующим образом: 'Извините, я не могу отвечать на подобные вопросы. Пожалуйста, направьте "
                           "ваши запросы в безопасное и конструктивное русло.'")

    # Проверка ограничения запросов
    for user in user_data:
        if user['user_id'] == user_id:
            today = datetime.now().strftime('%d-%m-%Y')
            if user['last_request_date'] != today:
                user['daily_requests'] = 0
                user['last_request_date'] = today

            if user['daily_requests'] >= 5:
                await update.message.reply_text("Вы превысили лимит 5 запросов в день. Попробуйте завтра.")
                return

            user['daily_requests'] += 1
            break

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
    elif role == 'self_development_coach':
        if chat_history:
            prompt = f"Ты - Коуч по саморазвитию. Вот история общения:\n{chat_history_text}\nПользователь: {message_text}"
        else:
            prompt = f"Представь, что ты коуч по саморазвитию. Ответь на вопрос: {message_text}."
        waiting_message = await update.message.reply_text("💪Составляю ответ...💪", disable_notification=True)
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
        # prompt += addition_for_prompt
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
async def check_subscription_and_handle_role(update: Update, context: CallbackContext, choice: str) -> None:
    user_id = update.message.from_user.id
    is_subscribed = await check_subscription_multiple(user_id, TELEGRAM_TOKEN, CHANNEL_IDS)
    if not is_subscribed:
        await update.message.reply_text("Вы не подписаны ни на 1 из каналов. Пожалуйста, подпишитесь на один из предложенных каналов, чтобы продолжить использование бота.")
        return
    await handle_role_selection(update, context, choice)


async def tarot_command(update: Update, context: CallbackContext) -> None:
    await check_subscription_and_handle_role(update, context, 'tarot')

async def self_development_coach_command(update: Update, context: CallbackContext) -> None:
    await check_subscription_and_handle_role(update, context, 'self_development_coach')

async def psychologist_command(update: Update, context: CallbackContext) -> None:
    await check_subscription_and_handle_role(update, context, 'psychologist')

async def career_consultant_command(update: Update, context: CallbackContext) -> None:
    await check_subscription_and_handle_role(update, context, 'career_consultant')


async def handle_role_selection(update: Update, context: CallbackContext, choice: str) -> None:
    context.user_data['role'] = choice
    if choice == "tarot":
        await update.message.reply_text(
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

    # Отправка обратной связи всем администраторам
    for admin_id in ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"Новая обратная связь от пользователя {update.message.from_user.username} ({update.message.from_user.id}):\n\n{user_feedback}"
        )

    await update.message.reply_text("Спасибо за ваш отзыв! Он был отправлен администраторам.")
    return ConversationHandler.END

async def cancel_feedback(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Отмена отправки отзыва.")
    return ConversationHandler.END

# Основная функция запуска бота
def main() -> None:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    # Обработчики команд
    start_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            AGREE_TO_TERMS: [CallbackQueryHandler(agree_to_terms, pattern='^agree_to_terms$')],
        },
        fallbacks=[],
    )
    application.add_handler(start_handler)

    application.add_handler(CommandHandler('tarot', tarot_command))
    application.add_handler(CommandHandler('self_development_coach', self_development_coach_command))
    application.add_handler(CommandHandler('psychologist', psychologist_command))
    application.add_handler(CommandHandler('career_consultant', career_consultant_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))

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