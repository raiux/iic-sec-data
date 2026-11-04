"""Temporary private storage and synthetic reports only; no network or real filings."""
import base64
import copy
import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import annual_history_store as storage
from scripts.annual_history import import_report, issuer_views, new_history, serialize, validate_history
from scripts.offline_pipeline import PublicExportBlocked, canonical_json
from scripts.run_offline_tests import deny_network
from tests.test_annual_history import CIK, LEI, binary, esef_report, report
from tests.test_normalize_sec import ROW


def lock_worker(path, ready, release):
    sys.addaudithook(deny_network)
    with storage.HistoryStore(path)._locked():
        ready.set()
        if not release.wait(20):
            raise RuntimeError('test release timeout')


def update_worker(path, item, revision, start, ready, results):
    sys.addaudithook(deny_network)
    ready.put(True)
    if not start.wait(20):
        raise RuntimeError('test start timeout')
    try:
        storage.HistoryStore(path).import_report(item, expected_revision=revision)
        results.put('COMMITTED')
    except (storage.StoreBusy, storage.RevisionConflict) as exc:
        results.put(type(exc).__name__)


def crash_worker(path, item, phase):
    sys.addaudithook(deny_network)
    if phase == 'before_replace':
        storage.os.replace = lambda *args: os._exit(73)
    elif phase == 'before_write':
        storage.tempfile.NamedTemporaryFile = lambda **kwargs: os._exit(73)
    else:
        storage._sync_directory = lambda *args: os._exit(73)
    storage.HistoryStore(path).import_report(item)


class AnnualHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / 'history.json'
        self.store = storage.HistoryStore(self.path)

    def initialized(self):
        self.store.initialize()
        return self.store.import_report(report(2024))

    def assert_private(self, snapshot):
        self.assertFalse(snapshot.history['valuation_usable'])
        self.assertEqual(snapshot.history['visibility'], 'PRIVATE')
        for item in snapshot.history['reports']:
            self.assertFalse(item['controls']['valuation_usable'])
            self.assertEqual(item['controls']['source_verification'], 'NOT_PERFORMED')
        for identity in [CIK, LEI]:
            views = issuer_views(snapshot.history, identity)
            self.assertIsNone(views['latest_admissible']['period'])
            self.assertIsNone(views['last_valid_output'])
        with self.assertRaises(PublicExportBlocked):
            serialize(snapshot.history, audience='public')

    def test_explicit_initialize_missing_load_and_existing_refusal(self):
        with self.assertRaises(FileNotFoundError):
            self.store.load()
        with self.assertRaises(FileNotFoundError):
            self.store.import_report(report())
        self.assertFalse(self.path.exists())
        first = self.store.initialize()
        self.assertEqual(first.generation, 1)
        with self.assertRaisesRegex(storage.StoreError, 'ALREADY_EXISTS'):
            self.store.initialize()
        self.assertEqual(self.store.load().revision, first.revision)

    def test_sec_ifrs_us_gaap_esef_roundtrip_n_plus_one_and_restatement(self):
        retained = dict(issuer=CIK, generated_at='2024-08-03T00:00:00Z', reference='fictional:old',
                        input_version_ids=['a'*64], **binary(b'fictional-output-not-validated'))
        self.store.initialize(new_history(retained_output=retained))
        for item in [report(2023), report(2024, namespace='ifrs-full'), report(2025), esef_report()]:
            previous = self.store.load()
            snapshot = self.store.import_report(item, expected_revision=previous.revision)
            self.assertEqual(snapshot.history['reports'][:-1], previous.history['reports'])
        first = snapshot.history['reports'][1]['version_id']
        restated = report(2024, amount=99, filed='2026-01-01', received='2026-01-02T00:00:00Z', namespace='ifrs-full',
                          document_id='fictional-restated')
        restated.update(revision_kind='RESTATEMENT', revises=[first])
        final = self.store.import_report(restated)
        loaded = storage.HistoryStore(self.path).load()
        self.assertEqual(serialize(final.history), serialize(loaded.history))
        self.assertEqual(loaded.history['retained_output'], retained)
        for identity in (CIK, LEI):
            self.assertEqual(issuer_views(final.history, identity), issuer_views(loaded.history, identity))
        self.assertEqual(issuer_views(loaded.history, CIK)['annual_periods'][1]['status'], 'CONFLICT')
        self.assertEqual(len(loaded.history['reports']), 5)
        for record in loaded.history['reports']:
            self.assertEqual(record['input']['source']['declared_sha256'],
                             hashlib.sha256(base64.b64decode(
                                 record['input']['source']['raw_bytes_base64'])).hexdigest())
        self.assert_private(loaded)

    def test_esef_n_plus_one_restatement_and_untrusted_archived_output(self):
        def esef(year, value='100'):
            item = esef_report()
            period = f'{year}-01-01T00:00:00/{year+1}-01-01T00:00:00'
            document = json.loads(base64.b64decode(item['source']['raw_bytes_base64']))
            fact = document['matching_facts'][0]
            fact.update(period=period, value=value)
            fact['dimensions']['period'] = period
            item['reporting_period'] = dict(start=f'{year}-01-01', end=f'{year}-12-31')
            item['received_at'] = f'{year+1}-03-02T10:00:00Z'
            item['source'].update(document_id=f'fictional-esef-{year}', filed_at=f'{year+1}-03-01',
                                  **binary(json.dumps(document).encode()))
            return item
        self.store.initialize()
        for year in [2023, 2024, 2025]:
            current = self.store.import_report(esef(year))
        old = current.history['reports'][1]['version_id']
        restated = esef(2024, '99')
        restated.update(revision_kind='RESTATEMENT', revises=[old], received_at='2027-01-02T00:00:00Z')
        restated['source'].update(document_id='fictional-esef-restated', filed_at='2027-01-01')
        current = self.store.import_report(restated)
        loaded = self.store.load()
        self.assertEqual(serialize(current.history), serialize(loaded.history))
        view = issuer_views(loaded.history, LEI)
        self.assertEqual(view['annual_periods'][1]['status'], 'CONFLICT')
        self.assertEqual(len(view['annual_periods'][1]['version_ids']), 2)
        self.assertEqual(view['latest_available']['periods'][0]['end'], '2025-12-31')
        self.assert_private(loaded)

    def test_reimport_and_restart_preserve_exact_file_revision_and_first_receipt(self):
        snapshot = self.initialized()
        original = self.path.read_bytes()
        later = report(2024, received='2026-01-01T00:00:00Z')
        restarted = storage.HistoryStore(self.path)
        same = restarted.import_report(later, expected_revision=snapshot.revision)
        self.assertEqual(same.revision, snapshot.revision)
        self.assertEqual(same.generation, snapshot.generation)
        self.assertEqual(self.path.read_bytes(), original)

    def test_out_of_order_local_import_and_invalid_report_leave_old_bytes(self):
        self.initialized()
        self.store.import_report(report(2025))
        local = self.root / 'report.json'
        local.write_text(canonical_json(report(2023, received='2026-01-01T00:00:00Z')), encoding='utf-8')
        snapshot = self.store.import_local(local)
        view = issuer_views(snapshot.history, CIK)
        self.assertEqual(view['latest_available']['periods'][0]['end'], '2025-06-30')
        self.assertEqual(view['latest_received']['version_ids'], [snapshot.history['reports'][-1]['version_id']])
        original = self.path.read_bytes()
        for payload in ['{}', '{"a":1,"a":1}', '{"value":NaN}', '{"value":Infinity}', '{broken', '[]']:
            local.write_text(payload, encoding='utf-8')
            with self.assertRaises(ValueError):
                self.store.import_local(local)
            self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_unknown_incomplete_duplicate_nonfinite_rejected_without_write(self):
        self.initialized()
        original = self.path.read_bytes()
        envelope = json.loads(original)
        bad_version = dict(envelope, storage_format='iic.annual-history-store.v999')
        bad_history = copy.deepcopy(envelope)
        bad_history['history']['reports'][0]['observations'] = []
        bad_hash = dict(envelope, payload_sha256='0'*64)
        bad_history_version = copy.deepcopy(envelope)
        bad_history_version['history']['schema_version'] = 'iic.annual-history.v999'
        payloads = [b'', b'{', b'{}', b'[]', b'\xff',
                    b'{"generation":1,"generation":2}', b'{"value":NaN}', b'{"value":Infinity}',
                    *[canonical_json(x).encode() for x in [bad_version, bad_history, bad_hash, bad_history_version]]]
        for payload in payloads:
            self.path.write_bytes(payload)
            with self.subTest(payload=payload[:15]):
                for action in [self.store.load, lambda: self.store.import_report(report()),
                               self.store.initialize, self.store.cleanup_orphans]:
                    with self.assertRaises(ValueError):
                        action()
                    self.assertEqual(self.path.read_bytes(), payload)
        # Recovery is explicit, using a caller-held known backup; never choose a temporary.
        self.path.write_bytes(original)
        self.assertEqual(len(self.store.load().history['reports']), 1)

    def test_positive_generation_tampering_is_detected_before_any_write(self):
        self.initialized()
        envelope = json.loads(self.path.read_bytes())
        envelope['generation'] += 1
        altered = canonical_json(envelope).encode()
        self.path.write_bytes(altered)
        for operation in [self.store.load, lambda: self.store.import_report(report())]:
            with self.assertRaisesRegex(storage.StoreError, 'CHECKSUM_MISMATCH'):
                operation()
            self.assertEqual(self.path.read_bytes(), altered)

    def test_json_types_cannot_alias_revisions_or_metadata(self):
        item = report()
        history = import_report(new_history(), item)
        for value in [0, 1, True]:
            changed = copy.deepcopy(item)
            changed['valuation_usable'] = value
            with self.assertRaises(ValueError):
                import_report(history, changed)
        for value in [True, 1]:
            changed = copy.deepcopy(item)
            changed['source']['provenance']['reference'] = value
            with self.assertRaises(ValueError):
                import_report(history, changed)
        # A raw true versus 1 is a distinct source/version, never an identical reimport.
        a = report(rows=[dict(ROW,
                             start='2024-07-01', end='2025-06-30', filed='2025-08-01',
                             dimensions={'fictional_axis': True})])
        b = copy.deepcopy(a)
        raw = base64.b64decode(a['source']['raw_bytes_base64'])
        b['source'].update(binary(raw.replace(b'true', b'1')))
        history = import_report(import_report(new_history(), a), b)
        self.assertEqual(len(history['reports']), 2)
        self.assertNotEqual(history['reports'][0]['version_id'], history['reports'][1]['version_id'])
        tampered = copy.deepcopy(history)
        tampered['reports'][0]['observations'][0]['dimensions']['fictional_axis'] = 1
        with self.assertRaises(ValueError):
            validate_history(tampered)
        self.store.initialize(history)
        envelope = json.loads(self.path.read_bytes())
        for value in [True, 1.0, '1', False, 0]:
            bad = dict(envelope, generation=value)
            self.path.write_text(canonical_json(bad), encoding='utf-8')
            with self.assertRaises(ValueError):
                self.store.load()

    def test_stale_revision_and_two_instances_do_not_lose_updates(self):
        first = self.initialized()
        second = storage.HistoryStore(self.path)
        committed = second.import_report(report(2025), expected_revision=first.revision)
        original = self.path.read_bytes()
        with self.assertRaises(storage.RevisionConflict):
            self.store.import_report(report(2023), expected_revision=first.revision)
        self.assertEqual(self.path.read_bytes(), original)
        resumed = self.store.import_report(report(2023), expected_revision=committed.revision)
        self.assertEqual(len(resumed.history['reports']), 3)

    def test_real_process_holds_lock_and_os_releases_after_exit(self):
        self.initialized()
        ctx = multiprocessing.get_context('spawn')
        ready, release = ctx.Event(), ctx.Event()
        process = ctx.Process(target=lock_worker, args=(str(self.path), ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(20))
            with self.assertRaises(storage.StoreBusy):
                self.store.import_report(report())
            with self.assertRaises(storage.StoreBusy):
                storage.HistoryStore(self.path).load()
        finally:
            release.set()
            process.join(20)
            if process.is_alive():
                process.terminate(); process.join()
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.import_report(report()).history['reports']), 2)

    def test_two_simultaneous_processes_cas_then_retry_without_loss(self):
        initial = self.initialized()
        ctx = multiprocessing.get_context('spawn')
        ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
        processes = [ctx.Process(target=update_worker,
                     args=(str(self.path), report(year), initial.revision, start, ready, results))
                     for year in [2023, 2025]]
        for process in processes:
            process.start()
        try:
            for _ in processes:
                self.assertTrue(ready.get(timeout=20))
            start.set()
            outcomes = [results.get(timeout=30) for _ in processes]
            self.assertEqual(outcomes.count('COMMITTED'), 1)
            self.assertTrue(any(result in ('StoreBusy', 'RevisionConflict') for result in outcomes))
        finally:
            start.set()
            for process in processes:
                process.join(20)
                if process.is_alive():
                    process.terminate();process.join()
            ready.close(); results.close()
        for process in processes:
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.load().history['reports']), 2)
        for year in [2023, 2025]:
            self.store.import_report(report(year))
        self.assertEqual(len(self.store.load().history['reports']), 3)

    def test_killed_writer_before_write_replace_and_after_replace_resume_idempotently(self):
        self.initialized()
        ctx = multiprocessing.get_context('spawn')
        for phase, year in [('before_write', 2023), ('before_replace', 2025), ('after_replace', 2026)]:
            before = self.path.read_bytes()
            item = report(year)
            process = ctx.Process(target=crash_worker, args=(str(self.path), item, phase))
            process.start();process.join(30)
            if process.is_alive():
                process.terminate();process.join()
            self.assertEqual(process.exitcode, 73)
            if phase != 'after_replace':
                self.assertEqual(self.path.read_bytes(), before)
            else:
                self.assertNotEqual(self.path.read_bytes(), before)
            valid = self.store.load()
            resumed = self.store.import_report(item)
            once = self.path.read_bytes()
            self.store.import_report(item)
            self.assertEqual(self.path.read_bytes(), once)
            self.assertGreaterEqual(len(resumed.history['reports']), len(valid.history['reports']))
        self.assertTrue(list(self.root.glob('history.json.*.tmp')))
        removed = self.store.cleanup_orphans()
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(list(self.root.glob('history.json.*.tmp')), [])
        self.assertEqual(len(self.store.load().history['reports']), 4)

    def test_disk_open_write_flush_sync_readback_errors_leave_old_intact_and_clean(self):
        self.initialized()
        original = self.path.read_bytes()
        original_temp = storage.tempfile.NamedTemporaryFile
        class FaultyStream:
            def __init__(self, phase, **kwargs):
                self.stream = original_temp(**kwargs)
                self.phase = phase
                self.name = self.stream.name
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, data):
                if self.phase == 'write':
                    self.stream.write(data[:20])
                    raise OSError('simulated full disk')
                return self.stream.write(data)
            def flush(self):
                if self.phase == 'flush': raise OSError('simulated flush error')
                return self.stream.flush()
            def fileno(self): return self.stream.fileno()
        faults = [patch.object(storage.tempfile, 'NamedTemporaryFile', side_effect=PermissionError('denied')),
                  patch.object(storage.os, 'fsync', side_effect=OSError('disk error'))]
        for phase in ['write', 'flush']:
            faults.append(patch.object(storage.tempfile, 'NamedTemporaryFile',
                                       side_effect=lambda _phase=phase, **kwargs: FaultyStream(_phase, **kwargs)))
        for fault in faults:
            with fault, self.assertRaises(OSError):
                self.store.import_report(report())
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(list(self.root.glob('history.json.*.tmp')), [])
        self.assertEqual(len(self.store.import_report(report()).history['reports']), 2)

    def test_replace_failure_and_post_replace_directory_error_require_reload(self):
        self.initialized()
        original = self.path.read_bytes()
        with patch.object(storage.os, 'replace', side_effect=PermissionError('denied')):
            with self.assertRaises(storage.CommitUncertain):
                self.store.import_report(report())
        self.assertEqual(self.path.read_bytes(), original)
        with patch.object(storage, '_sync_directory', side_effect=OSError('directory sync failed')):
            with self.assertRaises(storage.CommitUncertain):
                self.store.import_report(report())
        # After replacement do not destructively restore the previous generation.
        self.assertNotEqual(self.path.read_bytes(), original)
        loaded = self.store.load()
        self.assertEqual(len(loaded.history['reports']), 2)
        revision = loaded.revision
        self.assertEqual(self.store.import_report(report()).revision, revision)
        self.assertEqual(list(self.root.glob('history.json.*.tmp')), [])

    def test_readback_mismatch_never_replaces(self):
        self.initialized()
        original = self.path.read_bytes()
        real_read = storage._read
        def corrupt(path, limit):
            raw = real_read(path, limit)
            return b'corrupt' if str(path).endswith('.tmp') else raw
        with patch.object(storage, '_read', side_effect=corrupt), self.assertRaises(storage.StoreError):
            self.store.import_report(report())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob('history.json.*.tmp')), [])

    def test_cleanup_failure_is_explicit_and_never_adopts_the_temporary(self):
        self.initialized()
        original = self.path.read_bytes()
        with patch.object(storage.os, 'fsync', side_effect=OSError('write failed')):
            with patch.object(Path, 'unlink', side_effect=PermissionError('cleanup denied')):
                with self.assertRaisesRegex(storage.CommitUncertain, 'CLEANUP_FAILED'):
                    self.store.import_report(report())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.load().revision, hashlib.sha256(original).hexdigest())
        self.assertEqual(self.store.cleanup_orphans(), 1)
        with patch.object(Path, 'open', side_effect=PermissionError('read denied')):
            with self.assertRaises(PermissionError):
                self.store.import_report(report())
        self.assertEqual(self.path.read_bytes(), original)

    def test_paths_size_limits_and_private_posix_modes(self):
        for path in ['relative.json', storage.REPOSITORY / 'private-history.json']:
            with self.assertRaises(storage.StoreError):
                storage.HistoryStore(path)
        nested = self.root / 'other-repository'
        nested.mkdir(mode=0o700); (nested / '.git').write_text('gitdir: fictional')
        with self.assertRaises(storage.StoreError):
            storage.HistoryStore(nested / 'history.json')
        self.initialized()
        original = self.path.read_bytes()
        with patch.object(storage, 'MAX_STORE_BYTES', 10), self.assertRaises(storage.StoreError):
            self.store.load()
        self.assertEqual(self.path.read_bytes(), original)
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.store.lock_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(storage.StoreError):
            self.store.import_local('relative.json')

    def test_cli_status_is_summary_only_and_never_dumps_private_source(self):
        import contextlib
        import io
        self.initialized()
        stream = io.StringIO()
        with patch.object(sys, 'argv', ['store', 'status', '--store', str(self.path)]), contextlib.redirect_stdout(stream):
            self.assertEqual(storage.main(), 0)
        output = stream.getvalue()
        self.assertIn('PRIVATE valuation_usable=false', output)
        for secret in ['raw_bytes_base64', 'fictional', CIK['identifier'], str(self.path)]:
            self.assertNotIn(secret, output)


if __name__ == '__main__':
    unittest.main()
