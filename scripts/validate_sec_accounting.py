"""C2 offline accounting checks; caller assertions never become official proof."""
import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from scripts.normalize_sec import ANNUAL_FORMS, MAPPING, REPORTING_CURRENCIES, normalize_snapshot, parse_raw
from scripts.offline_pipeline import PublicExportBlocked, canonical_json, validator

BASE = Path(__file__).resolve().parents[1]
ACCESSION = re.compile(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', re.ASCII)
SHA256 = re.compile(r'[a-f0-9]{64}', re.ASCII)
SHARE_METRICS = {'diluted_average_shares_reported', 'outstanding_shares_reported'}


def _tokens(pointer):
    if type(pointer) is not str or not pointer.startswith('/'):
        raise ValueError('RAW_POINTER_INVALID')
    return [part.replace('~1', '/').replace('~0', '~')
            for part in pointer.split('/')[1:]]


def _point(document, pointer):
    value = document
    for token in _tokens(pointer):
        if isinstance(value, list):
            if not token.isascii() or not token.isdigit():
                raise ValueError('RAW_POINTER_INVALID')
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise ValueError('RAW_POINTER_INVALID')
    return value


def _expected_taxonomy(cik, taxonomy_by_cik):
    value = taxonomy_by_cik.get(cik)
    return value if type(value) is str and value in {'us-gaap', 'ifrs-full'} else None


def _sec_archive_ref(record):
    url = record.get('document_url')
    if type(url) is not str:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != 'https' or parsed.netloc.lower() != 'www.sec.gov' or parsed.query or parsed.fragment:
        return False
    accession_path = record.get('accession', '').replace('-', '')
    cik_path = str(int(record.get('issuer_cik', '0'))) if record.get('issuer_cik', '').isdigit() else ''
    expected_prefix = f'/Archives/edgar/data/{cik_path}/{accession_path}/'
    return bool(cik_path and parsed.path.startswith(expected_prefix) and len(parsed.path) > len(expected_prefix))


def _evidence_errors(record):
    if type(record) is not dict:
        return ['EVIDENCE_OBJECT_REQUIRED']
    required = {'issuer_cik', 'accession', 'form', 'filed', 'taxonomy',
                'document_url', 'document_sha256', 'facts'}
    if set(record) != required:
        return ['EVIDENCE_FIELDS_INVALID']
    if type(record['issuer_cik']) is not str or not re.fullmatch(r'[0-9]{10}', record['issuer_cik'], re.ASCII):
        return ['EVIDENCE_CIK_INVALID']
    if type(record['accession']) is not str or not ACCESSION.fullmatch(record['accession']):
        return ['EVIDENCE_ACCESSION_INVALID']
    if type(record['form']) is not str or record['form'] not in ANNUAL_FORMS:
        return ['EVIDENCE_FORM_INVALID']
    try:
        date.fromisoformat(record['filed'])
    except (TypeError, ValueError):
        return ['EVIDENCE_DATE_INVALID']
    if type(record['taxonomy']) is not str or record['taxonomy'] not in {'ifrs-full', 'us-gaap'}:
        return ['EVIDENCE_TAXONOMY_INVALID']
    if type(record['document_sha256']) is not str or not SHA256.fullmatch(record['document_sha256']):
        return ['EVIDENCE_SHA256_INVALID']
    if not _sec_archive_ref(record):
        return ['EVIDENCE_REFERENCE_INVALID']
    if type(record['facts']) is not list:
        return ['EVIDENCE_FACTS_INVALID']
    for fact in record['facts']:
        if type(fact) is not dict or set(fact) != {
                'namespace', 'concept', 'unit', 'value', 'start', 'end', 'dimensions'}:
            return ['EVIDENCE_FACT_INVALID']
        if any(type(fact[name]) is not str for name in ('namespace', 'concept', 'unit', 'value', 'end')):
            return ['EVIDENCE_FACT_INVALID']
        if fact['start'] is not None and type(fact['start']) is not str:
            return ['EVIDENCE_FACT_INVALID']
        if type(fact['dimensions']) is not dict:
            return ['EVIDENCE_FACT_INVALID']
    return []


def _assertion_match(row, observation, evidence):
    if not _sec_archive_ref(evidence):
        return 'INVALID_REFERENCE'
    if (evidence['issuer_cik'] != observation['cik']
            or evidence['accession'] != row.get('accn')
            or evidence['form'] != row.get('form')
            or evidence['filed'] != row.get('filed')):
        return 'CONFLICT'
    if evidence['taxonomy'] != observation['namespace']:
        return 'CONFLICT'
    normalized = observation.get('normalized') or {}
    unit = normalized.get('unit')
    start = row.get('start')
    matches = [fact for fact in evidence['facts'] if (
        fact['namespace'] == observation['namespace']
        and fact['concept'] == observation['concept']
        and fact['unit'] == unit
        and fact['value'] == str(row.get('val'))
        and fact['start'] == start
        and fact['end'] == row.get('end')
        and fact['dimensions'] == (row.get('dimensions') or {}))]
    if not matches:
        return 'CONFLICT'
    # The manifest has no filing bytes or signed acquisition receipt. Matching
    # assertions are useful diagnostics, never authentication of a filing.
    return 'ASSERTIONS_MATCH_UNAUTHENTICATED'


def _taxonomy_from_bundle(bundle):
    result = {}
    for issuer in bundle.get('issuers', []):
        expected = set()
        for instrument in issuer.get('instruments', []):
            for item in instrument.get('provenance', []):
                if (item.get('role') == 'source.xbrl.taxonomy'
                        and type(item.get('ref')) is str
                        and item['ref'] in {'us-gaap', 'ifrs-full'}):
                    expected.add(item['ref'])
        if len(expected) == 1:
            result[issuer['cik']] = next(iter(expected))
    return result


def validate_normalization(normalization, *, evidence=(), taxonomy_by_cik=None):
    """Recheck C1 raw pointers and claims; report evidence comparisons conservatively."""
    if type(normalization) is not dict or type(normalization.get('raw_document')) is not str:
        raise ValueError('C1_NORMALIZATION_REQUIRED')
    if type(evidence) is not list and type(evidence) is not tuple:
        raise ValueError('EVIDENCE_ARRAY_REQUIRED')
    if (type(normalization.get('source')) is not dict
            or type(normalization.get('observations')) is not list
            or type(normalization.get('coverage')) is not list
            or any(type(item) is not dict for item in normalization['observations'])
            or any(type(item) is not dict for item in normalization['coverage'])):
        raise ValueError('C1_NORMALIZATION_SHAPE_INVALID')
    taxonomy_by_cik = taxonomy_by_cik or {}
    payload = normalization['raw_document']
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    hash_valid = (normalization.get('source', {}).get('sha256') == digest
                  and all(obs.get('source_sha256') == digest
                          for obs in normalization.get('observations', [])))
    try:
        raw = parse_raw(payload)
    except (ValueError, TypeError):
        raw = None
    try:
        as_of = date.fromisoformat(normalization['as_of'])
    except (KeyError, TypeError, ValueError):
        as_of = None

    # Rebuild the C1 grouping from the retained source; caller-supplied
    # groups can be incomplete or have their conflict status overwritten.
    groups_from_raw = []
    if raw is not None and as_of is not None:
        groups_from_raw = normalize_snapshot(
            payload, as_of=as_of, source_locator=normalization['source']['locator']
        )['groups']

    valid_evidence = []
    invalid_evidence = []
    for index, record in enumerate(evidence):
        issues = _evidence_errors(record)
        if issues:
            invalid_evidence.append(dict(input_index=index, status='INVALID', reasons=issues))
        else:
            valid_evidence.append(record)
    output_observations = []
    by_cik = {}
    for observation in normalization.get('observations', []):
        cik = observation.get('cik') if type(observation) is dict else None
        if type(cik) is str:
            by_cik.setdefault(cik, []).append(observation)
        pointer = observation.get('raw_pointer') if type(observation) is dict else None
        reasons = []
        try:
            row = _point(raw, pointer) if raw is not None else None
        except (KeyError, IndexError, ValueError, TypeError):
            row = None
        # Decode the unit from the same pointer and verify every identity segment.
        try:
            parts = _tokens(pointer)
            pointer_valid = (type(row) is dict and len(parts) == 8
                             and parts[0] == 'companies' and parts[2] == 'facts'
                             and parts[3] == observation.get('namespace')
                             and parts[4] == observation.get('concept') and parts[5] == 'units'
                             and type(parts[1]) is str and parts[1].isascii() and parts[1].isdigit()
                             and parts[7].isascii() and parts[7].isdigit())
            pointer_unit = parts[6]
            company = raw['companies'][int(parts[1])] if pointer_valid else None
            pointer_valid = (pointer_valid and company.get('cik') == cik
                             and observation.get('id') == hashlib.sha256(
                                 (digest + pointer).encode('utf-8')).hexdigest()
                             and str(int(parts[1])) == parts[1] and str(int(parts[7])) == parts[7])
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            pointer_valid, pointer_unit, company = False, None, None
        if not pointer_valid:
            reasons.append('RAW_POINTER_INVALID')
        if not hash_valid or observation.get('source_sha256') != digest:
            reasons.append('RAW_SHA256_MISMATCH')

        metric, nature = MAPPING.get(parts[3], {}).get(parts[4], (None, None)) if pointer_valid else (None, None)
        normalized = observation.get('normalized')
        if not isinstance(normalized, dict):
            normalized = None
        row_consistent = bool(pointer_valid and normalized is not None
                              and normalized.get('value') == str(row.get('val'))
                              and normalized.get('metric') == metric and normalized.get('nature') == nature
                              and normalized.get('unit') == pointer_unit
                              and normalized.get('start') == row.get('start')
                              and normalized.get('end') == row.get('end')
                              and normalized.get('accession') == row.get('accn')
                              and normalized.get('filed') == row.get('filed')
                              and normalized.get('form') == row.get('form')
                              and normalized.get('reported_fy') == row.get('fy')
                              and normalized.get('reported_fp') == row.get('fp'))
        if observation.get('structure') == 'CANDIDATE' and not row_consistent:
            reasons.append('NORMALIZED_RAW_MISMATCH')

        expected_tax = _expected_taxonomy(cik, taxonomy_by_cik)
        tax_status = 'UNCHECKED'
        if expected_tax is not None:
            tax_status = ('CONSISTENT_WITH_B3_ASSERTION' if observation.get('namespace') == expected_tax
                          or observation.get('namespace') == 'dei' else 'CONFLICT')
        if tax_status == 'CONFLICT':
            reasons.append('TAXONOMY_CONFLICT')

        accession = row.get('accn') if type(row) is dict else None
        accession_status = 'PASS' if type(accession) is str and ACCESSION.fullmatch(accession) else 'FAIL'
        if accession_status == 'FAIL':
            reasons.append('ACCESSION_INVALID')
        form = row.get('form') if type(row) is dict else None
        form_status = 'PASS' if form in ANNUAL_FORMS else 'OUT_OF_SCOPE'
        if form_status != 'PASS':
            reasons.append('FORM_OUT_OF_SCOPE')
        filed = end = start = None
        try:
            filed = date.fromisoformat(row['filed'])
            end = date.fromisoformat(row['end'])
            start = date.fromisoformat(row['start']) if 'start' in row else None
        except (KeyError, TypeError, ValueError):
            pass
        period_status = 'PASS'
        if filed is None or end is None or end > filed or as_of is None or filed > as_of:
            period_status = 'FAIL'
        elif nature == 'DURATION':
            if start is None or not 300 <= (end - start).days <= 380:
                period_status = 'OUT_OF_SCOPE'
        elif nature == 'INSTANT' and start is not None:
            period_status = 'OUT_OF_SCOPE'
        if period_status != 'PASS':
            reasons.append('PERIOD_INVALID_OR_OUT_OF_SCOPE')

        fy = row.get('fy') if type(row) is dict else None
        fp = row.get('fp') if type(row) is dict else None
        if fy is not None and (type(fy) is not int or not 1900 <= fy <= 2100):
            exercise_status = 'FAIL'
            reasons.append('FISCAL_YEAR_INVALID')
        elif fp is not None and (type(fp) is not str or fp != 'FY'):
            exercise_status = 'CONFLICT'
            reasons.append('FISCAL_PERIOD_NOT_ANNUAL')
        elif fy is None or fp is None:
            exercise_status = 'UNCHECKED_MISSING_REPORTED_FY_FP'
        else:
            exercise_status = 'REPORTED_FY_FP_PRESENT_UNVERIFIED'

        dimensions = row.get('dimensions') or row.get('segment') if type(row) is dict else None
        dimensions_status = 'OUT_OF_SCOPE' if dimensions else 'NO_EXPLICIT_DIMENSIONS'
        scope_status = 'UNCHECKED'  # CompanyFacts does not prove consolidation/parent attribution.
        unit_allowed = (pointer_unit == 'shares' if metric in SHARE_METRICS
                        else pointer_unit in REPORTING_CURRENCIES)
        unit_status = 'PASS' if unit_allowed else 'FAIL'
        if not unit_allowed:
            reasons.append('UNIT_NOT_ADMISSIBLE')
        attribution_status = ('UNCHECKED_PARENT_ATTRIBUTION'
                              if metric == 'net_income_reported' else 'UNCHECKED_SCOPE')

        matching_evidence = [item for item in valid_evidence
                             if item['issuer_cik'] == cik and item['accession'] == accession]
        if not matching_evidence:
            invalid_for_row = any(type(item) is dict
                                  and item.get('issuer_cik') == cik
                                  and item.get('accession') == accession
                                  for item in evidence)
            evidence_status = ('INVALID_RECORD' if invalid_for_row else
                               'INVALID_EVIDENCE_PRESENT' if invalid_evidence else 'UNCHECKED_MISSING')
            evidence_ref = None
        elif len(matching_evidence) > 1:
            claims = {_assertion_match(row, observation, item) for item in matching_evidence}
            evidence_status = 'CONFLICT' if len(claims) > 1 or 'CONFLICT' in claims else 'ASSERTIONS_MATCH_UNAUTHENTICATED'
            evidence_ref = sorted(item['document_url'] for item in matching_evidence)[0]
        else:
            evidence_status = _assertion_match(row, observation, matching_evidence[0])
            evidence_ref = matching_evidence[0]['document_url']
        if evidence_status in {'CONFLICT', 'INVALID_REFERENCE'}:
            reasons.append('FILING_EVIDENCE_' + evidence_status)
        if evidence_status == 'INVALID_RECORD':
            reasons.append('FILING_EVIDENCE_INVALID_RECORD')

        form_text = form if type(form) is str else ''
        amendment = 'REVIEW_REQUIRED' if form_text.endswith('/A') else 'AMENDMENT_STATUS_UNCHECKED'
        group_statuses = sorted({group.get('status') for group in groups_from_raw
                                 if observation.get('id') in group.get('observation_ids', [])})
        if 'CONFLICT' in group_statuses:
            reasons.append('CONFLICT_UNARBITRATED')
        if observation.get('structure') != 'CANDIDATE':
            reasons.append('C1_NOT_CANDIDATE')

        conflicts = {'CONFLICT_UNARBITRATED', 'NORMALIZED_RAW_MISMATCH',
                     'RAW_POINTER_INVALID', 'RAW_SHA256_MISMATCH', 'ACCESSION_INVALID', 'TAXONOMY_CONFLICT',
                     'FILING_EVIDENCE_CONFLICT', 'FILING_EVIDENCE_INVALID_REFERENCE',
                     'FILING_EVIDENCE_INVALID_RECORD',
                     'FISCAL_YEAR_INVALID', 'FISCAL_PERIOD_NOT_ANNUAL',
                     'UNIT_NOT_ADMISSIBLE'}
        status = 'CONFLICT' if (conflicts.intersection(reasons)
                                or evidence_status in {'CONFLICT', 'INVALID_REFERENCE'}
                                or period_status == 'FAIL' or form_status == 'OUT_OF_SCOPE') else 'UNCHECKED'
        output_observations.append(dict(
            id=observation.get('id'), cik=cik, namespace=observation.get('namespace'),
            concept=observation.get('concept'), metric=metric, structure=observation.get('structure'),
            raw_pointer=pointer, source_sha256=observation.get('source_sha256'),
            normalized=observation.get('normalized'),
            checks=dict(raw_pointer='PASS' if pointer_valid else 'FAIL',
                        raw_sha256='PASS' if hash_valid else 'FAIL',
                        raw_normalized='PASS' if row_consistent else 'UNCHECKED',
                        accession=accession_status, form=form_status, period=period_status,
                        fiscal_exercise=exercise_status,
                        dimensions=dimensions_status, unit=unit_status,
                        taxonomy=tax_status, consolidation_scope=scope_status,
                        parent_attribution=attribution_status, amendment=amendment,
                        official_filing=evidence_status),
            filing_evidence_reference=evidence_ref, status=status,
            reasons=sorted(set(reasons)), accounting_validation=status,
            valuation_usable=False))

    output_issuers = []
    for coverage in normalization.get('coverage', []):
        cik = coverage['cik']
        rows = [item for item in output_observations if item['cik'] == cik]
        group_ids = {item['id'] for item in rows}
        groups = [group for group in groups_from_raw
                  if group.get('cik') == cik and set(group.get('observation_ids', [])) & group_ids]
        blockers = {'OFFICIAL_FILING_AUTHENTICITY_UNCHECKED', 'ACCOUNTING_VALIDATION_UNCHECKED',
                    'CONSOLIDATION_AND_PARENT_SCOPE_UNCHECKED'}
        if any(group.get('status') == 'CONFLICT' for group in groups):
            blockers.add('CONFLICT_UNARBITRATED')
        if any(group.get('status') == 'REPEATED_VALUE_CANDIDATES' for group in groups):
            blockers.add('REPEATED_CANDIDATES_UNSELECTED')
        if any(item['checks']['amendment'] == 'REVIEW_REQUIRED' for item in rows):
            blockers.add('AMENDMENT_REVIEW_REQUIRED')
        issuer_validation = ('CONFLICT' if any(item['status'] == 'CONFLICT' for item in rows)
                             else 'UNCHECKED')
        output_issuers.append(dict(
            cik=cik, taxonomy=_expected_taxonomy(cik, taxonomy_by_cik),
            observations=rows, groups=groups, coverage=coverage,
            accounting_validation=issuer_validation, valuation_usable=False,
            valuation_blockers=sorted(blockers), instruments=[]))

    overall_validation = ('CONFLICT' if any(item['status'] == 'CONFLICT'
                                             for item in output_observations) else 'UNCHECKED')
    result = dict(schema_version='iic.c2-accounting-validation.v1', visibility='PRIVATE',
                  source_verification='NOT_PERFORMED', accounting_validation=overall_validation,
                  valuation_usable=False,
                  source_snapshot=dict(locator=normalization.get('source', {}).get('locator'),
                                       sha256=digest, raw_document=payload),
                  evidence_input_status=('PRESENT_UNAUTHENTICATED' if evidence else 'NOT_PROVIDED'),
                  invalid_evidence=invalid_evidence,
                  observations=output_observations, issuers=output_issuers,
                  limitations=['OFFICIAL_FILING_BYTES_NOT_AUTHENTICATED',
                                'CONSOLIDATION_AND_PARENT_ATTRIBUTION_NOT_PROVEN',
                                'NO_VALUATION_UNTIL_INDEPENDENT_ACCOUNTING_REVIEW'])
    validator('c2-accounting-validation.schema.json').validate(result)
    return result


def validate_bundle(bundle, *, evidence=()):
    """C2 over B3→C1, carrying separate B3 issuer and instrument gates forward."""
    validator('b3-c1-integration.schema.json').validate(bundle)
    taxonomy_by_cik = _taxonomy_from_bundle(bundle)
    normalization = bundle.get('normalization')
    if normalization is None:
        result = dict(schema_version='iic.c2-accounting-validation.v1', visibility='PRIVATE',
                      source_verification='NOT_PERFORMED', accounting_validation='UNCHECKED',
                      valuation_usable=False, source_snapshot=None,
                      evidence_input_status=('PRESENT_UNAUTHENTICATED' if evidence else 'NOT_PROVIDED'),
                      invalid_evidence=[], observations=[], issuers=[],
                      limitations=['OFFICIAL_FILING_BYTES_NOT_AUTHENTICATED',
                                    'CONSOLIDATION_AND_PARENT_ATTRIBUTION_NOT_PROVEN',
                                    'NO_VALUATION_UNTIL_INDEPENDENT_ACCOUNTING_REVIEW'])
        validator('c2-accounting-validation.schema.json').validate(result)
        return result
    result = validate_normalization(normalization, evidence=evidence,
                                   taxonomy_by_cik=taxonomy_by_cik)
    instrument_by_cik = {}
    for issuer in bundle.get('issuers', []):
        instrument_by_cik.setdefault(issuer['cik'], []).extend(issuer.get('instruments', []))
    eligible_ciks = set(instrument_by_cik)
    result['observations'] = [item for item in result['observations']
                              if item['cik'] in eligible_ciks]
    result['issuers'] = [item for item in result['issuers'] if item['cik'] in eligible_ciks]
    present_ciks = {issuer['cik'] for issuer in result['issuers']}
    for source_issuer in bundle.get('issuers', []):
        cik = source_issuer['cik']
        if cik not in present_ciks:
            result['issuers'].append(dict(
                cik=cik, taxonomy=taxonomy_by_cik.get(cik), observations=[], groups=[], coverage=None,
                accounting_validation='UNCHECKED', valuation_usable=False,
                valuation_blockers=['C1_CIK_NOT_PRESENT_IN_SNAPSHOT',
                                    'ACCOUNTING_VALIDATION_UNCHECKED',
                                    'OFFICIAL_FILING_AUTHENTICITY_UNCHECKED'],
                instruments=[]))
    for issuer in result['issuers']:
        for instrument in instrument_by_cik.get(issuer['cik'], []):
            item = dict(instrument)
            item['provenance_assertion_status'] = 'CALLER_ASSERTIONS_ONLY_NOT_REVALIDATED'
            item['valuation_usable'] = False
            item['valuation_blockers'] = sorted(set(item.get('valuation_blockers', [])) |
                                                {'ACCOUNTING_VALIDATION_UNCHECKED',
                                                 'OFFICIAL_FILING_AUTHENTICITY_UNCHECKED'})
            issuer['instruments'].append(item)
    result['issuers'].sort(key=lambda item: item['cik'])
    result['accounting_validation'] = (
        'CONFLICT' if any(item['status'] == 'CONFLICT' for item in result['observations'])
        else 'UNCHECKED')
    validator('c2-accounting-validation.schema.json').validate(result)
    return result


def serialize(report, *, audience='private'):
    """The C2 report has no public projection; use B3's fail-closed serializer."""
    return canonical_json(report, audience=audience)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Private B3→C1 JSON bundle')
    parser.add_argument('--evidence', help='Optional private filing-assertion JSON array')
    args = parser.parse_args()
    try:
        bundle = json.loads(Path(args.input).read_text(encoding='utf-8'))
        evidence = json.loads(Path(args.evidence).read_text(encoding='utf-8')) if args.evidence else []
        result = validate_bundle(bundle, evidence=evidence)
    except (OSError, ValueError, json.JSONDecodeError):
        parser.error('Unreadable input or invalid local C2 contract')
    print(serialize(result), end='')


if __name__ == '__main__':
    main()
