"""Portable empty-database bootstrap contracts; all connections are inert."""

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql

from aipulse.models import Base
from aipulse.storage import init_dev_database as bootstrap


CHAIN = ('20261014_0001', '20261016_0002', '20261021_0003', '20261021_0004')


@pytest.fixture
def offline_dir():
    # Flat files avoid relying on a particular developer's scratch directory.
    return Path(os.environ.get('AIPULSE_OFFLINE_TEST_DIR', tempfile.gettempdir()))


def test_four_reviewed_revisions_work_without_git_or_personal_interpreter(monkeypatch):
    monkeypatch.setattr(bootstrap, 'ROOT', Path(bootstrap.__file__).resolve().parents[3])
    # No Git command should be required by the public file snapshot.
    monkeypatch.setattr(subprocess, 'run', lambda *_a, **_k: pytest.fail('Git dependency'))
    config = bootstrap.reviewed_config()
    assert config is not None
    assert bootstrap.FINAL_REVISION == CHAIN[-1]
    assert len(bootstrap.REVIEWED) == 4


def test_same_connection_runs_each_fixed_revision_once_and_stops_on_failure(monkeypatch, offline_dir):
    connection = object()
    calls = []
    monkeypatch.setattr(bootstrap, 'verify_identity_and_empty', lambda conn: calls.append(('empty', conn)))
    monkeypatch.setattr(bootstrap, 'reviewed_config', lambda: SimpleNamespace(attributes={}))
    monkeypatch.setattr(bootstrap, 'verify_constraints', lambda conn: None)
    monkeypatch.setattr(bootstrap, 'verify_schema', lambda conn, revision: calls.append(('schema', revision)))

    def upgrade(config, revision):
        assert config.attributes['connection'] is connection
        calls.append(('upgrade', revision))
        if revision == CHAIN[2]:
            raise RuntimeError('Synthetic DDL failure')

    monkeypatch.setattr(bootstrap.command, 'upgrade', upgrade)
    with pytest.raises(RuntimeError, match='Synthetic DDL failure'):
        bootstrap.upgrade_once(connection, {'path': str(offline_dir/'synthetic.sql')})
    assert [v for k,v in calls if k == 'upgrade'] == list(CHAIN[:3])
    assert [v for k,v in calls if k == 'schema'] == list(CHAIN[:2])


def test_nonempty_database_never_reaches_alembic(monkeypatch, offline_dir):
    def reject(_conn):
        raise bootstrap.UnsafeDevelopmentMigration('Development database is not empty')
    monkeypatch.setattr(bootstrap, 'verify_identity_and_empty', reject)
    monkeypatch.setattr(bootstrap.command, 'upgrade', lambda *_: pytest.fail('DDL reached'))
    with pytest.raises(bootstrap.UnsafeDevelopmentMigration, match='not empty'):
        bootstrap.upgrade_once(object(), {'path': str(offline_dir/'synthetic.sql')})


class EmptyConnection:
    def __init__(self, *, identity=('aipulse_db','aipulse_migrate@localhost','8.0.44',3306), tables=()):
        self.identity, self.tables = identity, tables
    def execute(self, statement, params=None):
        sql = str(statement)
        if 'CURRENT_USER()' in sql:
            return SimpleNamespace(one=lambda: self.identity)
        rows = [('utf8mb4','utf8mb4_0900_ai_ci')] if 'SCHEMATA' in sql else self.tables
        return SimpleNamespace(all=lambda: rows)


@pytest.mark.parametrize('identity', [
    ('aipulse_test_db','aipulse_migrate@localhost','8.0.44',3306),
    ('aipulse_db','aipulse_app@localhost','8.0.44',3306),
])
def test_wrong_database_or_runtime_account_is_rejected(identity):
    with pytest.raises(bootstrap.UnsafeDevelopmentMigration):
        bootstrap.verify_identity_and_empty(EmptyConnection(identity=identity))


def test_even_a_version_table_means_database_is_not_empty():
    with pytest.raises(bootstrap.UnsafeDevelopmentMigration, match='not empty'):
        bootstrap.verify_identity_and_empty(EmptyConnection(tables=[('alembic_version',)]))


@pytest.mark.parametrize('revision', CHAIN)
def test_schema_checks_all_columns_at_each_stage(revision):
    expected = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if revision == CHAIN[0] and column.name in {'score_reason','rule_version'}:
                continue
            if revision in CHAIN[:2] and table.name == 'news_event' and column.name.startswith('summary_'):
                continue
            if revision != CHAIN[3] and (table.name,column.name) in {('research_task','result_json'),('research_evidence','metadata_json')}:
                continue
            type_name=str(column.type.compile(dialect=mysql.dialect())).lower()
            type_name={'bool':'tinyint(1)', 'boolean':'tinyint(1)', 'integer':'int'}.get(type_name,type_name)
            expected.append((table.name,column.name,type_name,'YES' if column.nullable else 'NO'))
    expected.append(('alembic_version','version_num','varchar(32)','NO'))

    class Connection:
        def execute(self, statement, params=None):
            sql = str(statement)
            if 'information_schema.TABLES' in sql:
                rows = [(name,'InnoDB','utf8mb4_0900_ai_ci') for name in bootstrap.ALL_TABLES]
            elif 'version_num FROM' in sql:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [revision]))
            elif 'information_schema.COLUMNS' in sql:
                rows = expected
            else:
                raise AssertionError(sql)
            return SimpleNamespace(all=lambda: rows)
        def scalar(self, statement): return 0

    bootstrap.verify_schema(Connection(),revision)


def test_empty_backup_requires_matching_sha256_and_no_business_sql(offline_dir):
    from uuid import uuid4
    backup=offline_dir/f'empty_{uuid4().hex}.sql'
    body='-- MySQL dump\n-- Current Database: `aipulse_db`\n-- '+('x'*140)+'\n-- Dump completed on 2026-10-23\n'
    backup.write_text(body,encoding='utf-8')
    digest=hashlib.sha256(backup.read_bytes()).hexdigest()
    assert bootstrap.verify_backup(backup,digest)['sha256']==digest
    with pytest.raises(bootstrap.UnsafeDevelopmentMigration):
        bootstrap.verify_backup(backup,'0'*64)
    backup.write_text(body+'INSERT INTO `news_event` VALUES (1);',encoding='utf-8')
    with pytest.raises(bootstrap.UnsafeDevelopmentMigration):
        bootstrap.verify_backup(backup,hashlib.sha256(backup.read_bytes()).hexdigest())
