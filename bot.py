import os
import logging
import sqlite3
from datetime import datetime, timedelta
from random import randint
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram import Message
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = "8142815825:AAEZeUHIXI2j44VDG6SrH8Vjv--jko7j7Eo"
DB = "timoteo_store.db"
COURSE_DEFAULT = 1.55
MIN_STARS = 50
REF_PERCENT = 5
ADMIN_IDS = [1012303659, 694613924]  # Ваш Telegram ID. Чтобы добавить второго админа, просто добавьте его ID через запятую, например: [1012303659, 222222222]
PAYMENTS_DIR = "payments"

# Создаем папку для платежей
os.makedirs(PAYMENTS_DIR, exist_ok=True)

# Состояния ConversationHandler
(
    CHOOSING,
    BUY_USERNAME,
    BUY_AMOUNT,
    WAIT_PAYMENT,
    ADMIN_PANEL,
    ADMIN_SET_COURSE,
    ADMIN_BROADCAST,
    VIEW_ORDERS,
    LEAVE_FEEDBACK,
) = range(9)

# Добавить новое состояние
(EXCHANGE_BONUS, CONFIRM_ORDER) = (9, 10)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    """Инициализация базы данных"""
    conn = None
    try:
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                stars INTEGER DEFAULT 0,
                referral_id INTEGER,
                referral_bonus INTEGER DEFAULT 0,
                referrals_count INTEGER DEFAULT 0,
                last_spin TEXT,
                registration_date TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                recipient_username TEXT,
                stars_amount INTEGER,
                price REAL,
                paid INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_referral_id ON users(referral_id)")
        
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('course', ?)", (str(COURSE_DEFAULT),))
        
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при инициализации БД: {e}")
        raise
    finally:
        if conn:
            conn.close()

def db_connect():
    """Безопасное подключение к БД"""
    conn = None
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise

def register_user(user_id, username, referral_id=None):
    """Регистрация нового пользователя"""
    if referral_id == user_id:
        referral_id = None
        
    try:
        conn = db_connect()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (user_id, username, referral_id) VALUES (?, ?, ?)",
                (user_id, username, referral_id),
            )
            if referral_id:
                cur.execute(
                    "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?",
                    (referral_id,),
                )
        else:
            cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
        
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")
    finally:
        if conn:
            conn.close()

