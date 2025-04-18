import logging
from database.db_manager import get_session

logger = logging.getLogger(__name__)


def is_postgresql():
    """Проверяет, используется ли PostgreSQL в качестве СУБД"""
    try:
        with get_session() as session:
            from sqlalchemy import inspect
            connection = session.connection()
            inspector = inspect(connection)
            dialect_name = inspector.engine.dialect.name.lower()
            return dialect_name == 'postgresql'
    except Exception as e:
        logger.error(f"Ошибка при определении диалекта БД: {e}")
        return False


def adapt_boolean_comparison(value, for_postgres=None):
    """
    Адаптирует значение для сравнения с boolean-полем в зависимости от используемой СУБД

    Args:
        value (bool): Булево значение для сравнения
        for_postgres (bool, optional): Принудительно использовать синтаксис для PostgreSQL
                                     Если None, определяет автоматически

    Returns:
        str or bool: Значение, адаптированное для текущей СУБД
    """
    if for_postgres is None:
        for_postgres = is_postgresql()

    if for_postgres:
        # Для PostgreSQL используем TRUE/FALSE
        return "TRUE" if value else "FALSE"
    else:
        # Для SQLite и других используем 1/0
        return 1 if value else 0


def get_db_type(column_name, table_name):
    """
    Получает тип столбца в базе данных

    Args:
        column_name (str): Имя столбца
        table_name (str): Имя таблицы

    Returns:
        str: Тип данных столбца
    """
    try:
        with get_session() as session:
            from sqlalchemy import inspect, text

            inspector = inspect(session.bind)
            columns = inspector.get_columns(table_name)

            for column in columns:
                if column['name'] == column_name:
                    return str(column['type'])

            return "unknown"
    except Exception as e:
        logger.error(f"Ошибка при получении типа столбца {column_name}: {e}")
        return "unknown"
