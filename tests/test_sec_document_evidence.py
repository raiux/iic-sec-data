"""Fictitious bytes only. No document is acquired or authenticated by these tests."""
import base64
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.offline_pipeline import PublicExportBlocked, validator
from scripts.sec_document_evidence import serialize, validate_bundle, validate_package
from scripts.validate_sec_accounting import validate_bundle as validate_c2
from tests.test_c2_accounting import c1_bundle, filing_evidence
from tests.test_normalize_sec import ROW


RAW = b'FICTIONAL DOCUMENT ONLY\x00\xff\r\nNot an SEC filing.\n'


def package():
    # The archive-shaped locator is inherited from the existing fictitious C2
    # assertion fixture; no live document or URL is claimed to exist.
    assertion = filing_evidence()
    return dict(schema_version='iic.c3-document-package.v1', visibility='PRIVATE',
                valuation_usable=False,
                identity=dict(issuer_cik='0000000001', accession='0000000001-26-000001',
                              document_name='filing.xml'),
                raw_bytes_base64=base64.b64encode(RAW).decode(),
                declared_sha256=hashlib.sha256(RAW).hexdigest(),
                declared_origin=dict(kind='FIXTURE', locator=assertion['document_url']),
                declared_acquired_at='2026-11-04T00:00:00Z',
                provenance=dict(method='FICTITIOUS_LOCAL_TEST', reference='fictional-document-fixture'))


def matching_assertion():
    return dict(filing_evidence(), document_sha256=hashlib.sha256(RAW).hexdigest())


