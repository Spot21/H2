import logging
import traceback
import asyncio
import json
from datetime import datetime, timezone

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.models import User
from database.db_manager import get_session
from services.quiz_service import QuizService
from services.parent_service import ParentService

logger = logging.getLogger(__name__)

class CommonHandler:
    def __init__(self, quiz_service: QuizService, parent_service: ParentService):
        self.quiz_service = quiz_service
        self.parent_service = parent_service

    async def check_and_create_user(self, user_id: int, username: str, full_name: str, role: str) -> bool:
        """Проверка и создание пользователя, если он не существует"""
        try:
            from database.models import User
            from database.db_manager import get_session

            with get_session() as session:
                # Проверяем существование пользователя
                existing_user = session.query(User).filter(User.telegram_id == user_id).first()

                if existing_user:
                    # Обновляем существующего пользователя
                    existing_user.username = username
                    existing_user.full_name = full_name
                    existing_user.role = role
                    existing_user.last_active = datetime.now(timezone.utc)
                    if not existing_user.settings:
                        existing_user.settings = '{}'

                    logger.info(f"Обновлен пользователь: id={existing_user.id}, роль={role}")
                    session.commit()
                    return True
                else:
                    # Создаем нового пользователя
                    new_user = User(
                        telegram_id=user_id,
                        username=username,
                        full_name=full_name,
                        role=role,
                        created_at=datetime.now(timezone.utc),
                        last_active=datetime.now(timezone.utc),
                        settings='{}' if role == 'parent' else None
                    )

                    session.add(new_user)
                    session.commit()

                    # Проверяем создание
                    check_user = session.query(User).filter(User.telegram_id == user_id).first()
                    if check_user:
                        logger.info(f"Создан новый пользователь: id={check_user.id}, роль={role}")
                        return True
                    else:
                        logger.error(f"Не удалось создать пользователя с telegram_id={user_id}")
                        return False

        except Exception as e:
            logger.error(f"Ошибка при проверке/создании пользователя: {e}")
            logger.error(traceback.format_exc())
            return False

    async def handle_common_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик нажатий на общие кнопки интерфейса"""
        query = update.callback_query
        callback_data = query.data
        user_id = update.effective_user.id

        logger.debug(f"Processing button {callback_data} from user {user_id}")

        await query.answer()

        logger.info(f"Обработка нажатия кнопки: {callback_data} пользователем {user_id}")

        # Если это выбор роли, обрабатываем особым образом
        if callback_data == "common_role_student":
            logger.info(f"Начало регистрации пользователя {user_id} как ученика")
            try:
                telegram_user = update.effective_user
                full_name = f"{telegram_user.first_name} {telegram_user.last_name or ''}"

                # Создаем или обновляем пользователя
                success = await self.check_and_create_user(
                    user_id=user_id,
                    username=telegram_user.username,
                    full_name=full_name,
                    role="student"
                )

                if not success:
                    raise Exception("Не удалось создать/обновить пользователя")

                # Отправляем сообщение о успешной регистрации
                await query.edit_message_text(
                    "✅ Вы успешно зарегистрированы как ученик!\n\n"
                    "Вы можете проходить тесты, отслеживать свою успеваемость и получать достижения."
                )

                # Небольшая пауза перед отображением меню
                await asyncio.sleep(1)

                # Отправляем главное меню
                from keyboards.student_kb import student_main_keyboard
                reply_markup = student_main_keyboard()

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Выберите действие:",
                    reply_markup=reply_markup
                )
                return
            except Exception as e:
                logger.error(f"Ошибка при регистрации ученика: {e}")
                logger.error(traceback.format_exc())
                await query.edit_message_text(
                    "Произошла ошибка при регистрации. Пожалуйста, попробуйте еще раз или обратитесь к администратору."
                )
                return

        elif callback_data == "student_recommendations":
            from handlers.student import StudentHandler
            student_handler = StudentHandler(self.quiz_service)
            await student_handler.show_recommendations(update, context)

        elif callback_data == "admin_problematic_questions":
            from handlers.admin import AdminHandler
            admin_handler = AdminHandler()
            await admin_handler.show_problematic_questions(update, context)

        elif callback_data == "common_role_parent":
            logger.info(f"Начало регистрации пользователя {user_id} как родителя")
            try:
                telegram_user = update.effective_user
                full_name = f"{telegram_user.first_name} {telegram_user.last_name or ''}"

                # Создаем или обновляем пользователя
                success = await self.check_and_create_user(
                    user_id=user_id,
                    username=telegram_user.username,
                    full_name=full_name,
                    role="parent"
                )

                if not success:
                    raise Exception("Не удалось создать/обновить пользователя")

                # Отправляем сообщение о успешной регистрации
                await query.edit_message_text(
                    "✅ Вы успешно зарегистрированы как родитель!\n\n"
                    "Вы можете привязать аккаунт ученика, используя команду /link с кодом, который вам предоставит ученик."
                )

                # Небольшая пауза перед отображением меню
                await asyncio.sleep(1)

                # Отправляем главное меню
                from keyboards.parent_kb import parent_main_keyboard
                reply_markup = parent_main_keyboard()

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Выберите действие:",
                    reply_markup=reply_markup
                )
                return
            except Exception as e:
                logger.error(f"Ошибка при регистрации родителя: {e}")
                logger.error(traceback.format_exc())
                await query.edit_message_text(
                    "Произошла ошибка при регистрации. Пожалуйста, попробуйте еще раз или обратитесь к администратору."
                )
                return

        # Проверяем, зарегистрирован ли пользователь
        try:
            # Получаем роль пользователя
            with get_session() as session:
                user = session.query(User).filter(User.telegram_id == user_id).first()
                if not user:
                    logger.warning(f"Пользователь {user_id} не найден в базе при нажатии на кнопку {callback_data}")
                    await query.edit_message_text(
                        "Кажется, вы еще не зарегистрированы. Пожалуйста, используйте команду /start"
                    )
                    return

                # Обновляем время последней активности
                user.last_active = datetime.now(timezone.utc)
                session.commit()

                role = user.role
                logger.info(f"Роль пользователя {user_id}: {role}")

            # Обработка кнопок в зависимости от callback_data
            if callback_data.startswith("common_start_test") or callback_data == "common_start_test":
                logger.debug(f"Перенаправление на start_test")
                from handlers.student import StudentHandler
                student_handler = StudentHandler(self.quiz_service)
                context.user_data["from_button"] = True  # Флаг для функции, что вызов из кнопки
                await student_handler.start_test(update, context)

            elif callback_data.startswith("common_stats") or callback_data == "common_stats":
                logger.debug(f"Перенаправление на show_stats")

                # Определяем период для статистики
                if callback_data == "common_stats":
                    period = "all"
                else:
                    period = callback_data.replace("common_stats_", "")

                # Устанавливаем период в качестве аргумента
                context.args = [period]
                context.user_data["from_button"] = True  # Флаг для функции

                from handlers.student import StudentHandler
                student_handler = StudentHandler(self.quiz_service)
                await student_handler.show_stats(update, context)

            elif callback_data == "common_achievements":
                logger.debug(f"Перенаправление на show_achievements")

                context.user_data["from_button"] = True  # Флаг для функции
                from handlers.student import StudentHandler
                student_handler = StudentHandler(self.quiz_service)
                await student_handler.show_achievements(update, context)

            elif callback_data == "common_help":
                logger.debug(f"Перенаправление на help_command")

                from handlers.start import StartHandler
                start_handler = StartHandler()
                # Получаем текст справки в зависимости от роли
                help_text = start_handler.get_help_text(role)

                # И просто редактируем сообщение
                await query.edit_message_text(
                    help_text,
                    parse_mode="Markdown"
                )

            # Обработчики для кнопок в тестах
            elif callback_data.startswith("quiz_start_"):
                logger.debug(f"Перенаправление на handle_test_button")
                from handlers.student import StudentHandler
                student_handler = StudentHandler(self.quiz_service)
                await student_handler.handle_test_button(update, context)

            elif (callback_data.startswith("quiz_answer_") or
                  callback_data.startswith("quiz_seq_") or
                  callback_data.startswith("quiz_reset_") or
                  callback_data.startswith("quiz_confirm_") or
                  callback_data == "quiz_skip"):
                logger.debug(f"Перенаправление на handle_test_button")
                from handlers.student import StudentHandler
                student_handler = StudentHandler(self.quiz_service)
                await student_handler.handle_test_button(update, context)

            elif callback_data == "common_link_student":
                logger.debug(f"Перенаправление на инструкцию по привязке ученика")
                await query.edit_message_text(
                    "Для привязки аккаунта ученика используйте команду /link с кодом ученика.\n\n"
                    "Пример: /link 123456\n\n"
                    "Код можно получить у ученика, который должен выполнить команду /mycode"
                )

            elif callback_data == "common_reports":
                logger.debug(f"Перенаправление на get_report")
                # Создаем пустой список аргументов для команды
                context.args = []
                from handlers.parent import ParentHandler
                parent_handler = ParentHandler(self.parent_service)
                await parent_handler.get_report(update, context)

            elif callback_data == "common_parent_settings":
                logger.debug(f"Перенаправление на settings")
                # Создаем пустой список аргументов для команды
                context.args = []
                from handlers.parent import ParentHandler
                parent_handler = ParentHandler(self.parent_service)
                await parent_handler.settings(update, context)

            elif callback_data == "common_help":
                logger.debug(f"Перенаправление на help_command")
                # Удаляем текущее сообщение с кнопками, чтобы не было конфликта
                await query.delete_message()
                from handlers.start import StartHandler
                start_handler = StartHandler()
                await start_handler.help_command(update, context)

            elif callback_data == "common_admin_panel":
                logger.debug(f"Перенаправление на admin_panel")
                # Удаляем текущее сообщение с кнопками, чтобы не было конфликта
                await query.delete_message()
                from handlers.admin import AdminHandler
                admin_handler = AdminHandler()
                await admin_handler.admin_panel(update, context)

            elif callback_data.startswith("common_leaderboard"):
                logger.debug(f"Перенаправление на show_leaderboard")

                # Определяем период для лидерборда
                if callback_data == "common_leaderboard":
                    period = "week"
                else:
                    period = callback_data.replace("common_leaderboard_", "")

                # Устанавливаем период в качестве аргумента
                context.args = [period]
                await self.show_leaderboard(update, context, period)

            else:
                logger.warning(f"Неизвестный callback_data: {callback_data}")
                await query.edit_message_text(
                    f"Неизвестная команда: {callback_data}\n\nИспользуйте /help для получения списка доступных команд."
                )

        except Exception as e:
            logger.error(f"Error in handle_common_button: {e}")
            logger.error(traceback.format_exc())
            try:
                await query.edit_message_text(
                    "Произошла ошибка при обработке запроса. Пожалуйста, попробуйте еще раз."
                )
            except Exception as edit_error:
                logger.error(f"Ошибка при попытке редактирования сообщения: {edit_error}")
                # Если не удалось отредактировать сообщение, пробуем отправить новое
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="Произошла ошибка при обработке запроса. Пожалуйста, попробуйте еще раз."
                    )
                except Exception:
                    pass  # Если и это не удалось, просто игнорируем

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик текстовых сообщений, которые не являются командами"""
        user_id = update.effective_user.id
        message_text = update.message.text
        logger.debug(f"Получено сообщение от пользователя {user_id}: {message_text[:20]}...")

        # Получаем роль пользователя
        with get_session() as session:
            user = session.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                await update.message.reply_text(
                    "Кажется, вы еще не зарегистрированы. Пожалуйста, используйте команду /start"
                )
                return

            user_role = user.role

        # Проверяем наличие состояния пользователя
        user_state = None
        if "admin_state" in context.user_data:
            user_state = "admin"
            state_value = context.user_data["admin_state"]
        elif "student_state" in context.user_data:
            user_state = "student"
            state_value = context.user_data["student_state"]
        elif "parent_state" in context.user_data:
            user_state = "parent"
            state_value = context.user_data["parent_state"]

        logger.debug(f"Состояние пользователя {user_id}: {user_state}, значение: {state_value if user_state else None}")

        # Перенаправляем ввод в зависимости от состояния
        if user_state == "admin":
            if user_role != "admin":
                await update.message.reply_text("У вас нет прав администратора для выполнения этого действия.")
                context.user_data.pop("admin_state", None)
                return

            from handlers.admin import AdminHandler
            admin_handler = AdminHandler()
            logger.debug(f"Перенаправление ввода администратора в состоянии {context.user_data['admin_state']}")
            await admin_handler.handle_admin_input(update, context)
        elif user_state == "student":
            # Обработка состояний ученика
            logger.debug(f"Обрабатываем ввод ученика в состоянии {context.user_data['student_state']}")
            # Добавить обработчик для состояний ученика, если есть
            await update.message.reply_text(
                "Функционал в разработке. Пожалуйста, используйте кнопки для взаимодействия."
            )
        elif user_state == "parent":
            # Обработка состояний родителя
            logger.debug(f"Обрабатываем ввод родителя в состоянии {context.user_data['parent_state']}")
            # Добавить обработчик для состояний родителя, если есть
            await update.message.reply_text(
                "Функционал в разработке. Пожалуйста, используйте кнопки для взаимодействия."
            )
        else:
            # Стандартный ответ, если нет активного состояния
            # Можно показать подсказку в зависимости от роли пользователя
            if user_role == "admin":
                await update.message.reply_text(
                    "Я не понимаю ваше сообщение. Используйте команду /admin для доступа к панели администратора."
                )
            elif user_role == "student":
                await update.message.reply_text(
                    "Я не понимаю ваше сообщение. Используйте команду /test для начала тестирования или кнопки меню."
                )
            elif user_role == "parent":
                await update.message.reply_text(
                    "Я не понимаю ваше сообщение. Используйте команду /link для привязки аккаунта ученика или кнопки меню."
                )
            else:
                await update.message.reply_text(
                    "Я не понимаю ваше сообщение. Пожалуйста, используйте команды или кнопки для взаимодействия."
                    "\n\nДля получения справки введите /help"
                )

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик ошибок для логирования и информирования пользователя"""
        logger.error(f"Exception while handling an update: {context.error}")

        # Логируем трассировку ошибки
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        logger.error(f"Exception traceback: {tb_string}")

        # Отправляем сообщение пользователю
        if update and hasattr(update, "effective_chat"):
            # Разные типы ошибок - разные сообщения
            if isinstance(context.error, telegram.error.BadRequest):
                message = "Произошла ошибка при отправке сообщения. Пожалуйста, попробуйте еще раз."
            elif isinstance(context.error, telegram.error.Unauthorized):
                message = "Бот не имеет доступа. Возможно, вы его заблокировали?"
            elif isinstance(context.error, telegram.error.TimedOut):
                message = "Истекло время ожидания ответа от серверов Telegram. Пожалуйста, попробуйте снова."
            else:
                message = "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте еще раз или обратитесь к администратору."

            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=message
                )
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

    async def show_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE, period=None) -> None:
        """Показать таблицу лидеров"""
        user_id = update.effective_user.id
        query = update.callback_query

        try:
            # Получаем период, если указан
            period = context.args[0] if context.args else "week"
            if period not in ["week", "month", "year", "all"]:
                period = "week"

            # Получаем таблицу лидеров
            from services.stats_service import generate_leaderboard
            leaderboard_result = generate_leaderboard(period, limit=10)
            logger.debug(f"Получены данные таблицы лидеров: {leaderboard_result}")

            if not leaderboard_result["success"]:
                error_message = f"Ошибка получения таблицы лидеров: {leaderboard_result['message']}"
                if query:
                    await query.edit_message_text(error_message)
                else:
                    await update.message.reply_text(error_message)
                return

            if not leaderboard_result.get("has_data", False):
                # Используем готовую клавиатуру
                from keyboards.student_kb import leaderboard_period_keyboard
                reply_markup = leaderboard_period_keyboard()

                message = f"За выбранный период ({self.get_period_name(period)}) нет данных для составления таблицы лидеров."
                if query:
                    await query.edit_message_text(message, reply_markup=reply_markup)
                else:
                    await update.message.reply_text(message, reply_markup=reply_markup)
                return

            # Формируем сообщение с таблицей лидеров
            message = f"🏆 *Таблица лидеров за {self.get_period_name(period)}*\n\n"

            for i, user_data in enumerate(leaderboard_result["leaderboard"], 1):
                name = user_data.get("full_name") or user_data.get("username") or f"Ученик {user_data.get('id')}"
                score = user_data.get("avg_score", 0)
                tests = user_data.get("tests_count", 0)

                message += f"{i}. {name} - {score:.2f} баллов ({tests} тестов)\n"

            # Используем готовую клавиатуру
            from keyboards.student_kb import leaderboard_period_keyboard
            reply_markup = leaderboard_period_keyboard()

            if query:
                await query.edit_message_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Ошибка в show_leaderboard: {e}")
            logger.error(traceback.format_exc())

            error_message = "Произошла ошибка при отображении таблицы лидеров."
            if query:
                try:
                    await query.edit_message_text(error_message)
                except Exception:
                    await context.bot.send_message(chat_id=user_id, text=error_message)
            else:
                await update.message.reply_text(error_message)

    def get_period_name(self, period: str) -> str:
        """Получение названия периода на русском языке"""
        if period == "week":
            return "неделю"
        elif period == "month":
            return "месяц"
        elif period == "year":
            return "год"
        elif period == "all":
            return "всё время"
        else:
            return "неизвестный период"
