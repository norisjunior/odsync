#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior
set -eu
cd "$(dirname "$0")"
python -m py_compile auth.py cli.py graph.py state.py sync.py
python - <<'PY'
import base64
import copy
import hashlib
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import auth
import cli
import graph
import state
import sync


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 29, 12)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.mapping = dict(name='databases', local_dir=str(self.root),
                            remote_dir='MyBackup/databases', recursive=False,
                            include=['*.sql.gz'], retention_days=3,
                            retention_patterns=['{date}-app.sql.gz', '{date}-auth.sql.gz'])
        self.config = dict(tenant_id='test', client_id='test', ignore=[], mapping=[self.mapping])
        self.remote = {}
        for target, attr, value in [(state, 'DB_PATH', self.root / 'state.db'),
                                    (sync, 'datetime', Clock)]:
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.connection = state.connect()
        self.addCleanup(self.connection.close)
        for day in range(24, 30):
            for db in ('app', 'auth'):
                name = self.name(day, db)
                path = self.root / name
                path.write_bytes(b'backup-test')
                info = path.stat()
                state.observe(self.connection, self.key(name), info.st_size, info.st_mtime_ns)
                state.mark_uploaded(self.connection, self.key(name), info.st_size,
                                    info.st_mtime_ns, hashlib.sha256(b'backup-test').hexdigest())
                state.save_session(self.connection, str(path), info.st_size,
                                   info.st_mtime_ns, 'legacy', 'fake-url')
                self.remote[name] = dict(id=name, eTag='version-1', size=info.st_size,
                    file={'hashes': {'sha1Hash': hashlib.sha1(b'backup-test').hexdigest()}})
        for name in ['notes.txt', '20260230-app.sql.gz', '20261001-other.sql.gz',
                     'subdir/' + self.name(24)]:
            self.remote[name] = dict(id=name, eTag='v1', size=100, file={})
        self.deleted = []
        self.logs = []

        def delete(item, *args):
            self.deleted.append(item['id'])
            self.remote.pop(item['id'])

        for target, attr, value in [
            (graph, 'remote_tree', Mock(side_effect=lambda *a, **k: copy.deepcopy(self.remote))),
            (graph, 'delete_file', Mock(side_effect=delete)),
            (graph, 'upload', Mock()), (graph, 'remote_file', Mock(return_value=None)),
            (sync, 'log', self.logs.append),
        ]:
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def name(self, day, db='app'):
        return f'202610{day:02d}-{db}.sql.gz'

    def key(self, name):
        return self.mapping['remote_dir'] + '/' + name

    def test_retention_no_reupload_and_local_preserved(self):
        sync.scan(self.config)
        self.assertEqual(set(self.deleted), {self.name(d, b)
                         for d in range(24, 27) for b in ('app', 'auth')})
        self.assertEqual(len(self.remote), 10)
        self.assertEqual(state.status(self.connection)[0], 6)
        for name in self.deleted:
            self.assertTrue((self.root / name).exists())
            self.assertIsNone(state.observation(self.connection, self.key(name)))
            self.assertIsNone(state.session(self.connection, str(self.root / name)))
        sync.scan(self.config)
        graph.upload.assert_not_called()
        self.assertEqual(len(self.deleted), 6)

    def test_retention_disabled(self):
        self.mapping.pop('retention_days')
        sync.scan(self.config)
        graph.remote_tree.assert_not_called()
        self.assertEqual(self.deleted, [])

    def test_variable_days(self):
        self.mapping['retention_days'] = 2
        sync.scan(self.config)
        self.assertEqual(len(self.deleted), 8)

    def test_missing_recent_pair(self):
        self.remote.pop(self.name(29, 'auth'))
        sync.scan(self.config)
        self.assertEqual(self.deleted, [])
        self.assertTrue(any('adiada' in x for x in self.logs))

    def test_zero_byte_recent(self):
        self.remote[self.name(29)]['size'] = 0
        sync.scan(self.config)
        self.assertEqual(self.deleted, [])

    def test_pending_recent_upload(self):
        name = self.name(29)
        state.forget_file(self.connection, self.key(name), str(self.root / name))
        sync.scan(self.config)
        graph.upload.assert_not_called()
        self.assertEqual(self.deleted, [])

    def test_upload_failure_blocks_deletion(self):
        self.connection.execute('DELETE FROM uploaded WHERE path=?', (self.key(self.name(29)),))
        self.connection.commit()
        graph.upload.side_effect = RuntimeError('upload failed')
        with self.assertRaises(RuntimeError):
            sync.scan(self.config)
        graph.delete_file.assert_not_called()

    def test_changed_local_blocks(self):
        (self.root / self.name(29)).write_bytes(b'changed-backup')
        sync.scan(self.config)
        self.assertEqual(self.deleted, [])

    def test_delete_failure_preserves_state(self):
        graph.delete_file.side_effect = RuntimeError('HTTP 412')
        with self.assertRaises(RuntimeError):
            sync.scan(self.config)
        self.assertEqual(state.status(self.connection)[0], 12)

    def test_plan_readonly(self):
        before = state.status(self.connection)
        with patch.object(state, 'connect', side_effect=AssertionError('SQLite write in plan')):
            sync.plan(self.config)
        self.assertEqual(before, state.status(self.connection))
        graph.delete_file.assert_not_called()
        self.assertEqual(sum('candidato' in x for x in self.logs), 6)

    def test_reconcile_never_deletes(self):
        sync.reconcile(self.config)
        graph.delete_file.assert_not_called()

    def test_calendar_and_literal_scope(self):
        self.assertEqual(sync._retention_cutoff(self.mapping, date(2027, 1, 1)), date(2026, 12, 30))
        self.assertEqual(sync._retention_cutoff(self.mapping, date(2028, 3, 1)), date(2028, 2, 28))
        for name in ['20260230-app.sql.gz', '../' + self.name(24),
                     self.name(24) + '.tmp', '20261024-appXsqlXgz']:
            self.assertIsNone(sync._dump_date(name, self.mapping))


