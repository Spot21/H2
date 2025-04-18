import logging
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from io import BytesIO

from services.quiz_service import QuizService
from services.stats_service import get_user_stats
from database.models import User, TestResult, Topic, Achievement
from database.db_manager import get_session
from keyboards.student_kb import (
    student_main_keyboard, topic_selection_keyboard, single_question_keyboard,
    multiple_question_keyboard, sequence_question_keyboard, test_results_keyboard,
    stats_period_keyboard, achievements_keyboard, leaderboard_period_keyboard
)

logger = logging.getLogger(__name__)

class StudentHandler:
    def __init__(self, quiz_service: QuizService):
        self.quiz_service = quiz_service

    async def start_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /test для начала тестирования"""
        try:
            user_id = update.effective_user.id
            logger.info(f"Запуск теста для пользователя {user_id}")

            # Получаем список доступных тем
            topics = self.quiz_service.get_topics()

            if not topics:
                # Проверяем, откуда был вызов - из сообщения или кнопки
                if update.callback_query:
                    await update.callback_query.edit_message_text(
                        "К сожалению, доступных тем для тестирования нет. Пожалуйста, попробуйте позже."
                    )
                else:
                    await update.message.reply_text(
                        "К сожалению, доступных тем для тестирования нет. Пожалуйста, попробуйте позже."
                    )
                return

            # Используем готовую клавиатуру
            reply_markup = topic_selection_keyboard(topics)

            # Отправляем сообщение в зависимости от источника вызова
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    "Выберите тему для тестирования:",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    "Выберите тему для тестирования:",
                    reply_markup=reply_markup
                )

        except Exception as e:
            logger.error(f"Error in start_test: {e}")
            logger.error(traceback.format_exc())

            error_message = "Произошла ошибка при запуске теста. Пожалуйста, попробуйте еще раз позже."

            # Отправляем сообщение об ошибке в зависимости от источника вызова
            if update.callback_query:
                try:
                    await update.callback_query.edit_message_text(error_message)
                except Exception:
                    user_id = update.effective_user.id
                    await context.bot.send_message(chat_id=user_id, text=error_message)
            else:
                await update.message.reply_text(error_message)

    async def handle_test_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик нажатий кнопок при тестировании"""
        query = update.callback_query
        callback_data = query.data
        user_id = update.effective_user.id

        logger.debug(f"Processing button {callback_data} from user {user_id}")

        await query.answer()

        try:
            if query.data == "student_recommendations":
                await self.show_recommendations(update, context)

            elif query.data.startswith("quiz_start_"):
                # Начало теста по выбранной теме
                topic_id_str = query.data.replace("quiz_start_", "")

                # Обрабатываем случайную тему
                if topic_id_str == "random":
                    import random
                    topics = self.quiz_service.get_topics()
                    if not topics:
                        await query.edit_message_text("К сожалению, доступных тем нет.")
                        return
                    topic = random.choice(topics)
                    topic_id = topic["id"]
                else:
                    topic_id = int(topic_id_str)

                # Начинаем тест
                quiz_data = self.quiz_service.start_quiz(user_id, topic_id)

                if not quiz_data["success"]:
                    await query.edit_message_text(quiz_data["message"])
                    return

                # Показываем первый вопрос
                await self.show_question(update, context)

            elif query.data == "quiz_details":
                # Показ детального отчета о результатах последнего теста
                await self.show_detailed_results(update, context)

            elif query.data.startswith("quiz_repeat_"):
                # Повторное прохождение теста
                topic_id = int(query.data.replace("quiz_repeat_", ""))
                # Начинаем тест
                quiz_data = self.quiz_service.start_quiz(user_id, topic_id)
                if not quiz_data["success"]:
                    await query.edit_message_text(quiz_data["message"])
                    return
                # Показываем первый вопрос
                await self.show_question(update, context)

            elif query.data.startswith("quiz_answer_"):
                # Обработка ответа на вопрос
                parts = query.data.split("_")
                question_id = int(parts[2])
                option_index = int(parts[3])

                current_question = self.quiz_service.get_current_question(user_id)

                if current_question and current_question["id"] == question_id:
                    if current_question["question_type"] == "single":
                        # Для вопроса с одиночным выбором сразу отправляем ответ
                        result = self.quiz_service.submit_answer(user_id, question_id, option_index)

                        if result["success"]:
                            if result["is_completed"]:
                                # Тест завершен
                                await self.show_test_results(update, context, result["result"])
                            else:
                                # Показываем следующий вопрос
                                await self.show_question(update, context)
                        else:
                            await query.edit_message_text(result["message"])

                    elif current_question["question_type"] == "multiple":
                        # Для вопроса с множественным выбором обновляем выбранные варианты
                        selected_options = self.quiz_service.active_quizzes[user_id]["answers"].get(str(question_id), [])

                        if option_index in selected_options:
                            selected_options.remove(option_index)
                        else:
                            selected_options.append(option_index)

                        self.quiz_service.active_quizzes[user_id]["answers"][str(question_id)] = selected_options

                        # Обновляем вопрос с отмеченными вариантами
                        await self.show_question(update, context, edit=True)

            elif query.data.startswith("quiz_seq_"):
                # Обработка выбора для вопроса с последовательностью
                parts = query.data.split("_")
                question_id = int(parts[2])
                option_index = int(parts[3])
                current_question = self.quiz_service.get_current_question(user_id)
                if current_question and current_question["id"] == question_id:
                    # Проверяем, что этот вариант еще не выбран
                    sequence = self.quiz_service.active_quizzes[user_id]["answers"].get(str(question_id), [])
                    if str(option_index) not in sequence:
                        # Добавляем вариант к последовательности
                        sequence.append(str(option_index))
                        self.quiz_service.active_quizzes[user_id]["answers"][str(question_id)] = sequence
                        # Обновляем вопрос с текущей последовательностью
                        await self.show_question(update, context, edit=True)
                    else:
                        # Если вариант уже выбран, показываем уведомление
                        await query.answer("Этот вариант уже выбран в последовательности")

            elif query.data.startswith("quiz_reset_"):
                # Сброс текущей последовательности
                parts = query.data.split("_")
                question_id = int(parts[2])

                current_question = self.quiz_service.get_current_question(user_id)

                if current_question and current_question["id"] == question_id:
                    # Сбрасываем последовательность
                    self.quiz_service.active_quizzes[user_id]["answers"][str(question_id)] = []

                    # Обновляем вопрос
                    await self.show_question(update, context, edit=True)

            elif query.data.startswith("quiz_confirm_"):
                # Подтверждение ответа для вопроса с множественным выбором или последовательностью
                parts = query.data.split("_")
                question_id = int(parts[2])

                current_question = self.quiz_service.get_current_question(user_id)

                if current_question and current_question["id"] == question_id:
                    answer = self.quiz_service.active_quizzes[user_id]["answers"].get(str(question_id), [])

                    # Отправляем ответ
                    result = self.quiz_service.submit_answer(user_id, question_id, answer)

                    if result["success"]:
                        if result["is_completed"]:
                            # Тест завершен
                            await self.show_test_results(update, context, result["result"])
                        else:
                            # Показываем следующий вопрос
                            await self.show_question(update, context)
                    else:
                        await query.edit_message_text(result["message"])

            elif query.data == "quiz_skip":
                # Пропуск текущего вопроса
                result = self.quiz_service.skip_question(user_id)

                if result["success"]:
                    if result["is_completed"]:
                        # Тест завершен
                        await self.show_test_results(update, context, result["result"])
                    else:
                        # Показываем следующий вопрос
                        await self.show_question(update, context)
                else:
                    await query.edit_message_text(result["message"])

        except Exception as e:
            logger.error(f"Error in handle_test_button: {e}")
            await query.edit_message_text(
                "Произошла ошибка при обработке вашего ответа. Пожалуйста, попробуйте еще раз."
            )

    async def show_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
        """Отображение текущего вопроса"""
        query = update.callback_query
        user_id = update.effective_user.id

        # Получаем текущий вопрос
        current_question = self.quiz_service.get_current_question(user_id)

        if not current_question:
            # Если вопросов больше нет, завершаем тест
            result = self.quiz_service.complete_quiz(user_id)
            await self.show_test_results(update, context, result)
            return

        # Форматируем вопрос
        question_num = self.quiz_service.active_quizzes[user_id]["current_question"] + 1
        total_questions = len(self.quiz_service.active_quizzes[user_id]["questions"])

        # Используем соответствующую клавиатуру в зависимости от типа вопроса
        question_type = current_question["question_type"]
        options = current_question["options"]
        question_id = current_question["id"]

        if question_type == "single":
            reply_markup = single_question_keyboard(question_id, options)
        elif question_type == "multiple":
            selected_options = self.quiz_service.active_quizzes[user_id]["answers"].get(str(question_id), [])
            reply_markup = multiple_question_keyboard(question_id, options, selected_options)
        elif question_type == "sequence":
            current_sequence = self.quiz_service.active_quizzes[user_id]["answers"].get(str(question_id), [])
            reply_markup = sequence_question_keyboard(question_id, options, current_sequence)
        else:
            # Fallback
            reply_markup = single_question_keyboard(question_id, options)

        # Форматируем текст вопроса
        question_text = f"*Вопрос {question_num}/{total_questions}*\n\n{current_question['text']}"

        # Добавляем информацию о типе вопроса
        if question_type == "multiple":
            question_text += "\n\n_Выберите все правильные варианты ответов_"
        elif question_type == "sequence":
            question_text += "\n\n_Расположите варианты в правильном порядке_"
            # Если уже есть выбранная последовательность, отображаем её
            if current_sequence:
                sequence_text = "\n".join(
                    [f"{i + 1}. {options[int(opt)]}" for i, opt in enumerate(current_sequence)]
                )
                question_text += f"\n\nТекущая последовательность:\n{sequence_text}"

        # Определяем медиа-файл, если есть
        media_file = None
        if current_question.get("media_url"):
            try:
                from utils.image_utils import get_image_path
                media_file = get_image_path(current_question["media_url"])
            except Exception as e:
                logger.error(f"Error getting media file: {e}")
                media_file = None

        # Отправляем или обновляем сообщение с вопросом
        if edit and query:
            await query.edit_message_text(
                text=question_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            # Если есть медиа-файл, отправляем его
            if media_file:
                with open(media_file, 'rb') as photo:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=photo,
                        caption=question_text,
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
            else:
                if query:
                    await query.edit_message_text(
                        text=question_text,
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=question_text,
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )

    async def show_test_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict) -> None:
        """Отображение результатов теста"""
        query = update.callback_query

        if not result["success"]:
            await query.edit_message_text(result["message"])
            return

        # Форматируем результаты
        correct_count = result["correct_count"]
        total_questions = result["total_questions"]
        percentage = result["percentage"]
        topic_id = result.get("topic_id", 0)

        result_text = f"📊 *Результаты теста*\n\n"
        result_text += f"✅ Правильных ответов: {correct_count} из {total_questions}\n"
        result_text += f"📈 Процент успеха: {percentage}%\n\n"

        # Добавляем эмодзи в зависимости от результата
        if percentage >= 90:
            result_text += "🏆 Отличный результат! Так держать! 🏆"
        elif percentage >= 70:
            result_text += "👍 Хороший результат! Продолжай в том же духе!"
        elif percentage >= 50:
            result_text += "💪 Неплохо, но есть куда расти!"
        else:
            result_text += "📚 Стоит повторить материал и попробовать еще раз."

        # Добавляем информацию о новых достижениях
        if "new_achievements" in result and result["new_achievements"]:
            result_text += "\n\n🏅 *Новые достижения:*\n"
            for achievement in result["new_achievements"]:
                result_text += f"• {achievement['name']} - {achievement['description']} (+{achievement['points']} очков)\n"

        # Используем готовую клавиатуру
        reply_markup = test_results_keyboard(topic_id)

        await query.edit_message_text(
            text=result_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def start_test_with_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE, topic_id: int) -> None:
        """Начать тест по конкретной теме"""
        logger.info(f"Запуск теста для темы {topic_id}")
        try:
            user_id = update.effective_user.id
            quiz_data = self.quiz_service.start_quiz(user_id, topic_id)

            if not quiz_data["success"]:
                await update.callback_query.edit_message_text(quiz_data["message"])
                return

            # Показываем первый вопрос
            await self.show_question(update, context)
        except Exception as e:
            logger.error(f"Error in start_test_with_topic: {e}")
            await update.callback_query.edit_message_text(
                "Произошла ошибка при запуске теста. Пожалуйста, попробуйте еще раз."
            )

    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /stats для отображения статистики ученика"""
        user_id = update.effective_user.id
        logger.info(f"Запрос статистики от пользователя {user_id}")

        # Получаем статистику за разные периоды
        period = context.args[0] if context.args else "all"
        if period not in ["week", "month", "year", "all"]:
            period = "all"

        stats = get_user_stats(user_id, period)

        if not stats["success"]:
            error_message = f"Не удалось получить статистику: {stats['message']}"

            # Отправляем сообщение об ошибке в зависимости от источника вызова
            if update.callback_query:
                await update.callback_query.edit_message_text(error_message)
            else:
                await update.message.reply_text(error_message)
            return

        if not stats["has_data"]:
            # Используем готовую клавиатуру
            reply_markup = stats_period_keyboard()

            # Отправляем сообщение в зависимости от источника вызова
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    stats["message"],
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    stats["message"],
                    reply_markup=reply_markup
                )
            return

        # Форматируем текст статистики
        stats_text = f"📊 *Статистика тестирования*\n"
        stats_text += f"*Период:* {self.get_period_name(period)}\n\n"

        # Общая статистика
        stats_data = stats["stats"]
        stats_text += f"*Общие данные:*\n"
        stats_text += f"• Пройдено тестов: {stats_data['total_tests']}\n"
        stats_text += f"• Средний результат: {stats_data['average_score']}%\n"
        stats_text += f"• Лучший результат: {stats_data['best_result']['score']}% "
        stats_text += f"({stats_data['best_result']['topic']}, {stats_data['best_result']['date']})\n"
        stats_text += f"• Общее время: {self.format_time(stats_data['total_time_spent'])}\n"

        # Динамика по времени
        if "time_stats" in stats and stats["time_stats"]:
            time_stats = stats["time_stats"]
            stats_text += f"\n*Динамика за период:*\n"
            progress_sign = "+" if time_stats["progress"] >= 0 else ""
            stats_text += f"• Изменение результата: {progress_sign}{time_stats['progress']}% "
            stats_text += f"({progress_sign}{time_stats['progress_percentage']}%)\n"

        # Статистика по темам
        if "tests_by_topic" in stats_data and stats_data["tests_by_topic"]:
            stats_text += f"\n*Тесты по темам:*\n"
            for topic, count in stats_data["tests_by_topic"].items():
                stats_text += f"• {topic}: {count} тестов\n"

        # Используем готовую клавиатуру
        reply_markup = stats_period_keyboard()

        # Отправляем текст статистики в зависимости от источника вызова
        if update.callback_query:
            await update.callback_query.edit_message_text(
                stats_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                stats_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

        # Отправляем графики, если они есть
        if "charts" in stats and stats["charts"]:
            charts = stats["charts"]

            if "progress_chart" in charts:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=charts["progress_chart"],
                    caption="📈 Динамика результатов по времени"
                )

            if "topics_chart" in charts:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=charts["topics_chart"],
                    caption="📊 Средний результат по темам"
                )

    async def show_detailed_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показ детального отчета о результатах последнего теста"""
        user_id = update.effective_user.id
        query = update.callback_query

        logger.info(f"Запрос детальных результатов от пользователя {user_id}")

        try:
            # Получаем последний тест пользователя
            with get_session() as session:
                user = session.query(User).filter(User.telegram_id == user_id).first()
                if not user:
                    await query.edit_message_text("Пользователь не найден. Пожалуйста, используйте /start для регистрации.")
                    return

                # Получаем последний завершенный тест
                last_test = session.query(TestResult).filter(
                    TestResult.user_id == user.id
                ).order_by(TestResult.completed_at.desc()).first()

                if not last_test:
                    await query.edit_message_text(
                        "У вас еще нет завершенных тестов. Используйте команду /test для начала тестирования.")
                    return

                # Получаем название темы
                topic_name = "Неизвестная тема"
                if last_test.topic_id:
                    topic = session.query(Topic).get(last_test.topic_id)
                    if topic:
                        topic_name = topic.name

                # Формируем детальный отчет
                detailed_text = f"📋 *Детальный анализ теста*\n\n"
                detailed_text += f"*Тема:* {topic_name}\n"
                detailed_text += f"*Дата:* {last_test.completed_at.strftime('%d.%m.%Y %H:%M')}\n"
                detailed_text += f"*Результат:* {last_test.score} из {last_test.max_score} ({last_test.percentage}%)\n"
                detailed_text += f"*Время:* {self.format_time(last_test.time_spent) if last_test.time_spent else 'Не измерено'}\n\n"

                # Если есть данные о вопросах и ответах, можно их тоже показать
                detailed_text += "*Вопросы и ответы:*\n"
                detailed_text += "К сожалению, данные о конкретных вопросах и ответах недоступны для этого теста."

                # Кнопки для возврата
                keyboard = [
                    [
                        InlineKeyboardButton("🔙 Вернуться к статистике", callback_data="common_stats"),
                        InlineKeyboardButton("📝 Пройти еще тест", callback_data="common_start_test")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Отправляем отчет с использованием edit_message_text
                await query.edit_message_text(
                    detailed_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )

        except Exception as e:
            logger.error(f"Ошибка при показе детальных результатов: {e}")
            logger.error(traceback.format_exc())
            try:
                await query.edit_message_text(
                    "Произошла ошибка при получении детальных результатов. Пожалуйста, попробуйте позже.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data="common_stats")
                    ]])
                )
            except Exception as edit_error:
                logger.error(f"Дополнительная ошибка при обработке сообщения об ошибке: {edit_error}")

    async def show_achievements(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /achievements для отображения достижений ученика"""
        user_id = update.effective_user.id
        logger.info(f"Запрос достижений от пользователя {user_id}")

        # Получаем статистику с достижениями
        stats = get_user_stats(user_id)

        if not stats["success"]:
            error_message = f"Не удалось получить информацию о достижениях: {stats['message']}"

            # Отправляем сообщение об ошибке в зависимости от источника вызова
            if update.callback_query:
                await update.callback_query.edit_message_text(error_message)
            else:
                await update.message.reply_text(error_message)
            return

        achievements = stats.get("achievements", [])
        total_points = stats.get("total_points", 0)

        if not achievements:
            message = "У вас пока нет достижений. Проходите тесты, чтобы получать награды!"

            # Отправляем сообщение в зависимости от источника вызова
            if update.callback_query:
                await update.callback_query.edit_message_text(message)
            else:
                await update.message.reply_text(message)
            return

        # Форматируем текст с достижениями
        achievements_text = f"🏆 *Ваши достижения*\n\n"
        achievements_text += f"*Общее количество баллов:* {total_points}\n\n"

        for achievement in achievements:
            achievements_text += f"🏅 *{achievement['name']}*\n"
            achievements_text += f"_{achievement['description']}_\n"
            achievements_text += f"Получено: {achievement['achieved_at'].strftime('%d.%m.%Y')}\n"
            achievements_text += f"Баллы: +{achievement['points']}\n\n"

        # Используем готовую клавиатуру
        reply_markup = achievements_keyboard()

        # Отправляем текст с достижениями в зависимости от источника вызова
        if update.callback_query:
            await update.callback_query.edit_message_text(
                achievements_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                achievements_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

    async def show_recommendations(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показ персонализированных рекомендаций для ученика"""
        user_id = update.effective_user.id
        query = update.callback_query

        try:
            # Получаем расширенную статистику
            from services.stats_service import get_extended_student_stats
            result = get_extended_student_stats(user_id, "month")

            if not result["success"]:
                if query:
                    await query.edit_message_text(f"Ошибка: {result['message']}")
                else:
                    await update.message.reply_text(f"Ошибка: {result['message']}")
                return

            if not result.get("has_data", False):
                # Используем готовую клавиатуру
                from keyboards.student_kb import student_main_keyboard
                reply_markup = student_main_keyboard()

                message = "У вас пока недостаточно данных для формирования рекомендаций. Пройдите больше тестов, чтобы получить персонализированные советы."

                if query:
                    await query.edit_message_text(message, reply_markup=reply_markup)
                else:
                    await update.message.reply_text(message, reply_markup=reply_markup)
                return

            # Формируем текст с рекомендациями
            recommendations = result.get("recommendations", [])
            weak_topics = result.get("weak_topics", [])
            comparison = result.get("comparison", {})

            text = "🔍 *Персональные рекомендации*\n\n"

            # Общая информация
            text += f"Ваш средний результат: *{result['stats']['average_score']}%*\n"

            if comparison:
                text += f"Средний результат всех учеников: *{comparison['global_avg_score']}%*\n"
                text += f"Ваш результат *{comparison['position']}* среднего на *{abs(comparison['difference'])}%*\n\n"

            # Рекомендации по слабым темам
            if weak_topics:
                text += "*Темы для улучшения:*\n"
                for topic in weak_topics:
                    text += f"• {topic['name']} - {topic['avg_score']}%\n"
                text += "\n"

            # Дополнительные рекомендации
            if recommendations:
                text += "*Рекомендации:*\n"
                for rec in recommendations:
                    text += f"• *{rec['title']}*\n"
                    text += f"  {rec['description']}\n"

                    if rec["type"] == "regularity" and rec.get("items"):
                        text += "  Обратите внимание на периоды без тестирования:\n"
                        for item in rec["items"][:3]:  # Ограничиваем количество
                            text += f"  {item['start']} - {item['end']}\n"

                    text += "\n"

            # Общие советы
            text += "*Общие советы:*\n"
            text += "• Занимайтесь регулярно, хотя бы 3-4 раза в неделю\n"
            text += "• Уделяйте особое внимание темам с низкими результатами\n"
            text += "• Старайтесь улучшать предыдущие результаты, а не только проходить новые тесты\n"

            # Создаем клавиатуру с кнопками действий
            keyboard = []

            # Если есть слабые темы, добавляем кнопку для тренировки
            if weak_topics and len(weak_topics) > 0:
                keyboard.append([
                    InlineKeyboardButton(
                        f"🎯 Тренировать тему: {weak_topics[0]['name']}",
                        callback_data=f"quiz_start_{weak_topics[0]['id']}"
                    )
                ])

            # Добавляем другие кнопки
            keyboard.append([
                InlineKeyboardButton("📊 Статистика", callback_data="common_stats"),
                InlineKeyboardButton("📝 Начать тест", callback_data="common_start_test")
            ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Отправляем сообщение
            if query:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

            # Если есть график прогресса, отправляем его
            if "charts" in result and "progress_chart" in result["charts"]:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=result["charts"]["progress_chart"],
                    caption="📈 Динамика результатов за последний месяц"
                )

        except Exception as e:
            logger.error(f"Error showing recommendations: {e}")
            message = "Произошла ошибка при формировании рекомендаций. Пожалуйста, попробуйте позже."

            if query:
                await query.edit_message_text(message)
            else:
                await update.message.reply_text(message)

    def get_period_name(self, period: str) -> str:
        """Получение читаемого названия периода"""
        periods = {
            "week": "за неделю",
            "month": "за месяц",
            "year": "за год",
            "all": "за всё время"
        }
        return periods.get(period, "за всё время")

    def format_time(self, minutes: int) -> str:
        """Форматирование времени из минут в часы и минуты"""
        hours = minutes // 60
        mins = minutes % 60

        if hours > 0:
            return f"{hours} ч {mins} мин"
        else:
            return f"{mins} мин"