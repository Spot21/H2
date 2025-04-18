import logging
import os
import traceback
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from config import ADMINS

from config import DB_ENGINE, DATA_DIR
from database.models import Base

# Настройка логирования
logger = logging.getLogger(__name__)

# Проверяем тип базы данных (SQLite или PostgreSQL)
is_sqlite = DB_ENGINE.startswith('sqlite:///')
is_postgres = DB_ENGINE.startswith('postgresql://')

# Для SQLite создаем директорию, если она не существует
if is_sqlite:
    sqlite_path = DB_ENGINE.replace('sqlite:///', '')
    os.makedirs(os.path.dirname(os.path.abspath(sqlite_path)), exist_ok=True)

    # Создаем движок базы данных с настройками для SQLite
    engine = create_engine(
        DB_ENGINE,
        connect_args={"check_same_thread": False},  # Только для SQLite
        echo=False  # Установите True для отладки SQL-запросов
    )
# Для PostgreSQL или других СУБД
else:
    engine = create_engine(
        DB_ENGINE,
        echo=False,  # Установите True для отладки SQL-запросов
        pool_size=10,  # Увеличиваем размер пула соединений
        max_overflow=20,  # Увеличиваем максимальное количество дополнительных соединений
        pool_timeout=60,  # Увеличиваем тайм-аут для получения соединения из пула
        pool_recycle=900,  # Пересоздание соединений старше 15 минут
        pool_pre_ping=True  # Добавляем проверку соединения перед использованием
    )

# Создаем фабрику сессий
Session = scoped_session(sessionmaker(bind=engine, autoflush=True, autocommit=False))


def init_db():
    """Инициализация базы данных"""
    try:
        # Проверка и настройка в зависимости от типа базы данных
        if is_sqlite:
            # Для SQLite проверяем права на запись
            db_path = DB_ENGINE.replace('sqlite:///', '')
            db_dir = os.path.dirname(os.path.abspath(db_path))
            test_file = os.path.join(db_dir, 'test_write.tmp')
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                logger.info(f"Проверка прав на запись в директорию {db_dir} успешна")
            except Exception as e:
                logger.error(f"Ошибка при проверке прав на запись в директорию {db_dir}: {e}")

            logger.info(f"База данных SQLite будет создана по пути: {db_path}")
        elif is_postgres:
            # Для PostgreSQL проверяем соединение
            logger.info(f"Используется база данных PostgreSQL: {DB_ENGINE}")
            try:
                with engine.connect() as conn:
                    logger.info("Соединение с PostgreSQL установлено успешно")
            except Exception as e:
                logger.error(f"Ошибка при подключении к PostgreSQL: {e}")
                raise
        else:
            logger.info(f"Используется база данных: {DB_ENGINE}")

        # Создаем все таблицы
        Base.metadata.create_all(engine)
        logger.info("Таблицы в базе данных созданы успешно")

        # Проверяем, есть ли уже данные в базе
        with get_session() as session:
            from database.models import User
            user_count = session.query(User).count()
            logger.info(f"Количество пользователей в базе: {user_count}")

            # Если база пуста, добавляем начальные данные
            if user_count == 0:
                add_default_data()
                logger.info("Начальные данные добавлены успешно")
            else:
                logger.info("База данных уже содержит данные, пропускаем добавление начальных данных")

    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        logger.error(traceback.format_exc())
        raise


@contextmanager
def get_session():
    """Контекстный менеджер для работы с сессией базы данных"""
    session = Session()
    try:
        logger.debug("Открыта новая сессия базы данных")
        yield session
        if session.is_active:  # Проверка, что сессия все еще активна
            session.commit()
            logger.debug("Сессия успешно закрыта с commit")
    except Exception as e:
        if session.is_active:  # Проверка, что сессия все еще активна
            session.rollback()
            logger.error(f"Ошибка в сессии базы данных, выполнен rollback: {e}")
        logger.error(traceback.format_exc())
        raise
    finally:
        session.close()
        logger.debug("Сессия закрыта в блоке finally")


def add_default_data():
    """Добавление начальных данных в базу данных"""
    from database.models import User, Topic

    try:
        with get_session() as session:
            # Проверяем, есть ли уже администратор
            admin_exists = session.query(User).filter(User.role == "admin").first() is not None

            if not admin_exists:
                # Добавляем администратора (ID нужно заменить на реальный)
                # Если есть хотя бы один администратор в конфигурации, используем его
                if ADMINS:
                    try:
                        admin_id = int(ADMINS[0])
                        admin = User(
                            telegram_id=admin_id,
                            username="admin",
                            full_name="Admin",
                            role="admin"
                        )
                        session.add(admin)
                        logger.info(f"Default admin user added with ID: {admin_id}")
                    except (ValueError, IndexError) as e:
                        logger.error(f"Error adding default admin: {e}")
                else:
                    logger.warning("No admin IDs found in configuration. Skipping admin creation.")

            # Проверяем, есть ли уже темы
            topics_exist = session.query(Topic).first() is not None

            if not topics_exist:
                # Добавляем несколько начальных тем
                topics = [
                    Topic(name="Древняя Русь IX-XII вв.",
                          description="Вопросы по истории Древней Руси в период IX-XII веков"),

                ]

                session.add_all(topics)
                logger.info("Default topics added")

            session.commit()
            logger.info("Default data added successfully")

    except Exception as e:
        logger.error(f"Error adding default data: {e}")
        raise