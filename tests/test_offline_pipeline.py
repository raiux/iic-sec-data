import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import ValidationError, SchemaError
from scripts.offline_pipeline import build_plan, canonical_json, validator, BASE, PublicExportBlocked

CLOCK = datetime(2026, 11, 3, tzinfo=timezone.utc)
FIXTURE = json.loads((BASE / 'tests/fixtures/identity-fictional.json').read_text())[0]


def record():
    return copy.deepcopy(FIXTURE)


def receipt():
    r = record()
    r['instrument'].update(id='fictional-receipt-A', kind='ADR', isin='ZZ0000000001',
                           underlying=dict(issuer_id=r['issuer']['id'],
                                           identity=copy.deepcopy(r['issuer']['identity'])))
    r['listing'].update(id='fictional-receipt-listing', instrument_id='fictional-receipt-A',
                        ticker='FICT-ADR')
    return r


class PipelineTests(unittest.TestCase):
    def plan(self, rows):
        # Assert the orchestration never reaches any financial collector, even for ROUTABLE.
        with patch('scripts.collect_sec.collect', side_effect=AssertionError('Real SEC collector called')), \
             patch('urllib.request.urlopen', side_effect=AssertionError('Network request')):
            result = build_plan(json.dumps(rows), as_of=CLOCK)
        validator('collection-plan.schema.json').validate(result)
        return result

    def test_meta_schema_and_fixture_validation(self):
        validator('identity-input.schema.json').validate([record()])
        validator('collection-plan.schema.json').validate(self.plan([record()]))
        # Actual semantic and format checking, beyond json.tool.
        r = record()
        r['issuer']['identity']['checked_at'] = '2026-02-30T00:00:00Z'
        with self.assertRaises(ValidationError):
            validator('identity-input.schema.json').validate([r])
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'schemas').mkdir()
            Path(directory, 'schemas/bad.json').write_text('{"type":"not-a-json-schema-type"}')
            with patch('scripts.offline_pipeline.BASE', Path(directory)):
                with self.assertRaises(SchemaError):
                    validator('bad.json')

    def test_non_string_schema_references_are_controlled(self):
        for key in ('$ref', '$dynamicRef'):
            for value in (None, 42, True, [], {}):
                with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                    Path(directory, 'schemas').mkdir()
                    Path(directory, 'schemas/bad.json').write_text(json.dumps(
                        {'properties': {'nested': {key: value}}}))
                    with patch('scripts.offline_pipeline.BASE', Path(directory)):
                        with self.assertRaisesRegex(ValueError, 'Schema reference must be a string'):
                            validator('bad.json')

    def test_sensitive_markers_remain_private_and_public_export_is_blocked(self):
        marker = 'FICTITIOUS_PRIVATE_MARKER_DO_NOT_EXPORT'
        mutations = [('reference',), ('issuer-proof',), ('filing',), ('issuer-id',),
                     ('instrument-id',), ('listing-id',)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                r = record()
                if mutation == ('reference',):
                    r['source']['reference'] = marker
                elif mutation == ('issuer-proof',):
                    r['issuer']['identity']['ref'] = marker
                elif mutation == ('filing',):
                    r['source']['filing']['ref'] = marker
                elif mutation == ('issuer-id',):
                    r['issuer']['id'] = marker
                    r['instrument']['issuer_id'] = marker
                    r['source']['issuer_id'] = marker
                elif mutation == ('instrument-id',):
                    r['instrument']['id'] = marker
                    r['listing']['instrument_id'] = marker
                else:
                    r['listing']['id'] = marker
                validator('identity-input.schema.json').validate([r])
                result = self.plan([r])
                self.assertEqual(result['visibility'], 'PRIVATE')
                self.assertIn(marker, canonical_json(result))  # Private audit remains complete.
                with self.assertRaises(PublicExportBlocked) as blocked:
                    canonical_json(result, audience='public')
                self.assertNotIn(marker, str(blocked.exception))

    def test_cli_public_export_has_no_output_or_private_diagnostic(self):
        marker = 'FICTITIOUS_PRIVATE_MARKER_DO_NOT_EXPORT'
        r = record()
        r['source']['reference'] = marker
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'private.json')
            source.write_text(json.dumps([r]))
            command = [sys.executable, '-m', 'scripts.offline_pipeline', '--input', str(source),
                       '--as-of', '2026-11-03T00:00:00Z', '--audience', 'public']
            result = subprocess.run(command, cwd=BASE, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Public export blocked', result.stderr)
        self.assertNotIn(marker, result.stderr)

    def test_remote_schema_references_forbidden(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'schemas').mkdir()
            Path(directory, 'schemas/bad.json').write_text('{"$ref":"https://example.invalid/schema"}')
            with patch('scripts.offline_pipeline.BASE', Path(directory)):
                with self.assertRaises(ValueError):
                    validator('bad.json')

    def test_replay_byte_identical_and_input_unchanged(self):
        rows = [record()]
        before = copy.deepcopy(rows)
        a,b = self.plan(rows),self.plan(rows)
        self.assertEqual(canonical_json(a), canonical_json(b))
        self.assertEqual(rows,before)
        self.assertEqual(a['as_of'],'2026-11-03T00:00:00+00:00')
        self.assertEqual(a['decisions'][0]['quality']['source'],'CALLER_ASSERTIONS_ONLY')

    def test_shared_issuer_duplicate_and_receipt_no_ratio(self):
        result = self.plan([record(),record(),receipt()])
        self.assertEqual(len(result['collection_intents']),1)
        self.assertEqual(result['collection_intents'][0]['input_indices'],[0,1,2])
        self.assertEqual([d['collection_required'] for d in result['decisions']],[True,False,False])
        self.assertFalse(result['decisions'][2]['instrument_valuation_eligible'])
        self.assertIn('verified_shares_per_receipt', result['decisions'][2]['missing_fields'])

    def test_ratio_schema_error_preserves_issuer_only(self):
        for ratio in ('2',2,2.5,{'decimal':2,'identity':record()['issuer']['identity']}):
            r=receipt()
            r['instrument']['ratio']=ratio
            result=self.plan([r])
            self.assertTrue(result['decisions'][0]['issuer_collection_eligible'])
            self.assertFalse(result['decisions'][0]['instrument_valuation_eligible'])
            self.assertEqual(len(result['collection_intents']),1)
            self.assertEqual(result['decisions'][0]['quality']['structure'],'INVALID')

    def test_receipt_schema_invalid_never_enables_valuation(self):
        for field in ('ratio', 'rights'):
            for defect in ('compact_date', 'wrong_type', 'additional_field', 'terms_extra', 'proof_type'):
                with self.subTest(field=field, defect=defect):
                    r = receipt()
                    r['instrument'].update(
                        ratio=dict(decimal='2', identity=copy.deepcopy(r['issuer']['identity'])),
                        rights=copy.deepcopy(r['issuer']['identity']))
                    proof = (r['instrument']['ratio']['identity'] if field == 'ratio'
                             else r['instrument']['rights'])
                    if defect == 'compact_date':
                        proof['checked_at'] = '20261103T000000Z'
                    elif defect == 'wrong_type':
                        proof['checked_at'] = 20261103
                    elif defect == 'additional_field':
                        proof['unexpected'] = 'fictional-extra'
                    elif defect == 'terms_extra':
                        r['instrument'][field]['unexpected_term'] = 'fictional-extra'
                    elif field == 'ratio':
                        r['instrument']['ratio']['identity'] = []
                    else:
                        r['instrument']['rights'] = []
                    result = self.plan([r])
                    decision = result['decisions'][0]
                    self.assertEqual(decision['quality']['structure'], 'INVALID')
                    self.assertFalse(decision['instrument_valuation_eligible'])
                    self.assertTrue(decision['issuer_collection_eligible'])
                    self.assertTrue(decision['collection_required'])
                    self.assertEqual(len(result['collection_intents']), 1)
                    self.assertTrue(any(error['path'].startswith('instrument/' + field)
                                        for error in decision['schema_errors']))

    def test_valid_receipt_requires_all_other_b1_gates(self):
        r = receipt()
        r['instrument'].update(
            ratio=dict(decimal='2', identity=copy.deepcopy(r['issuer']['identity'])),
            rights=copy.deepcopy(r['issuer']['identity']))
        decision = self.plan([r])['decisions'][0]
        self.assertEqual(decision['quality']['structure'], 'VALID')
        self.assertTrue(decision['instrument_valuation_eligible'])
        r['source']['official_check']['result'] = 'UNVERIFIED'
        result = self.plan([r])
        self.assertFalse(result['decisions'][0]['instrument_valuation_eligible'])
        self.assertEqual(result['collection_intents'], [])

    def test_verified_decimal_never_float_in_output(self):
        r=receipt()
        r['instrument'].update(ratio={'decimal':'0.0000000000000000000123456789',
                                     'identity':r['issuer']['identity']},
                               rights=r['issuer']['identity'])
        result=self.plan([r])
        self.assertTrue(result['decisions'][0]['instrument_valuation_eligible'])
        # Output decisions do not serialize accounting/ratio values.
        self.assertNotIn('shares_per_receipt',canonical_json(result))

    def test_funds_before_source_even_when_malformed(self):
        for kind in ('ETF','ETN','FUND'):
            r=record()
            r['instrument']['kind']=kind
            r['source']=None
            result=self.plan([r])
            self.assertEqual(result['decisions'][0]['status'],'EXCLUDED')
            self.assertEqual(result['collection_intents'],[])

    def test_unknown_conflict_and_delisting(self):
        r=record()
        r['instrument']['kind']='UNKNOWN'
        self.assertEqual(self.plan([r])['decisions'][0]['status'],'NEEDS_REVIEW')
        r=receipt()
        del r['instrument']['underlying']
        self.assertEqual(self.plan([r])['decisions'][0]['status'],'NEEDS_REVIEW')
        r=record()
        r['listing']['status']='DELISTED'
        self.assertEqual(self.plan([r])['decisions'][0]['status'],'ARCHIVED')
        a,b=record(),record()
        b['instrument']['isin']='ZZ0000000001'
        for rows in ([a,b],[b,a]):
            result=self.plan(rows)
            self.assertEqual(result['collection_intents'],[])
            self.assertTrue(all(d['status']=='NEEDS_REVIEW' for d in result['decisions']))

    def test_source_evidence_and_unauthorized(self):
        for kind, expected in [('missing','UNVERIFIED'),('denied','BLOCKED'),
                               ('unavailable','BLOCKED'),('unchecked','UNVERIFIED'),
                               ('date','NEEDS_REVIEW')]:
            r=record()
            if kind=='missing':del r['source']
            elif kind=='denied':r['source']['access']['authorization']='DENIED'
            elif kind=='unavailable':r['source']['access']['result']='UNAVAILABLE'
            elif kind=='unchecked':r['source']['official_check']['result']='UNVERIFIED'
            else:r['source']['access']['identity']['checked_at']='bad'
            result=self.plan([r])
            self.assertEqual(result['decisions'][0]['status'],expected)
            self.assertEqual(result['collection_intents'],[])

    def test_public_output_never_echoes_unrecognized_fields(self):
        for name in ('quantity','positions','cost_basis','api_key','portfolio','private_token'):
            r=record()
            r['instrument'][name]='DO_NOT_PUBLISH'
            result=self.plan([r])
            serialized=canonical_json(result)
            self.assertNotIn('DO_NOT_PUBLISH',serialized)
            self.assertNotIn(name,serialized)
            self.assertEqual(result['collection_intents'],[])
            self.assertEqual(result['decisions'][0]['status'],'NEEDS_REVIEW')

    def test_provenance_distinguishes_access_origin_and_xbrl(self):
        r=record()
        result=self.plan([r])
        evidence={p['role']:p for p in result['decisions'][0]['provenance']}
        self.assertEqual(evidence['source.reference']['ref'],r['source']['reference'])
        self.assertEqual(evidence['source.access']['checked_at'],r['source']['access']['identity']['checked_at'])
        self.assertEqual(evidence['source.access.result']['ref'],'AVAILABLE')
        self.assertEqual(evidence['source.xbrl.taxonomy']['ref'],'us-gaap')
        self.assertEqual(result['source_verification'],'NOT_PERFORMED')
        self.assertNotIn('observations',result)

    def test_malformed_json_and_contract(self):
        for text in ('{','{"x":1,"x":2}','[NaN]','[Infinity]','{}'):
            result=build_plan(text,as_of=CLOCK)
            self.assertEqual(result['status'],'REJECTED')
            self.assertEqual(result['collection_intents'],[])
        for bad in (None,{},[],2,{'instrument':{}}):
            result=self.plan([bad])
            self.assertEqual(result['decisions'][0]['status'],'NEEDS_REVIEW')
        with self.assertRaises(ValueError):
            build_plan('[]',as_of=datetime(2026,11,3))

    def test_output_schema_rejects_fabricated_fields_and_gate_violation(self):
        result=self.plan([record()])
        result['observations']=[]
        with self.assertRaises(ValidationError):
            validator('collection-plan.schema.json').validate(result)
        result=self.plan([record()])
        result['decisions'][0].update(status='EXCLUDED',issuer_collection_eligible=True)
        with self.assertRaises(ValidationError):
            validator('collection-plan.schema.json').validate(result)

    def test_output_contract_forbids_invalid_structure_with_valuation(self):
        result = self.plan([record()])
        result['decisions'][0]['quality']['structure'] = 'INVALID'
        self.assertTrue(result['decisions'][0]['instrument_valuation_eligible'])
        with self.assertRaises(ValidationError):
            validator('collection-plan.schema.json').validate(result)

    def test_network_guard_rejects_actual_lookup_event(self):
        code = ("import sys,socket; from scripts.run_offline_tests import deny_network; "
                "sys.addaudithook(deny_network); socket.getaddrinfo('example.invalid',443)")
        result = subprocess.run([sys.executable,'-c',code],cwd=BASE,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Network access forbidden',result.stderr)

    def test_missing_fields_and_future_proof(self):
        r=record()
        del r['source']
        self.assertIn('source',self.plan([r])['decisions'][0]['missing_fields'])
        r=receipt()
        del r['instrument']['underlying']
        self.assertIn('instrument/underlying',self.plan([r])['decisions'][0]['missing_fields'])
        r=record()
        r['source']['access']['identity']['checked_at']='2099-01-01T00:00:00Z'
        result=self.plan([r])
        self.assertEqual(result['decisions'][0]['status'],'UNVERIFIED')
        self.assertEqual(result['collection_intents'],[])
        r=record()
        del r['listing']['mic']
        self.assertIn('listing/mic',self.plan([r])['decisions'][0]['missing_fields'])

    def test_cli_entrypoint(self):
        cmd=[sys.executable,'-m','scripts.offline_pipeline','--input',
             str(BASE/'tests/fixtures/identity-fictional.json'),'--as-of','2026-11-03T00:00:00Z']
        output=subprocess.check_output(cmd,cwd=BASE,text=True)
        self.assertEqual(json.loads(output)['collection_intents'][0]['state'],'SIMULATED')
        self.assertEqual(output,canonical_json(self.plan([record()])))

    def test_esef_and_canadian_sec_use_same_pipeline(self):
        for adapter,taxonomy in (('ESEF','ifrs-full'),('SEC_COMPANYFACTS','ifrs-full')):
            r=record()
            r['source']['adapter']=adapter
            r['source']['xbrl']['taxonomy']=taxonomy
            result=self.plan([r])
            self.assertEqual(result['decisions'][0]['adapter'],adapter)
            self.assertEqual(len(result['collection_intents']),1)
