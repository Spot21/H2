import json
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from database.models import User, TestResult, Topic, Achievement
from database.db_manager import get_session


def get_user_stats(user_id: int, period: str = "all") -> Dict[str, Any]:
    """Получение статистики пользователя за указанный период"""
    try:
        with get_session() as session:
            # Находим пользователя
            user = session.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return {"success": False, "message": "Пользователь не найден"}

            # Определяем временной интервал
            now = datetime.utcnow()
            if period == "week":
                start_date = now - timedelta(days=7)
            elif period == "month":
                start_date = now - timedelta(days=30)
            elif period == "year":
                start_date = now - timedelta(days=365)
            else:  # "all"
                start_date = datetime(1970, 1, 1)  # Начало времен для базы данных

            # Получаем результаты тестов за указанный период
            query = session.query(TestResult).filter(TestResult.user_id == user.id)
            if period != "all":
                query = query.filter(TestResult.completed_at >= start_date)
            test_results = query.order_by(TestResult.completed_at).all()

            if not test_results:
                return {
                    "success": True,
                    "has_data": False,
                    "message": f"За выбранный период ({period}) нет результатов тестов"
                }

            # Получаем информацию о темах
            topics = {topic.id: topic.name for topic in session.query(Topic).all()}

            # Собираем данные для статистики
            results_data = []
            for result in test_results:
                results_data.append({
                    "date": result.completed_at,
                    "topic_id": result.topic_id,
                    "topic_name": topics.get(result.topic_id, f"Тема {result.topic_id}"),
                    "score": result.score,
                    "max_score": result.max_score,
                    "percentage": result.percentage,
                    "time_spent": result.time_spent
                })

            df = pd.DataFrame(results_data)

            # Рассчитываем общую статистику
            stats = {
                "total_tests": len(test_results),
                "tests_by_topic": df.groupby("topic_name").size().to_dict(),
                "average_score": round(df["percentage"].mean(), 1),
                "best_result": {
                    "score": round(df["percentage"].max(), 1),
                    "topic": df.loc[df["percentage"].idxmax(), "topic_name"] if not df.empty else None,
                    "date": df.loc[df["percentage"].idxmax(), "date"].strftime("%d.%m.%Y") if not df.empty else None
                },
                "total_time_spent": df["time_spent"].sum() // 60  # В минутах
            }

            # Динамика по времени
            time_stats = {}
            if period != "all" and len(df) > 1:
                # Рассчитываем динамику относительно первого результата
                first_score = df.iloc[0]["percentage"]
                last_score = df.iloc[-1]["percentage"]
                time_stats["progress"] = round(last_score - first_score, 1)
                time_stats["progress_percentage"] = round((time_stats["progress"] / first_score) * 100,
                                                          1) if first_score > 0 else 0

            # Создаем графики
            charts = {}

            # График успеваемости по времени
            if len(df) > 1:
                fig = plt.figure(figsize=(10, 6))
                for topic_id, group in df.groupby("topic_id"):
                    plt.plot(
                        group["date"],
                        group["percentage"],
                        "o-",
                        label=group["topic_name"].iloc[0]
                    )

                plt.title("Динамика успеваемости")
                plt.xlabel("Дата")
                plt.ylabel("Процент правильных ответов")
                plt.grid(True)
                plt.xticks(rotation=45)
                plt.tight_layout()

                if len(df["topic_id"].unique()) > 1:
                    plt.legend()

                img_buf = BytesIO()
                plt.savefig(img_buf, format='png')
                img_buf.seek(0)
                plt.close(fig)  # Явно закрываем фигуру

                charts["progress_chart"] = img_buf

            # График результатов по темам
            if len(df["topic_id"].unique()) > 1:
                topic_avg = df.groupby("topic_name")["percentage"].mean().sort_values(ascending=False)

                fig = plt.figure(figsize=(10, 6))
                bars = plt.bar(topic_avg.index, topic_avg.values)

                # Добавляем значения над столбцами
                for bar in bars:
                    height = bar.get_height()
                    plt.text(
                        bar.get_x() + bar.get_width() / 2.,
                        height + 1,
                        f'{height:.1f}%',
                        ha='center',
                        va='bottom'
                    )

                plt.title("Средний результат по темам")
                plt.ylabel("Процент правильных ответов")
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                plt.ylim(0, 105)  # Чтобы поместились значения над столбцами

                img_buf = BytesIO()
                plt.savefig(img_buf, format='png')
                img_buf.seek(0)
                plt.close(fig)  # Добавляем закрытие конкретной фигуры

                charts["topics_chart"] = img_buf

            # Получаем достижения пользователя
            achievements = [
                {
                    "name": a.name,
                    "description": a.description,
                    "achieved_at": a.achieved_at,
                    "points": a.points
                }
                for a in user.achievements
            ]

            # Общее количество баллов
            total_points = sum(a["points"] for a in achievements)

            return {
                "success": True,
                "has_data": True,
                "stats": stats,
                "time_stats": time_stats,
                "charts": charts,
                "achievements": achievements,
                "total_points": total_points
            }

    except Exception as e:
        return {"success": False, "message": f"Ошибка при получении статистики: {str(e)}"}


