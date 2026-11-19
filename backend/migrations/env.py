"""Alembic environment: uses the same database settings as the app (backend/.env).

Set MIGRATE_DB_USER / MIGRATE_DB_PASSWORD to run migrations with an account that may change the schema
(the application account kz_app only has data rights; see docs/DEVELOPER_GUIDE.md "Database accounts")."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from kz.config import Config
from kz.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def url():
    user = os.getenv('MIGRATE_DB_USER')
    if user:
        Config.DB_USER = user
        Config.DB_PASSWORD = os.getenv('MIGRATE_DB_PASSWORD', '')
    name = os.getenv('MIGRATE_DB_NAME') or Config.DB_NAME
    return Config.db_url(name)


def include_object(obj, name, type_, reflected, compare_to):
    # alembic_version is Alembic's own table
    return not (type_ == 'table' and name == 'alembic_version')


def run_migrations_offline():
    context.configure(url=url(), target_metadata=target_metadata, literal_binds=True, compare_type=True,
                      include_object=include_object, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(url(), poolclass=pool.NullPool, connect_args=Config.connect_args())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
