from sqlalchemy import create_engine, inspect
from app.schema_updates import allow_unconfigured_relay_target


def test_optional_target_migration_preserves_rows_and_references(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    with engine.begin() as c:
        c.exec_driver_sql('PRAGMA foreign_keys=ON')
        c.exec_driver_sql('CREATE TABLE accounts (id INTEGER PRIMARY KEY)')
        c.exec_driver_sql('CREATE TABLE relay_tasks (id INTEGER PRIMARY KEY, account_a INTEGER REFERENCES accounts(id), relay_chat BIGINT NOT NULL, enabled BOOLEAN NOT NULL)')
        c.exec_driver_sql('CREATE TABLE relay_jobs (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES relay_tasks(id), status TEXT)')
        c.exec_driver_sql('INSERT INTO accounts VALUES (1)')
        c.exec_driver_sql('INSERT INTO relay_tasks VALUES (2,1,-1002,1)')
        c.exec_driver_sql("INSERT INTO relay_jobs VALUES (3,2,'pending')")
    allow_unconfigured_relay_target(engine)
    allow_unconfigured_relay_target(engine)
    with engine.begin() as c:
        assert c.exec_driver_sql('SELECT * FROM relay_tasks').one() == (2,1,-1002,1)
        assert c.exec_driver_sql('SELECT * FROM relay_jobs').one() == (3,2,'pending')
        assert c.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
        assert not c.exec_driver_sql('PRAGMA foreign_key_check').all()
        c.exec_driver_sql('INSERT INTO relay_tasks VALUES (4,1,NULL,0)')
        assert inspect(c).get_foreign_keys('relay_tasks')[0]['referred_table'] == 'accounts'
    engine.dispose()
