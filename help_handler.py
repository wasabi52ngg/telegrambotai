from telegram import Update
from telegram.ext import ContextTypes

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Приветствуем! Это раздел помощи вашего виртуального помощника.\n\n"
        "Цель раздела: предоставить информацию о возможностях бота, инструкциях по использованию и помочь при возникновении проблем.\n\n"
        "Роли:\n"
        "     - Психолог: предлагает поддержку и консультации на основе различных методик (когнитивно-поведенческая терапия, психодинамическая терапия, гештальт-терапия).\n"
        "     - Коуч по саморазвитию: предлагает советы и рекомендации по личностному росту.\n"
        "     - Карьерный консультант: советы по карьерному развитию и профессиональному росту.\n"
        "     - Таро: иногда на ситуацию стоит посмотреть под неожиданным углом\n\n"
        "Команды:\n"
        "     - /start: Начать работу с ботом.\n"
        "     - /help: Получить помощь и информацию о боте.\n"
        "     - /psychologist: Выбрать роль психолога.\n"
        "     - /self_development_coach: Выбрать роль коуча по саморазвитию.\n"
        "     - /career_consultant: Выбрать роль карьерного консультанта.\n"
        "     - /tarot: Выбрать роль Таро.\n\n"
        "Инструкции по использованию:\n\n"
        "Начало работы:\n"
        "     1. Нажмите команду /start.\n"
        "     2. Выберите роль (например, психолог, коуч и т.д.).\n"
        "     3. Следуйте указаниям бота для ввода необходимых данных.\n\n"
        "Бот не отвечает на мои вопросы, что делать?\n"
        "Убедитесь, что вы следуете инструкциям и вводите данные в правильном формате. Если проблема сохраняется, свяжитесь с разработчиком.\n\n"
        "Если у вас возникли проблемы или предложения по улучшению, пожалуйста, свяжитесь с нами:\n"
        "       - Телеграм: @lunia_jul\n\n"
        "Спасибо за использование нашего бота! Надеемся, что этот раздел помощи окажется полезным для вас."
    )
    await update.message.reply_text(help_text)