"""
git pull origin master - если были изменения в гите
"""

from dotenv import load_dotenv
import logging
import os
import google.generativeai as genai
from docx import Document
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import difflib

load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


# Функция для чтения текстов песен из DOCX файлов
def read_songs(folder_path):
    songs = {}
    for filename in os.listdir(folder_path):
        if filename.endswith('.docx'):
            file_path = os.path.join(folder_path, filename)
            doc = Document(file_path)
            full_text = '\n'.join([paragraph.text for paragraph in doc.paragraphs])
            songs[filename[:-5]] = full_text  # Используем имя файла без расширения как название песни
    return dict(sorted(songs.items()))  # Сортируем словарь по ключам (названиям песен)


def read_chord_files(folder_path):
    chords = {}
    song_titles = set(SONGS.keys())  # Получаем названия песен из SONGS

    for filename in os.listdir(folder_path):
        if filename.endswith('.pdf'):
            # Удаляем расширение и любые специальные символы из имени файла
            clean_filename = ''.join(c for c in filename[:-4] if c.isalnum() or c.isspace()).lower()

            # Ищем наиболее похожее название песни
            best_match = difflib.get_close_matches(clean_filename, song_titles, n=1, cutoff=0.6)

            if best_match:
                song_title = best_match[0]
                chords[song_title] = os.path.join(folder_path, filename)
            else:
                # Если соответствие не найдено, используем оригинальное имя файла без расширения
                chords[filename[:-4]] = os.path.join(folder_path, filename)

    return dict(sorted(chords.items()))


# Глобальные переменные для хранения песен и аккордов
SONGS = read_songs('songs')
CHORDS = read_chord_files('chords')


# Функция для поиска в песнях с использованием Gemini
async def search_with_gemini(query):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')

        # Подготавливаем данные о песнях для промпта
        songs_data = []
        for title, lyrics in SONGS.items():
            songs_data.append(f"Название: {title}\nТекст песни:\n{lyrics}\n---")

        songs_corpus = "\n".join(songs_data)

        prompt = f"""Ты — помощник для поиска песен в базе данных церковных песен.

        Вот запрос пользователя: "{query}"
        
        Ниже представлены все песни из нашей базы данных:
        
        {songs_corpus}

        Найди песни, которые наиболее соответствуют запросу пользователя. Поиск может осуществляться по:
        1. Названию песни
        2. Словам из текста
        3. Теме или смыслу песни
        4. Библейскому контексту
        
        Формат ответа:
        1. Перечисли найденные песни, отсортированные по релевантности
        2. Для каждой песни укажи название и короткое обоснование почему эта песня подходит к запросу
        3. Если не найдено подходящих песен, так и скажи
        4. Не используй markdown в ответе, но используй абзацы и эмодзи.
        
        Отвечай кратко и по существу."""

        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Ошибка при запросе к Google Gemini API: {str(e)}")
        return f"Произошла ошибка при поиске: {str(e)}"


