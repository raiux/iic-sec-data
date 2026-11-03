import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collect_sec as sec

CLOCK = datetime(2026, 11, 3, tzinfo=timezone.utc)
COMPANY = {'name': 'Test', 'ticker': 'TEST', 'cik': '0000000001'}
ROW = {'start': '2025-01-01', 'end': '2025-12-31', 'val': 100,
       'filed': '2026-03-01', 'form': '10-K', 'accn': '0000000001-26-000001'}


def document():
    return {'cik': 1, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [dict(ROW)]}}}}}


def cache(age=1):
    return {'schema': 1, 'companies': [dict(COMPANY, facts=document()['facts'],
            fetched_at=(CLOCK - timedelta(hours=age)).isoformat())]}


class SecTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(sec, 'now', return_value=CLOCK)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.ua = patch.object(sec, 'UA', 'IIC tests contact@example.invalid')
        self.ua.start()
        self.addCleanup(self.ua.stop)
        self.network = patch.object(sec.urllib.request, 'urlopen', side_effect=AssertionError('Unexpected network'))
        self.open = self.network.start()
        self.addCleanup(self.network.stop)
        sec.LAST_REQUEST = None
        self.elapsed = 0.0
        self.sleep = patch.object(sec.time, 'sleep', side_effect=self.advance)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.monotonic = patch.object(sec.time, 'monotonic', side_effect=lambda: self.elapsed)
        self.monotonic.start()
        self.addCleanup(self.monotonic.stop)

    def advance(self, seconds):
        self.elapsed += seconds

    def response(self):
        return io.BytesIO(json.dumps(document()).encode())

    def error(self, code, retry=None):
        return urllib.error.HTTPError('https://data.sec.gov/test', code, 'error',
                                      {'Retry-After': retry} if retry else {}, None)

    def test_interval_headers_timeout(self):
        times = []
        def respond(request, timeout):
            times.append(self.elapsed)
            self.assertEqual(timeout, 40)
            self.assertIn('contact@', request.get_header('User-agent'))
            return self.response()
        self.open.side_effect = respond
        for _ in range(3):
            sec.request_json('https://data.sec.gov/test')
        self.assertEqual(times, [0, 1, 2])

    def test_403_stops_all_companies_and_retains(self):
        self.open.side_effect = self.error(403)
        old = cache(25)
        other = dict(COMPANY, cik='0000000002', name='Other')
        result = sec.collect([COMPANY, other], old)
        self.assertEqual(self.open.call_count, 1)
        self.assertEqual(result['companies'][0]['facts'], old['companies'][0]['facts'])
        self.assertEqual(result['companies'][0]['status'], 'STALE_RETAINED')
        self.assertEqual(result['unavailable'][0]['status'], 'UNAVAILABLE')

    def test_retry_after_seconds_and_http_date(self):
        for retry in ['90', 'Tue, 03 Nov 2026 00:01:30 GMT']:
            with self.subTest(retry=retry):
                self.elapsed = 0
                sec.LAST_REQUEST = None
                self.open.side_effect = [self.error(429, retry), self.response()]
                sec.request_json('https://data.sec.gov/test')
                self.assertGreaterEqual(self.elapsed, 90)

    def test_429_bounded_and_long_retry_stops(self):
        for retry, calls in [('1', 3), ('999999', 1)]:
            with self.subTest(retry=retry):
                self.open.reset_mock()
                self.open.side_effect = self.error(429, retry)
                with self.assertRaises(sec.SecBlocked):
                    sec.request_json('https://data.sec.gov/test')
                self.assertEqual(self.open.call_count, calls)

    def test_5xx_retries_bounded_and_recovers(self):
        for code in [500, 502, 503, 504]:
            self.open.reset_mock()
            self.open.side_effect = self.error(code)
            with self.assertRaises(urllib.error.HTTPError):
                sec.request_json('https://data.sec.gov/test')
            self.assertEqual(self.open.call_count, 3)
        self.open.side_effect = [self.error(503), self.response()]
        self.assertEqual(sec.request_json('https://data.sec.gov/test')['cik'], 1)

    def test_transport_timeout_retries(self):
        self.open.side_effect = TimeoutError('timeout')
        with self.assertRaises(TimeoutError):
            sec.request_json('https://data.sec.gov/test')
        self.assertEqual(self.open.call_count, 3)

    def test_fresh_cache_no_request_and_no_timestamp_renewal(self):
        old = cache()
        result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['status'], 'CACHE_FRESH')
        self.assertEqual(result['fetched_at'], old['companies'][0]['fetched_at'])
        self.open.assert_not_called()

    def test_stale_missing_or_future_timestamp_fetches(self):
        for age in [24, 25, -1, None]:
            old = cache(age or 1)
            if age is None:
                del old['companies'][0]['fetched_at']
            with patch.object(sec, 'request_json', return_value=document()) as request:
                result = sec.collect([COMPANY], old)['companies'][0]
            request.assert_called_once()
            self.assertEqual(result['status'], 'FETCHED')
            self.assertEqual(result['validation_status'], 'CANDIDATE')

    def test_malformed_response_retains_last_valid_and_timestamp(self):
        old = cache(25)
        for bad in [None, {}, {'cik': 2, 'facts': document()['facts']}, {'cik': 1, 'facts': {}}]:
            with patch.object(sec, 'request_json', return_value=bad):
                result = sec.collect([COMPANY], old)['companies'][0]
            self.assertEqual(result['status'], 'STALE_RETAINED')
            self.assertEqual(result['facts'], old['companies'][0]['facts'])
            self.assertEqual(result['fetched_at'], old['companies'][0]['fetched_at'])
        self.open.side_effect = lambda *a, **k: io.BytesIO(b'not json')
        result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['status'], 'STALE_RETAINED')

    def test_truncated_history_retains_last_observation(self):
        old = cache(25)
        old['companies'][0]['facts']['us-gaap']['Revenues']['units']['USD'].append(
            dict(ROW, val=98, accn='0000000001-26-000002', form='10-K/A'))
        with patch.object(sec, 'request_json', return_value=document()):
            result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['status'], 'STALE_RETAINED')
        self.assertEqual(result['facts'], old['companies'][0]['facts'])

    def test_invalid_contact_prevents_request(self):
        with patch.object(sec, 'UA', 'anonymous'):
            with self.assertRaises(ValueError):
                sec.request_json('https://data.sec.gov/test')
        self.open.assert_not_called()

    def test_no_cache_failure_is_unavailable(self):
        self.open.side_effect = self.error(503)
        result = sec.collect([COMPANY], {})
        self.assertEqual(result['companies'], [])
        self.assertEqual(result['unavailable'][0]['status'], 'UNAVAILABLE')

    def test_periods_units_dimensions_and_conflicts(self):
        doc = document()
        units = doc['facts']['us-gaap']['Revenues']['units']
        amended = dict(ROW, val=99, form='10-K/A', accn='0000000001-26-000002')
        units['USD'] += [amended, dict(ROW, start='2025-10-01'), dict(ROW, form='10-Q'),
                         dict(ROW, dimensions={'segment': 'x'})]
        units['shares'] = [dict(ROW)]
        facts = sec.compact_facts(doc, COMPANY['cik'])
        self.assertEqual(facts['us-gaap']['Revenues']['units'], {'USD': [ROW, amended]})

    def test_invalid_values_and_dates_rejected(self):
        for changes in [{'val': float('nan')}, {'val': True}, {'val': '100'},
                        {'filed': 'bad'}, {'accn': None}, {'end': '2027-01-01'}]:
            doc = document()
            doc['facts']['us-gaap']['Revenues']['units']['USD'][0].update(changes)
            with self.assertRaises(ValueError):
                sec.compact_facts(doc, COMPANY['cik'])

    def test_atomic_failure_keeps_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory(dir=sec.BASE) as directory:
            path = Path(directory) / 'sec_facts.json'
            sec.write_atomic(path, cache())
            original = path.read_bytes()
            with patch.object(Path, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    sec.write_atomic(path, {'companies': []})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_existing_schema_compatibility_and_pilot_coverage(self):
        stored = json.loads((sec.BASE / 'data/sec_facts.json').read_text())
        self.assertEqual(stored['schema'], 1)
        for company in stored['companies']:
            facts = sec.compact_facts(company, company['cik'], downloaded=False)
            self.assertTrue(facts)
            for namespace, concepts in facts.items():
                for tag, obj in concepts.items():
                    self.assertTrue(all(row in company['facts'][namespace][tag]['units'][unit]
                                        for unit, rows in obj['units'].items() for row in rows))
        with patch.object(sec, 'request_json', side_effect=ValueError('bad download')):
            output = sec.collect([dict(COMPANY)], cache(25))
        self.assertEqual(set(stored), set(output))

    def test_partial_cache_retains_valid_rows_on_each_failure(self):
        import copy
        import http.client
        for failure in [None, {}, self.error(403), self.error(429, '999999'),
                        urllib.error.URLError('interrupted'),
                        http.client.IncompleteRead(b'partial')]:
            with self.subTest(failure=repr(failure)):
                old = cache(25)
                rows = old['companies'][0]['facts']['us-gaap']['Revenues']['units']['USD']
                valid = dict(ROW, val=98, accn='0000000001-26-000002')
                rows.extend([valid, dict(ROW, val='invalid')])
                before = copy.deepcopy(old)
                self.open.side_effect = failure if isinstance(failure, Exception) else (
                    lambda *a, **k: io.BytesIO(json.dumps(failure).encode()))
                result = sec.collect([COMPANY], old)['companies'][0]
                self.assertEqual(result['facts']['us-gaap']['Revenues']['units']['USD'], [ROW, valid])
                self.assertEqual(result['collection_status'], 'FAILED')
                self.assertEqual(result['data_status'], 'RETAINED')
                self.assertEqual(result['cache_validation_status'], 'PARTIALLY_REJECTED')
                self.assertEqual(result['rejected_observations'][0]['observation']['val'], 'invalid')
                self.assertEqual(result['fetched_at'], old['companies'][0]['fetched_at'])
                self.assertEqual(old, before)

    def test_mixed_invalid_download_retains_entire_history(self):
        old = cache(25)
        doc = document()
        doc['facts']['us-gaap']['Revenues']['units']['USD'].extend([
            dict(ROW, val=99, accn='0000000001-26-000002'), dict(ROW, val=None)])
        with patch.object(sec, 'request_json', return_value=doc):
            result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['facts'], old['companies'][0]['facts'])
        self.assertEqual(result['collection_status'], 'FAILED')

    def test_provenance_change_blocks_replacement(self):
        old = cache(25)
        doc = document()
        doc['facts']['us-gaap']['Revenues']['units']['USD'][0]['filed'] = '2026-03-02'
        with patch.object(sec, 'request_json', return_value=doc):
            result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['facts'], old['companies'][0]['facts'])
        self.assertEqual(result['status'], 'STALE_RETAINED')

    def test_success_quarantines_partial_history_and_adds_new(self):
        old = cache(25)
        historical = dict(ROW, start='2025-10-01')
        old['companies'][0]['facts']['us-gaap']['Revenues']['units']['USD'].append(historical)
        doc = document()
        new = dict(ROW, val=99, accn='0000000001-26-000002')
        doc['facts']['us-gaap']['Revenues']['units']['USD'].append(new)
        with patch.object(sec, 'request_json', return_value=doc):
            result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['facts']['us-gaap']['Revenues']['units']['USD'], [ROW, new])
        self.assertEqual(result['rejected_observations'][0]['observation'], historical)
        self.assertEqual(result['data_status'], 'NEW_AND_RETAINED')

    def test_invalid_cache_container_blocks_publication(self):
        old = cache(25)
        old['companies'][0]['facts']['us-gaap']['Revenues']['units']['USD'] = None
        with patch.object(sec, 'write_atomic') as write:
            with self.assertRaises(ValueError):
                sec.collect([COMPANY], old)
        write.assert_not_called()

    def test_empty_body_retains_cache(self):
        self.open.side_effect = lambda *a, **k: io.BytesIO(b'')
        old = cache(25)
        result = sec.collect([COMPANY], old)['companies'][0]
        self.assertEqual(result['facts'], old['companies'][0]['facts'])
        self.assertEqual(result['collection_status'], 'FAILED')

    def test_cache_filtered_rows_quarantined_with_provenance(self):
        import copy
        for changes, unit in [({'form': '8-K'}, 'USD'),
                              ({'start': '2025-10-01'}, 'USD'),
                              ({'dimensions': {'segment': 'x'}}, 'USD'),
                              ({'segment': {'name': 'x'}}, 'USD'),
                              ({}, 'shares')]:
            with self.subTest(changes=changes, unit=unit):
                old = cache(25)
                valid = old['companies'][0]['facts']['us-gaap']['Revenues']['units']['USD'][0]
                valid['source_fact_id'] = 'original-provenance'
                invalid = dict(ROW, **changes)
                units = old['companies'][0]['facts']['us-gaap']['Revenues']['units']
                units.setdefault(unit, []).append(invalid)
                before = copy.deepcopy(old)
                with patch.object(sec, 'request_json', return_value={}):
                    result = sec.collect([COMPANY], old)['companies'][0]
                self.assertEqual(result['facts']['us-gaap']['Revenues']['units']['USD'], [valid])
                self.assertEqual(result['rejected_observations'], [dict(
                    namespace='us-gaap', concept='Revenues', unit=unit,
                    observation=invalid,
                    reason='Excluded by configured form, period, dimensions or unit filters')])
                self.assertEqual(result['cache_validation_status'], 'PARTIALLY_REJECTED')
                self.assertEqual(old, before)
                again = sec.recover_cache(result, COMPANY['cik'])
                self.assertEqual(again, result)

    def test_unknown_historical_concepts_preserved_and_not_validated(self):
        import copy
        for namespace in ['us-gaap', 'custom-issuer']:
            old = cache(25)
            unknown = {'label': 'Historical label', 'units': {'USD': [dict(ROW, form='8-K')]}}
            old['companies'][0]['facts'].setdefault(namespace, {})['UnknownConcept'] = unknown
            before = copy.deepcopy(old)
            for response in [{}, document()]:
                with patch.object(sec, 'request_json', return_value=response):
                    result = sec.collect([COMPANY], old)['companies'][0]
                self.assertEqual(result['facts'][namespace]['UnknownConcept'], unknown)
                self.assertEqual(result['historical_concepts'], [dict(
                    namespace=namespace, concept='UnknownConcept', validation_status='NOT_EVALUATED',
                    reason='Historical concept outside configured TAGS; preserved verbatim')])
                self.assertEqual(result['rejected_observations'], [])
                self.assertEqual(old, before)


if __name__ == '__main__':
    unittest.main()
