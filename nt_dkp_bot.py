import logging
import sqlite3
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
# ВАЖНО: Замените эти значения на свои перед запуском!
TOKEN = os.environ.get
("8861486783:AAGwLhLTyjXlD_e73kJ-xg49-WXNYhyKuAw")  # Токен от @BotFather
ADMIN_ID = 525854881             # Ваш личный Telegram ID (число)

# --- РАБОТА С БАЗОЙ ДАННЫХ (SQLite) ---
def init_db():
    """Создает таблицы в базе данных, если их еще нет"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    
    # Таблица для входящих заявок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            nickname TEXT,
            game_level TEXT,
            status TEXT DEFAULT 'pending' -- pending, approved, rejected
        )
    ''')
    
    # Таблица для принятых участников
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            nickname TEXT,
            role TEXT DEFAULT 'member' -- member, officer, admin
        )
    ''')
    
    conn.commit()
    conn.close()

def add_application(tg_id, nickname, game_level):
    """Добавляет новую заявку в базу"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO applications (tg_id, nickname, game_level) VALUES (?, ?, ?)", 
                   (tg_id, nickname, game_level))
    conn.commit()
    app_id = cursor.lastrowid
    conn.close()
    return app_id

def get_pending_applications():
    """Получает список всех ожидающих рассмотрения заявок"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, tg_id, nickname, game_level FROM applications WHERE status = 'pending'")
    rows = cursor.fetchall()
    conn.close()
    return rows

def approve_application(app_id):
    """Принимает заявку: переносит игрока из applications в members"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    
    # Получаем данные заявки
    cursor.execute("SELECT tg_id, nickname FROM applications WHERE id = ?", (app_id,))
    row = cursor.fetchone()
    
    if row:
        tg_id, nickname = row
        try:
            # Добавляем в участники
            cursor.execute("INSERT INTO members (tg_id, nickname) VALUES (?, ?)", (tg_id, nickname))
            # Меняем статус заявки на 'approved'
            cursor.execute("UPDATE applications SET status = 'approved' WHERE id = ?", (app_id,))
            conn.commit()
            logger.info(f"Player {nickname} ({tg_id}) approved.")
            return True
        except sqlite3.IntegrityError:
            # Если игрок уже есть в базе (UNIQUE constraint failed)
            logger.warning(f"User {tg_id} already in members.")
            return False
    conn.close()
    return False

def reject_application(app_id):
    """Отклоняет заявку"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE applications SET status = 'rejected' WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

def get_all_members():
    """Возвращает список всех активных участников"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, role FROM members")
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- КОМАНДЫ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — Приветствие и меню"""
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {user_name}! 👋\n\n"
        "Это официальный бот нашего клана.\n\n"
        "Что я умею:\n"
        "/apply - Подать заявку на вступление\n"
        "/members - Список участников клана\n"
        "/rules - Правила клана\n\n"
        "Жду тебя в наших рядах!"
    )

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rules — Показывает правила"""
    text = (
        "📜 *Правила Клана*\n\n"
        "1. Активность обязательна (минимум 5 часов в неделю).\n"
        "2. Уважение к другим участникам.\n"
        "3. Не спамить в общем чате.\n"
        "4. Выполнять задания лидера.\n\n"
        "Нарушение правил ведет к исключению."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /apply — Начало процесса подачи заявки"""
    await update.message.reply_text(
        "Отлично! Чтобы подать заявку, отправь мне сообщение в формате:\n\n"
        "`Ник в игре | Уровень`\n\n"
        "Например: `DragonSlayer | 50`\n\n"
        "(Бот распознает эту команду автоматически)"
    )
    # Ставим флажок, что ждем текст заявки от этого пользователя
    context.user_data['awaiting_apply'] = True

async def handle_apply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычного текстового сообщения (заявки)"""
    # Проверяем, ждет ли бот именно заявку от этого юзера
    if not context.user_data.get('awaiting_apply'):
        return
        
    text = update.message.text
    parts = text.split('|')
    
    if len(parts) != 2:
        await update.message.reply_text("❌ Формат неверный. Используй: Ник | Уровень")
        return
        
    nickname = parts[0].strip()
    level = parts[1].strip()
    tg_id = update.effective_user.id
    
    # Сохраняем в БД
    app_id = add_application(tg_id, nickname, level)
    
    await update.message.reply_text(
        f"✅ Заявка принята!\n"
        f"Ник: {nickname}\n"
        f"ID заявки: {app_id}\n\n"
        "Администрация рассмотрит её в ближайшее время."
    )
    
    # Снимаем флажок ожидания
    del context.user_data['awaiting_apply']
    
    # Отправляем уведомление Админу с кнопками
    keyboard = [
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"approve_{app_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{app_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 Новая заявка!\nНик: {nickname}, Ур.: {level}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

async def members_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /members — Список участников"""
    members = get_all_members()
    if not members:
        await update.message.reply_text("Клан пока пуст. Стань первым!")
        return
        
    text = "👥 *Участники клана:* \n\n"
    for nick, role in members:
        icon = "🛡️" if role == "admin" else ("⚔️" if role == "officer" else "🧍")
        text += f"{icon} {nick}\n"
        
    await update.message.reply_text(text, parse_mode="Markdown")

# --- ОБРАБОТКА КНОПОК ДЛЯ АДМИНА ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на кнопки Принять/Отклонить"""
    query = update.callback_query
    await query.answer() # Гасим кружочек загрузки в Telegram
    
    data = query.data
    
    if data.startswith("approve_"):
        app_id = int(data.split("_")[1])
        success = approve_application(app_id)
        if success:
            await query.edit_message_text("✅ Игрок добавлен в клан!")
        else:
            await query.edit_message_text("⚠️ Ошибка или игрок уже в базе.")
            
    elif data.startswith("reject_"):
        app_id = int(data.split("_")[1])
        reject_application(app_id)
        await query.edit_message_text("❌ Заявка отклонена.")

# --- ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА ---
def main():
    # 1. Инициализация базы данных при старте
    init_db()
    
    # 2. Создание приложения
    app = ApplicationBuilder().token(TOKEN).build()

    # 3. Регистрация обработчиков команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("apply", apply_command))
    app.add_handler(CommandHandler("members", members_list))
    
    # 4. Обработчик текста (для приема заявок)
    # Фильтр фильтрует только текст, но не команды (начинающиеся с /)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_apply_message))
    
    # 5. Обработчик нажатий на кнопки (Callback Query)
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Клан-бот запущен... Нажмите Ctrl+C для остановки.")
    
    # 6. Запуск цикла опроса обновлений (Polling)
    app.run_polling()

if __name__ == '__main__':
    main()