# Функция для создания клавиатуры с буквами, на которые начинаются названия песен
def create_alphabet_keyboard():
    # Получаем список уникальных первых букв названий песен
    available_letters = sorted(set(title[0].upper() for title in SONGS.keys() if title[0].isalpha()))

    # Создаем клавиатуру только с этими буквами
    keyboard = []
    row = []
    for letter in available_letters:
        row.append(InlineKeyboardButton(letter, callback_data=f"letter_{letter}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


# Функция для создания клавиатуры с песнями на выбранную букву
def create_songs_keyboard(letter):
    keyboard = []
    for title in sorted(SONGS.keys()):
        if title.upper().startswith(letter):
            keyboard.append([InlineKeyboardButton(title, callback_data=f"song_{title}")])
    return InlineKeyboardMarkup(keyboard)


# Функция для обработки команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Executing /start command")
    context.user_data['gemini_mode'] = True  # Включаем режим поиска через Gemini по умолчанию
    keyboard = ReplyKeyboardMarkup([
        ['📚 База по алфавиту'],
        ['🎵 Все песни']
    ], resize_keyboard=True)
    await update.message.reply_text('Выберите действие или введите запрос для поиска песни:', reply_markup=keyboard)


# Функция для обработки команды /menu
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Executing /menu command")
    keyboard = create_alphabet_keyboard()
    message = update.message if update.message else update.callback_query.message
    await message.reply_text('Выберите букву или введите название песни:', reply_markup=keyboard)


# Функция для отображения всех песен с ограничением и кнопками "Еще" и "Назад"
async def all_songs(update_or_message, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Executing /all_songs command")

    # Количество песен на одну страницу
    songs_per_page = 10

    # Получаем список всех песен
    all_songs_list = sorted(SONGS.keys())

    # Сбрасываем индекс на 0 при новом вызове команды
    context.user_data['current_song_index'] = 0
    context.user_data['previous_song_index'] = None  # Не существует предыдущего списка при первом вызове

    # Текущий индекс первой песни для вывода
    current_index = context.user_data.get('current_song_index', 0)

    # Получаем подмножество песен для текущего вывода
    next_index = current_index + songs_per_page
    songs_to_show = all_songs_list[current_index:next_index]

    # Создаем кнопки для отображаемых песен
    keyboard_inline = [[InlineKeyboardButton(title, callback_data=f"song_{title}")] for title in songs_to_show]

    # Добавляем кнопки "Назад" и "Еще" на одну строку
    navigation_buttons = []
    if current_index > 0:
        navigation_buttons.append(InlineKeyboardButton("Назад", callback_data="show_previous_songs"))
    if next_index < len(all_songs_list):
        navigation_buttons.append(InlineKeyboardButton("Еще", callback_data="show_more_songs"))

    if navigation_buttons:
        keyboard_inline.append(navigation_buttons)

    # Проверяем, где было сообщение
    if hasattr(update_or_message, 'message'):
        message = update_or_message.message
    else:
        message = update_or_message

    # Отправляем сообщение с песнями
    await message.reply_text('Список песен:', reply_markup=InlineKeyboardMarkup(keyboard_inline))

    # Сохраняем текущий и предыдущий индекс для навигации
    context.user_data['previous_song_index'] = current_index  # Предыдущий список — текущий перед обновлением
    context.user_data['current_song_index'] = next_index  # Обновляем текущий индекс


# Функция для поиска текста песни в ручном режиме (прежний метод)
async def find_song_manually(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    logger.info(f"Поиск песни по запросу вручную: {user_input}")
    found_songs = []

    for title, lyrics in SONGS.items():
        if user_input.lower() in title.lower() or user_input.lower() in lyrics.lower():
            found_songs.append((title, lyrics))

    if found_songs:
        for title, lyrics in found_songs:
            await update.message.reply_text(lyrics)
            try:
                await update.message.reply_document(document=open(f'songs/{title}.docx', 'rb'))
            except FileNotFoundError:
                await update.message.reply_text(f"Файл {title}.docx не найден.")

            # Добавляем кнопку для аккордов
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎸 Аккорды", callback_data=f"chords_{title}")],
                [InlineKeyboardButton("📚 База по алфавиту", callback_data="menu")],
                [InlineKeyboardButton("🎵 Все песни", callback_data="all_songs")]
            ])
            await update.message.reply_text("Жми👇:", reply_markup=keyboard)
    else:
        await update.message.reply_text('Песни по вашему запросу не найдены.')


# Функция для переключения режима Gemini
async def toggle_gemini_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Инвертируем текущее состояние
    current_mode = context.user_data.get('gemini_mode', True)
    new_mode = not current_mode
    context.user_data['gemini_mode'] = new_mode

    # Создаем новую клавиатуру с обновленным статусом
    mode_text = "ВКЛ" if new_mode else "ВЫКЛ"
    keyboard = ReplyKeyboardMarkup([
        ['📚 База по алфавиту'],
        ['🎵 Все песни'],
        [f'🔍 Режим Gemini: {mode_text}']
    ], resize_keyboard=True)

    await update.message.reply_text(
        f"Режим поиска Gemini {'включен' if new_mode else 'выключен'}. "
        f"{'Теперь ваши запросы будут обрабатываться с помощью AI' if new_mode else 'Теперь будет использоваться стандартный поиск'}.",
        reply_markup=keyboard
    )


# Функция для обработки текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if text == '📚 База по алфавиту':
        await menu(update, context)
    elif text == '🎵 Все песни':
        await all_songs(update, context)
    else:
        # Поиск с использованием Gemini всегда включен
        await update.message.reply_text("Ищу песни по вашему запросу...")
        result = await search_with_gemini(text)

        # Отправляем результат поиска
        await update.message.reply_text(result)

        # Добавляем клавиатуру для дальнейшей навигации
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 База по алфавиту", callback_data="menu")],
            [InlineKeyboardButton("🎵 Все песни", callback_data="all_songs")]
        ])
        await update.message.reply_text("Жми👇:", reply_markup=keyboard)


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    logger.info(f"Получен callback_data: {query.data}")

    if query.data == "show_more_songs":
        # Получаем список всех песен
        all_songs_list = sorted(SONGS.keys())
        songs_per_page = 10

        # Текущий индекс для вывода
        current_index = context.user_data.get('current_song_index', 0)
        next_index = current_index + songs_per_page

        # Получаем подмножество песен для текущего вывода
        songs_to_show = all_songs_list[current_index:next_index]

        # Создаем кнопки для отображаемых песен
        keyboard_inline = [[InlineKeyboardButton(title, callback_data=f"song_{title}")] for title in songs_to_show]

        # Добавляем кнопки "Назад" и "Еще" на одну строку
        navigation_buttons = []
        if current_index > 0:
            navigation_buttons.append(InlineKeyboardButton("Назад", callback_data="show_previous_songs"))
        if next_index < len(all_songs_list):
            navigation_buttons.append(InlineKeyboardButton("Еще", callback_data="show_more_songs"))

        if navigation_buttons:
            keyboard_inline.append(navigation_buttons)

        # Обновляем сообщение с новыми песнями
        await query.message.edit_text('Список песен:', reply_markup=InlineKeyboardMarkup(keyboard_inline))

        # Обновляем индексы
        context.user_data['previous_song_index'] = current_index
        context.user_data['current_song_index'] = next_index

    elif query.data == "show_previous_songs":
        # Получаем список всех песен
        all_songs_list = sorted(SONGS.keys())
        songs_per_page = 10

        # Индекс для возврата
        previous_index = context.user_data.get('previous_song_index', 0)
        current_index = max(previous_index - songs_per_page, 0)  # Ограничиваем минимальным индексом 0
        next_index = previous_index

        # Получаем подмножество песен для вывода
        songs_to_show = all_songs_list[current_index:next_index]

        # Создаем кнопки для отображаемых песен
        keyboard_inline = [[InlineKeyboardButton(title, callback_data=f"song_{title}")] for title in songs_to_show]

        # Добавляем кнопки "Назад" и "Еще" на одну строку
        navigation_buttons = []
        if current_index > 0:
            navigation_buttons.append(InlineKeyboardButton("Назад", callback_data="show_previous_songs"))
        if next_index < len(all_songs_list):
            navigation_buttons.append(InlineKeyboardButton("Еще", callback_data="show_more_songs"))

        if navigation_buttons:
            keyboard_inline.append(navigation_buttons)

        # Обновляем сообщение с песнями
        await query.message.edit_text('Список песен:', reply_markup=InlineKeyboardMarkup(keyboard_inline))

        # Обновляем индексы
        context.user_data['current_song_index'] = next_index
        context.user_data['previous_song_index'] = current_index

    elif query.data.startswith("letter_"):
        letter = query.data.split("_")[1]
        keyboard = create_songs_keyboard(letter)
        await query.edit_message_text(text=f"Песни на букву {letter}:", reply_markup=keyboard)
    elif query.data.startswith("song_") or query.data.startswith("gpt_song_"):
        if query.data.startswith("gpt_song_"):
            title = query.data[9:]  # Убираем префикс "gpt_song_"
        else:
            title = query.data[5:]  # Убираем префикс "song_"

        lyrics = SONGS.get(title, "Песня не найдена")

        # Удаляем сообщение с кнопками
        await query.message.delete()

        # Отправляем новое сообщение с текстом песни
        await query.message.reply_text(lyrics)

        try:
            # Отправляем файл
            await query.message.reply_document(document=open(f'songs/{title}.docx', 'rb'))
        except FileNotFoundError:
            await query.message.reply_text(f"Файл {title}.docx не найден.")

        # Создаем клавиатуру с кнопками "Аккорды", "База по алфавиту", "Все песни" и "Найти стих из Библии к песне"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎸 Аккорды", callback_data=f"chords_{title}")],
            [InlineKeyboardButton("📚 База по алфавиту", callback_data="menu")],
            [InlineKeyboardButton("🎵 Все песни", callback_data="all_songs")],
            [InlineKeyboardButton("📖 Найти стих из Библии к песне", callback_data=f"bible_{title}")]
        ])
        await query.message.reply_text("Жми👇:", reply_markup=keyboard)
    elif query.data.startswith("chords_"):
        title = query.data[7:]
        chord_file = CHORDS.get(title)

        if chord_file:
            try:
                # Отправляем файл с аккордами
                await query.message.reply_document(document=open(chord_file, 'rb'))
            except FileNotFoundError:
                await query.message.reply_text(f"Файл с аккордами {title}.pdf не найден.")
        else:
            await query.message.reply_text("Аккорды для этой песни не найдены.")

        # Отправляем сообщение с предложением вернуться к меню
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 База по алфавиту", callback_data="menu")],
            [InlineKeyboardButton("🎵 Все песни", callback_data="all_songs")]
        ])
        await query.message.reply_text("Жми👇:", reply_markup=keyboard)
    elif query.data.startswith("bible_"):
        title = query.data[6:]
        lyrics = SONGS.get(title, "Песня не найдена")

        # Получаем духовное наставление и стих из Библии
        await query.message.reply_text("Пожалуйста, подождите...")

        spiritual_guidance, bible_verse = await get_spiritual_guidance_and_bible_verse(title, lyrics)

        await query.message.reply_text(f"**Духовное наставление:**\n\n{spiritual_guidance}")
        await query.message.reply_text(f"**Подходящий стих из Библии:**\n\n{bible_verse}")

        # Добавляем клавиатуру с кнопками "Аккорды", "База по алфавиту" и "Все песни"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎸 Аккорды", callback_data=f"chords_{title}")],
            [InlineKeyboardButton("📚 База по алфавиту", callback_data="menu")],
            [InlineKeyboardButton("🎵 Все песни", callback_data="all_songs")]
        ])
        await query.message.reply_text("Жми👇:", reply_markup=keyboard)
    elif query.data == "menu":
        # Вызов функции menu при нажатии на кнопку "Вернуться к меню"
        await menu(update, context)
    elif query.data == "all_songs":
        # Вызов функции all_songs при нажатии на кнопку "Все песни"
        await all_songs(query.message, context)


async def get_spiritual_guidance_and_bible_verse(song_title, lyrics):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')

        prompt = (f"Проанализируйте следующую песню с названием '{song_title}' и текстом:\n\n{lyrics}\n\n"
                  f"Выполните две задачи:\n"
                  f"1. Найдите подходящий стих из Библии, который соответствует тематике этой песни\n"
                  f"2. Напишите духовное наставление в стиле глубоких богословских размышлений\n\n"
                  f"Разделите ответ на две части: сначала духовное наставление, затем библейский стих.")

        response = model.generate_content(prompt)

        # Разделяем ответ на две части
        parts = response.text.split('\n\n', 1)

        if len(parts) == 2:
            guidance, verse = parts
        else:
            guidance = parts[0]
            verse = "Не удалось найти подходящий стих."

        return guidance, verse
    except Exception as e:
        logger.error(f"Ошибка при запросе к Google Gemini API: {str(e)}")
        return "Ошибка при получении духовного наставления.", "Ошибка при получении стиха из Библии."


def main():
    # Вставьте сюда токен вашего бота
    token = os.getenv("BOT_TOKEN")

    # Создаем приложение
    application = Application.builder().token(token).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("all_songs", all_songs))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()