def get_user(user_id):
    """Получение данных пользователя"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return cur.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Ошибка получения пользователя: {e}")
        return None
    finally:
        if conn:
            conn.close()

def update_stars(user_id, amount):
    """Обновление баланса звёзд"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET stars = stars + ? WHERE user_id=?", (amount, user_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка обновления звёзд: {e}")
    finally:
        if conn:
            conn.close()

def add_order(user_id, recipient_username, stars_amount, price, paid=0):
    """Добавление нового заказа"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO orders (user_id, recipient_username, stars_amount, price, paid) VALUES (?, ?, ?, ?, ?)",
            (user_id, recipient_username, stars_amount, price, paid),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка добавления заказа: {e}")
    finally:
        if conn:
            conn.close()

def get_orders(user_id):
    """Получение списка заказов пользователя"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Ошибка получения заказов: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_setting(key):
    """Получение значения настройки"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        res = cur.fetchone()
        return res[0] if res else None
    except sqlite3.Error as e:
        logger.error(f"Ошибка получения настройки: {e}")
        return None
    finally:
        if conn:
            conn.close()

def set_setting(key, value):
    """Установка значения настройки"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        if get_setting(key) is None:
            cur.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        else:
            cur.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка установки настройки: {e}")
    finally:
        if conn:
            conn.close()

def add_feedback(user_id, text):
    """Добавление отзыва"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO feedback (user_id, text) VALUES (?, ?)",
            (user_id, text),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка добавления отзыва: {e}")
    finally:
        if conn:
            conn.close()

def clean_old_data():
    """Очистка старых данных"""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM orders WHERE paid = 0 AND created_at < datetime('now', '-3 days')")
        conn.commit()
        logger.info("Очистка старых данных выполнена")
    except sqlite3.Error as e:
        logger.error(f"Ошибка очистки данных: {e}")
    finally:
        if conn:
            conn.close()

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🌟 Купить звёзды", callback_data="buy")],
        [InlineKeyboardButton("🎁 Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton("🤝 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton("🧾 Профиль", callback_data="profile")],
        [InlineKeyboardButton("💸 Обменять бонус", callback_data="exchange_bonus")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("📝 Оставить отзыв", callback_data="feedback")],
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("⚙️ Установить курс", callback_data="set_course")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="broadcast")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

def cancel_keyboard(show_main_menu=True):
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]]
    if show_main_menu:
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

def confirm_order_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Изменить получателя", callback_data="edit_recipient"),
            InlineKeyboardButton("✏️ Изменить количество", callback_data="edit_amount")
        ],
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay_order")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])

# Добавить клавиатуру для подтверждения заказа админом

def admin_confirm_keyboard(order_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_order_{order_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_order_{order_id}")
        ]
    ])

async def show_main_menu(update, context, greeting=False):
    current_course = float(get_setting('course') or COURSE_DEFAULT)
    if greeting:
        text = (
            "👋 Приветствую в Timoteo Store!⭐️ Тут вы можете купить звезды телеграм по лучшей цене. Быстро, дешево, безопасно! 🔐\n"
            "Поддержка бота: @timoteo4"
        )
    else:
        text = (
            f"Текущий курс: {current_course}₽ за 1 звезду\n\n"
            "Выбери действие:"
        )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    elif hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=main_menu_keyboard())

# ========== Fallback-обработчик ==========
MENU_KEYWORDS = {"меню", "назад", "главное меню", "menu", "main menu"}

def contains_menu_keyword(text):
    text = (text or "").lower().replace(" ", "")
    for kw in MENU_KEYWORDS:
        if kw.replace(" ", "") in text:
            return True
    return False

async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "") if update.message else ""
    if contains_menu_keyword(text):
        await show_main_menu(update, context, greeting=False)
        return ConversationHandler.END
    if update.message:
        await update.message.reply_text(
            "Я не знаю такой команды. Для возврата напишите 'меню' или используйте кнопку.",
            reply_markup=main_menu_keyboard()
        )
    return ConversationHandler.END

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and hasattr(update, 'message'):
        try:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
        except:
            try:
                await update.callback_query.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
            except:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        args = context.args
        referral_id = int(args[0]) if args and args[0].isdigit() else None
        user = update.effective_user
        if user:
            register_user(user.id, user.username or "", referral_id)
        context.user_data['course'] = float(get_setting('course') or COURSE_DEFAULT)
        await show_main_menu(update, context, greeting=True)
        if user and hasattr(user, 'id') and user.id in ADMIN_IDS and update.message:
            await update.message.reply_text("⚙️ Доступно админ-меню: /admin")
            
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📌 Доступные команды:\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n"
        "/admin - Админ-панель (только для админов)\n\n"
        "ℹ️ По всем вопросам обращайтесь к @timoteo4"
    )
    await update.message.reply_text(help_text)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        user_id = user.id if user and hasattr(user, 'id') else None
        if user_id in ADMIN_IDS and update.message:
            await update.message.reply_text("⚙️ Админ-панель", reply_markup=admin_menu_keyboard())
            return ADMIN_PANEL
        else:
            if update.message:
                await update.message.reply_text("❌ Доступ запрещён.")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_command: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик отмены действий"""
    try:
        query = update.callback_query if hasattr(update, 'callback_query') else None
        if query:
            await query.answer()
            if hasattr(query, 'message') and query.message:
                await query.message.edit_text("Действие отменено.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        if update.message:
            await update.message.reply_text("Действие отменено.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в cancel_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

# ========== ОБРАБОТЧИКИ СОСТОЯНИЙ ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    try:
        query = update.callback_query if hasattr(update, 'callback_query') else None
        if not query:
            if update.message:
                await update.message.reply_text("Неизвестная команда.")
            return ConversationHandler.END
        await query.answer()
        data = query.data if hasattr(query, 'data') else None
        user_id = query.from_user.id if hasattr(query, 'from_user') and query.from_user else None
        # --- ДОБАВЛЯЕМ ОБРАБОТКУ ПОДТВЕРЖДЕНИЯ/ОТКЛОНЕНИЯ ЗАКАЗА АДМИНОМ ---
        if data and data.startswith("confirm_order_"):
            order_id = int(data.split("_")[-1])
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
            order = cur.fetchone()
            if not order or order['paid']:
                await query.edit_message_text("Заказ уже подтверждён или не найден.")
                conn.close()
                return ConversationHandler.END
            # Начисляем звёзды покупателю
            update_stars(order['user_id'], order['stars_amount'])
            # Обновляем заказ как оплаченный
            cur.execute("UPDATE orders SET paid=1 WHERE order_id=?", (order_id,))
            conn.commit()
            # Реферальная система
            user = get_user(order['user_id'])
            if user and user['referral_id']:
                referral_id = user['referral_id']
                bonus_rub = int(order['price'] * REF_PERCENT / 100)
                bonus_stars = int(order['stars_amount'] * REF_PERCENT / 100)
                cur.execute("""
                    UPDATE users 
                    SET referral_bonus = referral_bonus + ?,
                        stars = stars + ?
                    WHERE user_id = ?
                """, (bonus_rub, bonus_stars, referral_id))
                conn.commit()
            conn.close()
            # Уведомляем пользователя о подтверждении
            try:
                await context.bot.send_message(
                    order['user_id'],
                    "Спасибо за покупку! Ваш заказ подтверждён и будет выполнен в ближайшее время.\n"
                    "Буду рад если вы оставите свой отзыв здесь - @otzivi_timoteo\n"
                    "Мой магазин со всеми товарами - @timoteo_store\n"
                    "Жду вас снова."
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя о подтверждении заказа: {e}")
            await query.edit_message_text("Заказ подтверждён и звёзды начислены.")
            return ConversationHandler.END
        elif data and data.startswith("reject_order_"):
            order_id = int(data.split("_")[-1])
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
            order = cur.fetchone()
            if not order or order['paid']:
                await query.edit_message_text("Заказ уже подтверждён/отклонён или не найден.")
                conn.close()
                return ConversationHandler.END
            # Отклоняем заказ (можно удалить или оставить paid=0)
            cur.execute("DELETE FROM orders WHERE order_id=?", (order_id,))
            conn.commit()
            conn.close()
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    order['user_id'],
                    "Ваш заказ был отклонён оператором. Если это ошибка — свяжитесь с поддержкой: @timoteo4"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя об отклонении заказа: {e}")
            await query.edit_message_text("Заказ отклонён.")
            return ConversationHandler.END
        # далее все проверки query.message перед вызовом reply_text/edit_text
        if data == "buy":
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(
                    "Укажите получателя звёзд ⭐️\n\n"
                    "📝 Введите username получателя\n"
                    "💡 Обязательно начните с символа @\n\n"
                    "📋 Формат: @username\n\n"
                    "💎 Звёзды будут отправлены указанному пользователю",
                    reply_markup=ReplyKeyboardRemove()
                )
            return BUY_USERNAME
        elif data == "daily_bonus":
            user = get_user(user_id)
            last_spin = user['last_spin'] if user and user['last_spin'] else None
            if last_spin:
                last_spin_date = datetime.strptime(last_spin, "%Y-%m-%d")
                if datetime.now() - last_spin_date < timedelta(days=1):
                    if hasattr(query, 'message') and isinstance(query.message, Message):
                        await query.message.reply_text("🎁 Ежедневный бонус уже получен. Попробуйте завтра!")
                    return ConversationHandler.END
            # 95% шанс 1-5, 5% шанс 6-100
            import random
            chance = random.random()
            if chance < 0.95:
                reward = random.randint(1, 5)
            else:
                reward = random.randint(6, 100)
            update_stars(user_id, reward)
            try:
                conn = db_connect()
                cur = conn.cursor()
                cur.execute("UPDATE users SET last_spin=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), user_id))
                conn.commit()
            finally:
                if conn:
                    conn.close()
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(f"🎁 Ваш ежедневный бонус: {reward} звёзд!\n\nЗаглядывайте каждый день и получайте больше!")
            return ConversationHandler.END
        elif data == "referrals":
            user = get_user(user_id)
            if user:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text(
                        f"🤝 Рефералы: {user['referrals_count']}\n"
                        f"🎁 Бонус: {user['referral_bonus']}\n\n"
                        f"Реферальная ссылка:\n"
                        f"t.me/{context.bot.username}?start={user_id}"
                    )
            else:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("Данные не найдены.")
            return ConversationHandler.END
        elif data == "profile":
            user = get_user(user_id)
            if user:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text(
                        f"🧾 Профиль:\n"
                        f"⭐ Звёзд: {user['stars']}\n"
                        f"🤝 Бонус: {user['referral_bonus']}\n"
                        f"👥 Приглашено: {user['referrals_count']}"
                    )
            else:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("Данные не найдены.")
            return ConversationHandler.END
        elif data == "my_orders":
            orders = get_orders(user_id)
            if orders:
                text = "📦 Ваши заказы:\n\n"
                for order in orders:
                    text += (
                        f"🆔 Заказ #{order['order_id']}\n"
                        f"👤 Получатель: {order['recipient_username']}\n"
                        f"⭐ Звёзд: {order['stars_amount']}\n"
                        f"💰 Сумма: {order['price']}₽\n"
                        f"📅 Дата: {order['created_at']}\n"
                        f"Статус: {'✅ Оплачено' if order['paid'] else '❌ Не оплачено'}\n\n"
                    )
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text(text, reply_markup=cancel_keyboard(show_main_menu=False))
                return VIEW_ORDERS
            else:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("У вас пока нет заказов.", reply_markup=cancel_keyboard(show_main_menu=False))
                return VIEW_ORDERS
        elif data == "feedback":
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text("Напишите ваш отзыв или предложение:", reply_markup=cancel_keyboard(show_main_menu=False))
            return LEAVE_FEEDBACK
        elif data == "exchange_bonus":
            user = get_user(user_id)
            bonus = user['referral_bonus'] if user else 0
            current_course = float(get_setting('course') or COURSE_DEFAULT)
            if bonus < 50:
                msg = f"Ваш бонус: {bonus}₽\n\nМинимальная сумма для обмена — 50₽.\nБонусы начисляются за покупки ваших рефералов."
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text(msg, reply_markup=cancel_keyboard(show_main_menu=False))
                return ConversationHandler.END
            msg = (
                f"💸 Ваш бонус: {bonus}₽\n\n"
                f"Вы можете обменять бонусные рубли на звёзды по курсу {current_course}₽ за 1 звезду.\n"
                f"Минимальная сумма для обмена — 50₽.\n\n"
                f"Введите сумму для обмена (целое число, не более {bonus}):"
            )
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(msg, reply_markup=cancel_keyboard(show_main_menu=False))
            context.user_data['max_bonus'] = bonus
            return EXCHANGE_BONUS
        elif data == "set_course":
            if user_id not in ADMIN_IDS:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("❌ Доступ запрещён.")
                return ConversationHandler.END
            current_course = float(get_setting('course') or COURSE_DEFAULT)
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(f"Текущий курс: {current_course}₽\nВведите новый:")
            return ADMIN_SET_COURSE
        elif data == "stats":
            if user_id not in ADMIN_IDS:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("❌ Доступ запрещён.")
                return ConversationHandler.END
            try:
                conn = db_connect()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) as users_count, SUM(stars) as total_stars FROM users")
                stats = cur.fetchone()
                cur.execute("""
                    SELECT 
                        u.user_id,
                        u.username,
                        u.referrals_count,
                        u.referral_bonus,
                        COUNT(o.order_id) as orders_count,
                        SUM(o.price) as total_income
                    FROM users u
                    LEFT JOIN orders o ON u.user_id = o.user_id
                    WHERE u.referrals_count > 0
                    GROUP BY u.user_id
                    ORDER BY u.referrals_count DESC
                """)
                referrals = cur.fetchall()
                text = (
                    f"📊 Общая статистика:\n"
                    f"👥 Пользователей: {stats['users_count']}\n"
                    f"⭐ Всего звёзд: {stats['total_stars'] or 0}\n\n"
                    f"🤝 Реферальная система:\n"
                )
                for ref in referrals:
                    text += (
                        f"\n@{ref['username']} (ID: {ref['user_id']})\n"
                        f"→ Приглашено: {ref['referrals_count']}\n"
                        f"→ Бонусов: {ref['referral_bonus']}\n"
                        f"→ Заказов: {ref['orders_count']}\n"
                        f"→ Сумма: {ref['total_income'] or 0}₽\n"
                    )
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text(text)
            except Exception as e:
                logger.error(f"Ошибка получения статистики: {e}")
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("⚠️ Ошибка получения статистики")
            finally:
                if conn:
                    conn.close()
            return ADMIN_PANEL
        elif data == "broadcast":
            if user_id not in ADMIN_IDS:
                if hasattr(query, 'message') and isinstance(query.message, Message):
                    await query.message.reply_text("❌ Доступ запрещён.")
                return ConversationHandler.END
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text("Введите текст рассылки:")
            return ADMIN_BROADCAST
        elif data == "main_menu":
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.edit_text("Выбери действие:", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        elif data == "cancel":
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.edit_text("Действие отменено.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        elif data == "pay_order":
            price = context.user_data.get("price") if context.user_data else None
            recipient = context.user_data.get("recipient_username", "-") if context.user_data else "-"
            amount = context.user_data.get("stars_amount") if context.user_data else None
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(
                    f"<b>РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:</b>\n"
                    f"+79652234445 Т-банк\n\n"
                    f"Сумма к оплате: <b>{price}₽</b>\n\n"
                    f"После оплаты напишите <b>оплатил</b> для подтверждения.\n"
                    f"\nВаша оплата будет проверена оператором. Звёзды будут начислены после подтверждения.",
                    reply_markup=cancel_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            return WAIT_PAYMENT
        elif data == "edit_recipient":
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(
                    "Укажите нового получателя звёзд ⭐️\n\n"
                    "📝 Введите username получателя\n"
                    "💡 Обязательно начните с символа @\n\n"
                    "📋 Формат: @username\n\n"
                    "💎 Звёзды будут отправлены указанному пользователю",
                    reply_markup=ReplyKeyboardRemove()
                )
            return BUY_USERNAME
        elif data == "edit_amount":
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text(
                    f"Введите количество звёзд (мин. {MIN_STARS}):",
                    reply_markup=cancel_keyboard(show_main_menu=False)
                )
            return BUY_AMOUNT
        else:
            if hasattr(query, 'message') and isinstance(query.message, Message):
                await query.message.reply_text("Неизвестная команда.")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        if hasattr(update, 'callback_query') and update.callback_query and hasattr(update.callback_query, 'message') and update.callback_query.message:
            await update.callback_query.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
        return ConversationHandler.END

async def buy_username_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        if not (update.message and update.message.text):
            if isinstance(update.message, Message):
                await update.message.reply_text("Ошибка! Username должен начинаться с '@'. Попробуйте ещё раз.", reply_markup=cancel_keyboard(show_main_menu=False))
            return BUY_USERNAME
        username = update.message.text.strip()
        if not username.startswith("@") or len(username) < 2:
            if isinstance(update.message, Message):
                await update.message.reply_text("Ошибка! Username должен начинаться с '@'. Попробуйте ещё раз.", reply_markup=cancel_keyboard(show_main_menu=False))
            return BUY_USERNAME
        context.user_data["recipient_username"] = username
        if isinstance(update.message, Message):
            await update.message.reply_text(f"Введите количество звёзд (мин. {MIN_STARS}):", reply_markup=cancel_keyboard(show_main_menu=False))
        return BUY_AMOUNT
    except Exception as e:
        logger.error(f"Ошибка в buy_username_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

async def buy_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        if not (update.message and update.message.text):
            if isinstance(update.message, Message):
                await update.message.reply_text("Ошибка! Введите число.", reply_markup=cancel_keyboard(show_main_menu=False))
            return BUY_AMOUNT
        amount = int(update.message.text.strip())
    except ValueError:
        if isinstance(update.message, Message):
            await update.message.reply_text("Ошибка! Введите число.", reply_markup=cancel_keyboard(show_main_menu=False))
        return BUY_AMOUNT
    except Exception as e:
        logger.error(f"Ошибка в buy_amount_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    if amount < MIN_STARS:
        if isinstance(update.message, Message):
            await update.message.reply_text(f"Минимум {MIN_STARS} звёзд. Попробуйте снова.", reply_markup=cancel_keyboard(show_main_menu=False))
        return BUY_AMOUNT
    context.user_data["stars_amount"] = amount
    price = round(amount * context.user_data.get('course', COURSE_DEFAULT), 2)
    context.user_data["price"] = price
    recipient = context.user_data.get("recipient_username", "-")
    confirm_text = (
        f"<b>Подтверждение заказа ✅</b>\n"
        f"Получатель: <b>{recipient}</b>\n"
        f"Количество звёзд: <b>{amount} ⭐️</b>\n"
        f"Сумма к оплате: <b>{price}₽</b>\n\n"
        f"Для оплаты заказа, нажмите <b>Оплатить</b>."
    )
    if isinstance(update.message, Message):
        await update.message.reply_text(confirm_text, reply_markup=confirm_order_keyboard(), parse_mode=ParseMode.HTML)
    return CONFIRM_ORDER

async def wait_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        has_photo = bool(update.message.photo) if update.message and hasattr(update.message, 'photo') else False
        if "оплатил" in text or has_photo:
            user_id = update.effective_user.id
            payment_data = context.user_data
            if has_photo:
                photo = await update.message.photo[-1].get_file()
                filename = f"{PAYMENTS_DIR}/{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                await photo.download_to_drive(filename)
            # Сохраняем заказ с paid=0
            add_order(
                user_id=user_id,
                recipient_username=payment_data['recipient_username'],
                stars_amount=payment_data['stars_amount'],
                price=payment_data['price'],
                paid=0
            )
            # Получаем только что созданный заказ (по user_id и paid=0, самый свежий)
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT order_id FROM orders WHERE user_id=? AND paid=0 ORDER BY created_at DESC LIMIT 1", (user_id,))
            order = cur.fetchone()
            order_id = order['order_id'] if order else None
            conn.close()
            # Уведомление админу с кнопками
            for admin_id in ADMIN_IDS:
                try:
                    buyer_username = f"@{update.effective_user.username}" if update.effective_user and update.effective_user.username else f"не указан (ID: {update.effective_user.id})"
                    recipient_username = payment_data['recipient_username'] if payment_data.get('recipient_username') else 'не указан'
                    await context.bot.send_message(
                        admin_id,
                        f"<b>Новый заказ!</b>\n"
                        f"Покупатель: {buyer_username}\n"
                        f"Получатель: {recipient_username}\n"
                        f"Сумма: <b>{payment_data['price']}₽</b>",
                        reply_markup=admin_confirm_keyboard(order_id),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
            if update.message:
                await update.message.reply_text(
                    "Спасибо! Ваша оплата будет проверена оператором. Ожидайте подтверждения.",
                    reply_markup=main_menu_keyboard()
                )
            return ConversationHandler.END
        else:
            if update.message:
                await update.message.reply_text("Не вижу оплату. Отправьте скрин или напишите 'оплатил'.", reply_markup=cancel_keyboard())
            return WAIT_PAYMENT
    except Exception as e:
        logger.error(f"Ошибка в wait_payment_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

async def admin_set_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        try:
            new_course = float(update.message.text.strip())
        except:
            if update.message:
                await update.message.reply_text("Ошибка! Введите число.", reply_markup=cancel_keyboard())
            return ADMIN_SET_COURSE
        set_setting("course", str(new_course))
        context.user_data['course'] = new_course
        if update.message:
            await update.message.reply_text(f"Курс обновлён: {new_course}₽", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_set_course: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text("❌ Доступ запрещён.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        text = update.message.text.strip() if update.message and update.message.text else ""
        if not text:
            if update.message:
                await update.message.reply_text("Текст пустой, попробуйте снова.", reply_markup=cancel_keyboard())
            return ADMIN_BROADCAST
        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users")
            users = cur.fetchall()
            count = 0
            for (uid,) in users:
                try:
                    await context.bot.send_message(uid, f"📢 Админ рассылка:\n\n{text}")
                    count += 1
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logger.warning(f"Ошибка отправки {uid}: {e}")
            if update.message:
                await update.message.reply_text(f"Рассылка отправлена {count} пользователям.", reply_markup=main_menu_keyboard())
        finally:
            if conn:
                conn.close()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_broadcast_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

async def leave_feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        user_id = update.effective_user.id
        text = update.message.text.strip() if update.message and update.message.text else ""
        if len(text) < 5:
            if update.message:
                await update.message.reply_text("Отзыв слишком короткий. Напишите подробнее.", reply_markup=cancel_keyboard())
            return LEAVE_FEEDBACK
        add_feedback(user_id, text)
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"Новый отзыв от @{update.effective_user.username}:\n\n{text}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить отзыв админу {admin_id}: {e}")
        if update.message:
            await update.message.reply_text(
                "✅ Спасибо за ваш отзыв!",
                reply_markup=main_menu_keyboard()
            )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в leave_feedback_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

async def exchange_bonus_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (update.message.text or "") if update.message and update.message.text else ""
        if contains_menu_keyword(text):
            await show_main_menu(update, context, greeting=False)
            return ConversationHandler.END
        user_id = update.effective_user.id
        user = get_user(user_id)
        bonus = user['referral_bonus'] if user else 0
        current_course = float(get_setting('course') or COURSE_DEFAULT)
        try:
            amount = int(text.strip())
        except:
            if update.message:
                await update.message.reply_text("Ошибка! Введите целое число.", reply_markup=cancel_keyboard())
            return EXCHANGE_BONUS
        if amount < 50:
            if update.message:
                await update.message.reply_text("Минимальная сумма для обмена — 50₽.", reply_markup=cancel_keyboard())
            return EXCHANGE_BONUS
        if amount > bonus:
            if update.message:
                await update.message.reply_text(f"У вас нет такой суммы бонуса. Максимум: {bonus}₽", reply_markup=cancel_keyboard())
            return EXCHANGE_BONUS
        stars = int(amount / current_course)
        if stars < 1:
            if update.message:
                await update.message.reply_text("Сумма слишком мала для обмена хотя бы на 1 звезду.", reply_markup=cancel_keyboard())
            return EXCHANGE_BONUS
        # Списываем бонус и начисляем звёзды
        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("UPDATE users SET referral_bonus = referral_bonus - ?, stars = stars + ? WHERE user_id = ?", (amount, stars, user_id))
            conn.commit()
        finally:
            if conn:
                conn.close()
        if update.message:
            await update.message.reply_text(f"✅ {amount}₽ успешно обменяны на {stars} звёзд!", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в exchange_bonus_handler: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

# ========== ЗАПУСК БОТА ==========
def main():
    """Основная функция запуска бота"""
    init_db()
    
    # Создаем Application с JobQueue
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Обработчики команд
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("admin", admin_command),
            CommandHandler("help", help_command),
            CallbackQueryHandler(button_handler),
        ],
        states={
            CHOOSING: [CallbackQueryHandler(button_handler)],
            BUY_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_username_handler)],
            BUY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_amount_handler)],
            WAIT_PAYMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wait_payment_handler),
                MessageHandler(filters.PHOTO, wait_payment_handler),
            ],
            ADMIN_PANEL: [CallbackQueryHandler(button_handler)],
            ADMIN_SET_COURSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_course)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_handler)],
            VIEW_ORDERS: [CallbackQueryHandler(button_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler)],
            LEAVE_FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, leave_feedback_handler), CallbackQueryHandler(cancel_handler)],
            EXCHANGE_BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, exchange_bonus_handler)],
            CONFIRM_ORDER: [CallbackQueryHandler(button_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_handler),
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)
    
    # Запуск очистки старых данных (если установлен JobQueue)
    try:
        application.job_queue.run_once(clean_old_data, when=5)
    except AttributeError:
        logger.warning("JobQueue не доступен. Очистка старых данных не будет выполняться автоматически.")
        # Выполняем очистку сразу
        clean_old_data()
    
    logger.info("Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()