class GraphTests(unittest.TestCase):
    def test_conditional_delete(self):
        with patch.object(graph, '_request', return_value=Mock(ok=True, status_code=204)) as request:
            with patch.object(graph.auth, 'get_access_token', return_value='test'):
                graph.delete_file(dict(id='a/b', eTag='tag', file={}), 't', 'c')
        self.assertEqual(request.call_args.args, ('DELETE', graph.BASE + '/items/a%2Fb'))
        self.assertEqual(request.call_args.kwargs['headers'], {'If-Match': 'tag'})

    def test_delete_errors(self):
        for code in (403, 412):
            with patch.object(graph, '_request', return_value=Mock(ok=False, status_code=code, text='error')):
                with patch.object(graph.auth, 'get_access_token', return_value='test'):
                    with self.assertRaises(RuntimeError):
                        graph.delete_file(dict(id='a', eTag='tag', file={}), 't', 'c')

    def test_missing_etag_never_deletes(self):
        with patch.object(graph, '_request') as request:
            with self.assertRaises(RuntimeError):
                graph.delete_file(dict(id='a', file={}), 't', 'c')
            request.assert_not_called()

    def test_resume_session_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / 'large.bin'
            path.write_bytes(b'x' * (graph.CHUNK_SIZE + 10))
            info = path.stat()
            with patch.object(state, 'DB_PATH', root / 'state.db'):
                connection = state.connect()
            try:
                state.save_session(connection, str(path), info.st_size, info.st_mtime_ns, 'hash', 'saved-url')
                responses = [Mock(ok=True, status_code=200, json=lambda: {'nextExpectedRanges': [f'{graph.CHUNK_SIZE}-']}),
                             Mock(ok=True, status_code=201)]
                with patch.object(graph, '_request', side_effect=responses) as request:
                    with patch.object(graph, 'ensure_folders'), patch.object(auth, 'get_access_token', return_value='test'):
                        graph.upload(path, 'folder/large.bin', info.st_size, info.st_mtime_ns,
                                     'hash', connection, 't', 'c')
                self.assertEqual([call.args[0] for call in request.call_args_list], ['GET', 'PUT'])
                self.assertEqual(request.call_args.kwargs['data'], b'x' * 10)
                self.assertIsNone(state.session(connection, str(path)))
            finally:
                connection.close()

    def test_network_error_does_not_leak_upload_url(self):
        with patch.object(graph.requests, 'request', side_effect=graph.requests.RequestException('https://secret-upload-url/?token=secret')):
            with patch.object(graph, 'RETRIES', 1):
                with self.assertRaises(RuntimeError) as error:
                    graph._request('GET', 'https://secret-upload-url')
        self.assertNotIn('secret', str(error.exception))


class ConfigTests(unittest.TestCase):
    def test_example(self):
        with patch.object(cli, 'CONFIG_PATH', Path('config.example.toml')):
            config = cli.load_config()
        self.assertEqual(config['mapping'][1]['retention_days'], 0)

    def test_validation(self):
        base = dict(tenant_id='t', client_id='c', mapping=[dict(name='custom',
                    local_dir='/backup', remote_dir='Custom/databases', recursive=False,
                    retention_days=3, retention_patterns=['{date}-app.sql.gz'])])
        invalid = [{'remote_dir': '/'}, {'remote_dir': '../files'}, {'recursive': True},
                   {'retention_days': True}, {'retention_days': -1}, {'retention_days': 366},
                   {'retention_patterns': []}, {'retention_patterns': ['{date}-*.sql.gz']},
                   {'retention_patterns': ['{date}/app.sql.gz']},
                   {'retention_patterns': ['{date}-{date}.sql.gz']},
                   {'retention_patterns': ['{date}-app.sql.gz', '{date}-app.sql.gz']},
                   {'include': ['*.txt']}]
        for change in [{}, {'retention_days': 0}, {'retention_days': 7}] + invalid:
            config = copy.deepcopy(base)
            config['mapping'][0].update(change)
            with patch.object(cli.tomllib, 'load', return_value=config):
                with patch.object(cli, 'CONFIG_PATH', Path('README.md')):
                    if change in invalid:
                        with self.assertRaises(ValueError):
                            cli.load_config()
                    else:
                        cli.load_config()

    def test_overlapping_destination_rejected(self):
        config = dict(tenant_id='t', client_id='c', mapping=[
            dict(name='db', local_dir='/db', remote_dir='Backup/db', recursive=False,
                 retention_days=3, retention_patterns=['{date}-app.sql.gz']),
            dict(name='files', local_dir='/files', remote_dir='backup')])
        with patch.object(cli.tomllib, 'load', return_value=config), patch.object(cli, 'CONFIG_PATH', Path('README.md')):
            with self.assertRaises(ValueError):
                cli.load_config()


class AuthTests(unittest.TestCase):
    def test_pkce(self):
        verifier, challenge = auth._pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(challenge, base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii'))


unittest.main(verbosity=2)
PY
