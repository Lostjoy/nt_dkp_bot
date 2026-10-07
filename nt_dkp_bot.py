import logging
import sqlite3
import os
import json
import re
import io

import easyocr
import numpy as np
from PIL import Image

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "525854881"))

if not TOKEN:
    raise ValueError("Ошибка: Не задана переменная TELEGRAM_BOT_TOKEN!")

# Черный список слов интерфейса ArcheAge/Games
BLACKLIST_WORDS = {
    'level', 'lv', 'hp', 'mp', 'stamina', 'exp', 'gold', 'silver', 'bronze',
    'damage', 'healing', 'kill', 'death', 'assist', 'raid', 'party', 'group',
    'clan', 'guild', 'war', 'battle', 'score', 'time', 'min', 'sec', 'ms',
    'ok', 'cancel', 'close', 'open', 'menu', 'chat', 'whisper', 'tell',
    'archeage', 'trion', 'world', 'server', 'zone', 'map', 'coord', 'x', 'y', 'z',
    'true', 'false', 'null', 'undefined'
}

# --- ИНИЦИАЛИЗАЦИЯ EASYOCR (ОДИН РАЗ ПРИ СТАРТЕ) ---
# Важно: делаем это ПОСЛЕ объявления logger
try:
    logger.info("⏳ Загрузка моделей EasyOCR...")
    ocr_reader = easyocr.Reader(['ru', 'en'], gpu=False)
    logger.info("✅ EasyOCR модели загружены успешно.")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки EasyOCR: {e}")
    ocr_reader = None


# --- РАБОТА С БАЗОЙ ДАННЫХ (SQLite) ---

def init_db():
    """Инициализация всех таблиц базы данных"""
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, nickname TEXT, game_level TEXT, status TEXT DEFAULT 'pending')''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER UNIQUE, nickname TEXT, role TEXT DEFAULT 'member')''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS points (
        tg_id INTEGER PRIMARY KEY, nickname TEXT, total_points INTEGER DEFAULT 0)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS event_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        event_type TEXT, participants_count INTEGER, admin_tg_id INTEGER, details TEXT)''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована.")

def add_application(tg_id, nickname, game_level):
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO applications (tg_id, nickname, game_level) VALUES (?, ?, ?)", (tg_id, nickname, game_level))
    conn.commit()
    app_id = cursor.lastrowid
    conn.close()
    return app_id

def approve_application(app_id):
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("SELECT tg_id, nickname FROM applications WHERE id = ?", (app_id,))
    row = cursor.fetchone()
    if row:
        tg_id, nickname = row
        try:
            cursor.execute("INSERT INTO members (tg_id, nickname) VALUES (?, ?)", (tg_id, nickname))
            cursor.execute("UPDATE applications SET status = 'approved' WHERE id = ?", (app_id,))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    conn.close()
    return False

def reject_application(app_id):
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE applications SET status = 'rejected' WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()

def get_all_members():
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, role FROM members")
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_player_points(tg_id, nickname, points_to_add):
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("SELECT total_points FROM points WHERE tg_id = ?", (tg_id,))
    row = cursor.fetchone()
    if row:
        new_total = row[0] + points_to_add
        cursor.execute("UPDATE points SET total_points = ? WHERE tg_id = ?", (new_total, tg_id))
    else:
        cursor.execute("INSERT INTO points (tg_id, nickname, total_points) VALUES (?, ?, ?)", (tg_id, nickname, points_to_add))
    conn.commit()
    conn.close()

def save_event_log(event_type, count, admin_id, details_json):
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO event_history (event_type, participants_count, admin_tg_id, details) VALUES (?, ?, ?, ?)",
                   (event_type, count, admin_id, details_json))
    conn.commit()
    conn.close()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ OCR ---

def is_valid_arche_nickname(nick):
    if not nick or len(nick) < 2 or len(nick) > 16:
        return False
    clean_nick = nick.strip()
    if clean_nick.isdigit():
        return False
    if clean_nick.lower() in BLACKLIST_WORDS:
        return False
    pattern = r'^[a-zA-Zа-яА-ЯёЁ0-9_\.\-\']+$'
    if not re.match(pattern, clean_nick):
        return False
    return True


# --- КОМАНДЫ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {user_name}! 👋\n\n"
        "Это официальный бот нашего клана ArcheAge.\n\n"
        "Что я умею:\n"
        "/apply - Подать заявку на вступление\n"
        "/members - Список участников клана\n"
        "/rules - Правила клана\n"
        "/top - Рейтинг игроков по баллам\n\n"
        "📸 *Админам:* Просто пришлите скриншот рейда/воя, чтобы начислить баллы!"
    )

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("📜 *Правила Клана*\n\n1. Активность обязательна (минимум 5 часов в неделю).\n"
            "2. Уважение к другим участникам.\n3. Не спамить в общем чате.\n"
            "4. Выполнять задания лидера.\n\nНарушение правил ведет к исключению.")
    await update.message.reply_text(text, parse_mode="Markdown")

async def apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отлично! Чтобы подать заявку, отправь мне сообщение в формате:\n\n"
        "`Ник в игре | Уровень`\n\nНапример: `DragonSlayer | 50`"
    )
    context.user_data['awaiting_apply'] = True