def update_user_stats(user_id: int) -> Dict[str, Any]:
    """Обновление общей статистики пользователя после прохождения теста"""
    try:
        with get_session() as session:
            user = session.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return {"success": False, "message": "Пользователь не найден"}

            # Обновляем время последней активности
            user.last_active = datetime.utcnow()

            # Можно добавить дополнительные обновления статистики,
            # например, общее время в системе, количество пройденных тестов и т.д.

            session.commit()
            return {"success": True}

    except Exception as e:
        return {"success": False, "message": f"Ошибка при обновлении статистики: {str(e)}"}


def generate_leaderboard(period: str = "week", limit: int = 10) -> Dict[str, Any]:
    """Генерация таблицы лидеров по результатам тестов"""
    try:
        with get_session() as session:
            # Определяем временной интервал
            now = datetime.utcnow()
            if period == "week":
                start_date = now - timedelta(days=7)
            elif period == "month":
                start_date = now - timedelta(days=30)
            elif period == "year":
                start_date = now - timedelta(days=365)
            else:  # "all"
                start_date = datetime(1970, 1, 1)

            # Получаем результаты тестов за указанный период
            test_results = (
                session.query(TestResult)
                .filter(TestResult.completed_at >= start_date)
                .all()
            )

            if not test_results:
                return {
                    "success": True,
                    "has_data": False,
                    "message": f"За выбранный период ({period}) нет результатов тестов"
                }

            # Группируем результаты по пользователям
            user_results = {}
            for result in test_results:
                user_id = result.user_id
                if user_id not in user_results:
                    user_results[user_id] = []
                user_results[user_id].append(result)

            # Рассчитываем средний балл для каждого пользователя
            leaderboard_data = []
            for user_id, results in user_results.items():
                user = session.query(User).get(user_id)
                if not user or user.role != "student":
                    continue

                if results:  # Проверка на пустой список
                    avg_score = round(sum(r.percentage for r in results) / len(results), 2)
                else:
                    avg_score = 0
                tests_count = len(results)

                leaderboard_data.append({
                    "user_id": user_id,
                    "username": user.username,
                    "full_name": user.full_name,
                    "avg_score": avg_score,
                    "tests_count": tests_count
                })

            # Сортируем по среднему баллу
            leaderboard_data.sort(key=lambda x: x["avg_score"], reverse=True)

            # Ограничиваем количество записей
            leaderboard_data = leaderboard_data[:limit]

            # Добавляем место в рейтинге
            for i, item in enumerate(leaderboard_data):
                item["rank"] = i + 1

            return {
                "success": True,
                "has_data": True,
                "period": period,
                "leaderboard": leaderboard_data
            }
    except Exception as e:
        return {"success": False, "message": f"Ошибка при создании таблицы лидеров: {str(e)}"}


def generate_topic_analytics() -> Dict[str, Any]:
    """Анализ результатов по разным темам для выявления сложных/простых тем"""
    try:
        with get_session() as session:
            # Получаем все результаты тестов
            test_results = session.query(TestResult).all()

            if not test_results:
                return {
                    "success": True,
                    "has_data": False,
                    "message": "Нет данных для анализа"
                }

            # Получаем информацию о темах
            topics = {topic.id: topic.name for topic in session.query(Topic).all()}

            # Группируем результаты по темам
            topic_results = {}
            for result in test_results:
                topic_id = result.topic_id
                if topic_id not in topic_results:
                    topic_results[topic_id] = []
                topic_results[topic_id].append(result)

            # Рассчитываем статистику по каждой теме
            topic_stats = []
            for topic_id, results in topic_results.items():
                avg_score = sum(r.percentage for r in results) / len(results)
                tests_count = len(results)

                topic_stats.append({
                    "topic_id": topic_id,
                    "topic_name": topics.get(topic_id, f"Тема {topic_id}"),
                    "avg_score": round(avg_score, 1),
                    "tests_count": tests_count
                })

            # Сортируем по среднему баллу (от самого низкого к самому высокому)
            topic_stats.sort(key=lambda x: x["avg_score"])

            # Создаем график сложности тем
            fig = plt.figure(figsize=(12, 6))

            topic_names = [t["topic_name"] for t in topic_stats]
            avg_scores = [t["avg_score"] for t in topic_stats]

            # Определяем цвета на основе сложности (красный - сложные, зеленый - простые)
            colors = ['#FF9999' if score < 60 else
                      '#FFCC99' if score < 75 else
                      '#CCFF99' if score < 90 else
                      '#99FF99' for score in avg_scores]

            bars = plt.bar(topic_names, avg_scores, color=colors)

            # Добавляем значения над столбцами
            for bar in bars:
                height = bar.get_height()
                plt.text(
                    bar.get_x() + bar.get_width() / 2.,
                    height + 1,
                    f'{height:.1f}%',
                    ha='center',
                    va='bottom'
                )

            plt.title("Средний результат по темам (от сложных к простым)")
            plt.ylabel("Средний процент правильных ответов")
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.ylim(0, 105)  # Чтобы поместились значения над столбцами

            img_buf = BytesIO()
            plt.savefig(img_buf, format='png')
            img_buf.seek(0)
            plt.close(fig)  # Закрываем конкретную фигуру

            return {
                "success": True,
                "has_data": True,
                "topic_stats": topic_stats,
                "chart": img_buf
            }

    except Exception as e:
        return {"success": False, "message": f"Ошибка при анализе тем: {str(e)}"}