class DocumentEvidenceTests(unittest.TestCase):
    def test_binary_roundtrip_integrity_does_not_authenticate(self):
        item = package()
        original = copy.deepcopy(item)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network')):
            report = validate_package(item)
        self.assertEqual(report['byte_integrity'], 'MATCH')
        self.assertEqual(report['status'], 'CONSISTENT_UNAUTHENTICATED')
        self.assertEqual(report['identity_comparison'], 'NOT_COMPARED')
        self.assertEqual(report['origin_authenticity'], 'UNCHECKED')
        self.assertEqual(report['accounting_validation'], 'UNCHECKED')
        self.assertFalse(report['valuation_usable'])
        self.assertEqual(report['visibility'], 'PRIVATE')
        self.assertEqual(base64.b64decode(report['package']['raw_bytes_base64']), RAW)
        self.assertEqual(item, original)
        report['package']['provenance']['method'] = 'changed output'
        self.assertEqual(item, original)

    def test_altered_bytes_or_declared_hash_are_conflicts(self):
        for field, value in [('raw_bytes_base64', base64.b64encode(RAW + b'altered').decode()),
                             ('declared_sha256', '0' * 64)]:
            with self.subTest(field=field):
                item = package()
                item[field] = value
                report = validate_package(item)
                self.assertEqual(report['byte_integrity'], 'MISMATCH')
                self.assertEqual(report['status'], 'CONFLICT')
                self.assertEqual(report['reasons'], ['DECLARED_SHA256_MISMATCH'])
                self.assertEqual(report['package'], item)
                self.assertEqual(report['origin_authenticity'], 'UNCHECKED')

    def test_rehashed_forgery_is_still_unauthenticated(self):
        item = package()
        item['declared_origin']['kind'] = 'IMPORTED'
        item['raw_bytes_base64'] = base64.b64encode(b'forged fictional contents').decode()
        item['declared_sha256'] = hashlib.sha256(b'forged fictional contents').hexdigest()
        report = validate_package(item)
        self.assertEqual(report['byte_integrity'], 'MATCH')
        self.assertEqual(report['origin_authenticity'], 'UNCHECKED')
        self.assertEqual(report['status'], 'CONSISTENT_UNAUTHENTICATED')

    def test_identity_and_locator_divergences_despite_matching_hash(self):
        for field, value in [('issuer_cik', '0000000002'),
                             ('accession', '0000000001-26-000002'),
                             ('document_name', 'different.xml'), ('locator', 'fictional:other')]:
            with self.subTest(field=field):
                item = package()
                if field == 'locator':
                    item['declared_origin']['locator'] = value
                else:
                    item['identity'][field] = value
                report = validate_package(item, expected=matching_assertion())
                self.assertEqual(report['byte_integrity'], 'MATCH')
                self.assertEqual(report['identity_comparison'], 'CONFLICT')
                self.assertEqual(report['status'], 'CONFLICT')
                self.assertEqual(report['reasons'], ['DOCUMENT_IDENTITY_DECLARATIONS_DIVERGE'])

    def test_manifest_hash_is_checked_independently(self):
        report = validate_package(package(), expected=filing_evidence())
        self.assertEqual(report['byte_integrity'], 'MATCH')
        self.assertEqual(report['identity_comparison'], 'DECLARATIONS_MATCH')
        self.assertEqual(report['manifest_sha256'], 'MISMATCH')
        self.assertEqual(report['status'], 'CONFLICT')

    def test_missing_fields_and_invalid_json_types_are_rejected(self):
        for field in package():
            with self.subTest(missing=field):
                item = package()
                del item[field]
                self.assertEqual(validate_package(item)['reasons'], ['PACKAGE_SCHEMA_INVALID'])
        for field in package():
            for value in (None, [], {}, 3, True):
                if value == package()[field] and type(value) is type(package()[field]):
                    continue
                with self.subTest(field=field, value=value):
                    item = package()
                    item[field] = value
                    self.assertEqual(validate_package(item)['status'], 'INVALID')
        for value in (None, [], 'private marker', 3, True):
            self.assertEqual(validate_package(value)['reasons'], ['PACKAGE_SCHEMA_INVALID'])

    def test_malformed_nested_values_and_authenticity_claim_rejected(self):
        for section, field, value in [
            ('identity', 'issuer_cik', []), ('identity', 'accession', {}),
            ('identity', 'document_name', '../fictional.xml'),
            ('declared_origin', 'kind', 'VERIFIED'),
            ('declared_origin', 'locator', None), ('provenance', 'method', []),
        ]:
            with self.subTest(section=section, field=field):
                item = package()
                item[section][field] = value
                self.assertEqual(validate_package(item)['status'], 'INVALID')
        for field, value in [('declared_acquired_at', '2026-11-04'),
                             ('declared_acquired_at', 'not-a-date'),
                             ('declared_sha256', 'bad'), ('origin_authenticity', 'VERIFIED')]:
            item = package()
            item[field] = value
            self.assertEqual(validate_package(item)['status'], 'INVALID')

    def test_base64_is_canonical_nonempty_and_strict(self):
        for value in ('!!!!', 'YQ==\n', 'YR==', '====', 'éééé', ''):
            with self.subTest(value=value):
                item = package()
                item['raw_bytes_base64'] = value
                self.assertEqual(validate_package(item)['status'], 'INVALID')

    def test_identity_and_hash_patterns_reject_trailing_newlines(self):
        for field in ('issuer_cik', 'accession', 'document_name', 'declared_sha256'):
            with self.subTest(field=field):
                item = package()
                container = item if field == 'declared_sha256' else item['identity']
                container[field] += '\n'
                self.assertEqual(validate_package(item)['reasons'], ['PACKAGE_SCHEMA_INVALID'])

    def test_invalid_expected_assertions_cannot_create_a_match(self):
        for value in (None, [], {}, dict(matching_assertion(), taxonomy=[])):
            with self.subTest(value=value):
                report = validate_package(package(), expected=value)
                self.assertEqual(report['status'], 'INVALID')
                self.assertEqual(report['reasons'], ['C2_ASSERTION_INVALID'])

    def test_c2_connector_preserves_exact_report_with_and_without_conflicts(self):
        for rows in ([ROW], [ROW, dict(ROW, val=99)]):
            with self.subTest(rows=len(rows)):
                bundle = c1_bundle(rows=rows)
                evidence = [matching_assertion()]
                documents = [dict(evidence_index=0, package=package())]
                before = copy.deepcopy((bundle, evidence, documents))
                result = validate_bundle(bundle, evidence=evidence, documents=documents)
                self.assertEqual(result['c2'], validate_c2(bundle, evidence=evidence))
                self.assertEqual((bundle, evidence, documents), before)
                check = result['document_checks'][0]
                self.assertEqual(check['status'], 'CONSISTENT_UNAUTHENTICATED')
                self.assertEqual(check['identity_comparison'], 'DECLARATIONS_MATCH')
                self.assertEqual(check['manifest_sha256'], 'MATCH')
                self.assertFalse(result['valuation_usable'])
                self.assertEqual(result['origin_authenticity'], 'UNCHECKED')
                validator('c2-accounting-validation.schema.json').validate(result['c2'])

    def test_bad_documents_and_indices_do_not_change_c2(self):
        bundle = c1_bundle()
        evidence = [matching_assertion()]
        expected = validate_c2(bundle, evidence=evidence)
        for entry in (None, {}, dict(evidence_index=True, package=package()),
                      dict(evidence_index=-1, package=package()),
                      dict(evidence_index=1, package=package()),
                      dict(evidence_index=0, package=None)):
            result = validate_bundle(bundle, evidence=evidence, documents=[entry])
            self.assertEqual(result['document_checks'][0]['status'], 'INVALID')
            self.assertEqual(result['c2'], expected)
        for value in (None, {}, 'invalid'):
            with self.assertRaisesRegex(ValueError, 'DOCUMENTS_ARRAY_REQUIRED'):
                validate_bundle(bundle, documents=value)
        result = validate_bundle(bundle, evidence=[None],
                                 documents=[dict(evidence_index=0, package=package())])
        self.assertEqual(result['document_checks'][0]['status'], 'INVALID')

    def test_empty_and_multiple_explicit_bindings_do_not_select_a_document(self):
        bundle = c1_bundle()
        self.assertEqual(validate_bundle(bundle)['document_checks'], [])
        item = dict(evidence_index=0, package=package())
        result = validate_bundle(bundle, evidence=[matching_assertion()], documents=[item, item])
        self.assertEqual([check['input_index'] for check in result['document_checks']], [0, 1])
        self.assertEqual([check['evidence_index'] for check in result['document_checks']], [0, 0])

    def test_private_serializer_blocks_unvalidated_public_exports(self):
        report = validate_package(package())
        with self.assertRaises(PublicExportBlocked):
            serialize(report, audience='public')
        self.assertEqual(json.loads(serialize(report)), report)
        malformed = dict(package(), unexpected='SENSITIVE_TEST_MARKER')
        self.assertNotIn('SENSITIVE_TEST_MARKER', serialize(validate_package(malformed)))

    def test_cli_reads_local_package_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fictional-document.json'
            original = json.dumps(package()).encode()
            path.write_bytes(original)
            output = subprocess.check_output([sys.executable, '-m', 'scripts.sec_document_evidence',
                                              '--input', str(path)], text=True)
            self.assertEqual(json.loads(output), validate_package(package()))
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