async def handle_apply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    app_id = add_application(tg_id, nickname, level)
    await update.message.reply_text(f"✅ Заявка принята!\nID заявки: {app_id}")
    del context.user_data['awaiting_apply']
    keyboard = [[
        InlineKeyboardButton("✅ Принять", callback_data=f"approve_{app_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{app_id}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Новая заявка!\nНик: {nickname}, Ур.: {level}", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

async def members_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    members = get_all_members()
    if not members:
        await update.message.reply_text("Клан пока пуст. Стань первым!")
        return
    text = "👥 *Участники клана:* \n\n"
    for nick, role in members:
        icon = "🛡️" if role == "admin" else ("⚔️" if role == "officer" else "🧍")
        text += f"{icon} {nick}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def top_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('clan.db')
    cursor = conn.cursor()
    cursor.execute("SELECT nickname, total_points FROM points ORDER BY total_points DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Баллов пока нет. Участвуйте в ивентах!")
        return
    text = "🏆 *Топ игроков клана:* \n\n"
    medals = ["🥇", "", "🥉"]
    for i, (nick, pts) in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} {nick}: {pts} баллов\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# --- ОБРАБОТКА ФОТО АКТИВНОСТИ (EASYOCR) ---
# ЕДИНСТВЕННАЯ ВЕРСИЯ ФУНКЦИИ

async def handle_activity_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает фото активности через EasyOCR"""
    
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещен.")
        return

    if ocr_reader is None:
        await update.message.reply_text("⚠️ Модуль распознавания не инициализирован. Проверьте логи сервера.")
        return

    photo = update.message.photo[-1]
    file_obj = await context.bot.get_file(photo.file_id)
    
    msg_status = await update.message.reply_text("📸 Получено фото. Распознаю текст (EasyOCR)...")

    try:
        img_bytes = await file_obj.download_as_bytearray()
        
        # Конвертируем байты в формат, понятный EasyOCR
        image = Image.open(io.BytesIO(bytes(img_bytes)))
        image_np = np.array(image)
        
        # Распознавание
        results = ocr_reader.readtext(image_np, detail=1, paragraph=False)
        
        if not results:
            await msg_status.edit_text("❌ Текст не найден на изображении.")
            return
        
        # Извлекаем текст с уверенностью > 0.3
        found_texts = []
        for bbox, text, conf in results:
            if conf > 0.3:
                found_texts.append(text.strip())
        
        logger.info(f"EasyOCR found {len(found_texts)} text blocks: {found_texts[:10]}")
        
        # Парсим ники
        valid_nicks = []
        seen_in_this_scan = set()
        
        for text_block in found_texts:
            candidates = re.findall(r'\S+', text_block)
            for cand in candidates:
                cleaned = cand.strip('.,;:!?"\'()[]{}')
                if is_valid_arche_nickname(cleaned) and cleaned not in seen_in_this_scan:
                    valid_nicks.append(cleaned)
                    seen_in_this_scan.add(cleaned)
        
        # Сверяем с базой
        recognized_players = []
        unknown_candidates = []
        
        conn = sqlite3.connect('clan.db')
        cursor = conn.cursor()
        POINTS_PER_RAID = 50 
        
        for nick in valid_nicks:
            cursor.execute("SELECT tg_id FROM members WHERE LOWER(nickname) = ?", (nick.lower(),))
            row = cursor.fetchone()
            if row:
                tg_id = row[0]
                recognized_players.append({'nick': nick, 'tg_id': tg_id})
                update_player_points(tg_id, nick, POINTS_PER_RAID)
            else:
                if len(nick) > 3: 
                     unknown_candidates.append(nick)
        conn.close()
        
        # Формируем отчет
        report_msg = (
            f"✅ Обработка завершена (EasyOCR)!\n\n"
            f"👥 Найдено участников: {len(recognized_players)}\n"
            f"💰 Начислено баллов каждому: {POINTS_PER_RAID}\n\n"
        )
        if recognized_players:
            report_msg += "**Успешно найдены:**\n"
            for p in recognized_players[:10]:
                report_msg += f"• `{p['nick']}` (+{POINTS_PER_RAID})\n"
            if len(recognized_players) > 10:
                report_msg += f"...и еще {len(recognized_players)-10}\n"
        if unknown_candidates:
            report_msg += "\n⚠️ **Не найдено в базе:**\n"
            report_msg += ", ".join([f"`{n}`" for n in unknown_candidates[:5]])
            if len(unknown_candidates) > 5:
                report_msg += f"\n...и еще {len(unknown_candidates)-5}"
                
        save_event_log("ArcheAge EasyOCR Photo", len(recognized_players), ADMIN_ID, json.dumps({
            "found": [p['nick'] for p in recognized_players],
            "misses": unknown_candidates,
            "raw_ocr_count": len(found_texts)
        }))

        await msg_status.edit_text(report_msg, parse_mode="Markdown")

    except MemoryError:
        logger.error("EasyOCR ran out of memory!")
        await msg_status.edit_text("❌ Недостаточно памяти для распознавания. Сервер перегружен.")
    except Exception as e:
        logger.error(f"EasyOCR critical error: {e}", exc_info=True)
        await msg_status.edit_text(f"❌ Ошибка распознавания: {str(e)}")


# --- ОБРАБОТКА КНОПОК ДЛЯ АДМИНА ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("apply", apply_command))
    app.add_handler(CommandHandler("members", members_list))
    app.add_handler(CommandHandler("top", top_players))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_apply_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_activity_photo))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Клан-бот запущен... Нажмите Ctrl+C для остановки.")
    app.run_polling()

if __name__ == '__main__':
    main()