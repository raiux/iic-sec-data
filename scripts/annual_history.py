"""Pure offline annual history. No persistence, authentication or calculation engine."""
import base64
import copy
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from jsonschema import ValidationError
from scripts.normalize_financials import extract_facts, period as esef_period
from scripts.normalize_sec import normalize_snapshot, parse_raw
from scripts.offline_pipeline import canonical_json, validator
from scripts.validate_sec_accounting import validate_normalization


MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _schema(value):
    try:
        validator('annual-history.schema.json').validate(value)
    except ValidationError as exc:
        raise ValueError('HISTORY_CONTRACT_INVALID') from exc


def _stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def _bytes(source):
    encoded = source['raw_bytes_base64']
    if len(encoded) > 4 * ((MAX_SOURCE_BYTES + 2) // 3):
        raise ValueError('SOURCE_SIZE_LIMIT')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError('SOURCE_BYTES_INVALID') from exc
    if not raw or len(raw) > MAX_SOURCE_BYTES or base64.b64encode(raw).decode() != encoded:
        raise ValueError('SOURCE_BYTES_INVALID')
    if hashlib.sha256(raw).hexdigest() != source['declared_sha256']:
        raise ValueError('SOURCE_SHA256_MISMATCH')
    return raw


def _annual(start, end):
    try:
        return (type(start) is str and type(end) is str
                and 300 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 380)
    except ValueError:
        return False


def _amount(value):
    try:
        amount = Decimal(value) if type(value) is str else None
        return amount if amount is not None and amount.is_finite() else None
    except InvalidOperation:
        return None


def _json_value(value):
    # Decimal text is exact; the original lexical representation stays in raw bytes.
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _extract(report, raw):
    source, issuer = report['source'], report['issuer']
    try:
        payload = raw.decode('utf-8')
        document = parse_raw(payload)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('SOURCE_JSON_INVALID') from exc
    observations = []
    validation = 'UNCHECKED'
    if source['format'] == 'SEC_SNAPSHOT':
        if issuer['scheme'] != 'CIK':
            raise ValueError('SOURCE_IDENTITY_SCHEME_MISMATCH')
        normalized = normalize_snapshot(payload, as_of=date.fromisoformat(source['filed_at']),
                                        source_locator=source['locator'])
        c2 = validate_normalization(normalized)
        companies = [(i, company) for i, company in enumerate(document['companies'])
                     if company['cik'] == issuer['identifier']]
        if len(companies) != 1:
            raise ValueError('SOURCE_ISSUER_MISSING_OR_AMBIGUOUS')
        c2_issuer = next(item for item in c2['issuers'] if item['cik'] == issuer['identifier'])
        validation = c2_issuer['accounting_validation']
        for item in c2_issuer['observations']:
            row = document
            for part in item['raw_pointer'].split('/')[1:]:
                part = part.replace('~1', '/').replace('~0', '~')
                row = row[int(part)] if isinstance(row, list) else row[part]
            row = row if type(row) is dict else {}
            unit = item['raw_pointer'].split('/')[-2].replace('~1', '/').replace('~0', '~')
            observations.append(dict(
                source_fact_id=item['id'], source_pointer=item['raw_pointer'],
                concept=item['namespace'] + ':' + item['concept'], unit=unit,
                start=row.get('start'), end=row.get('end'),
                value=str(row['val']) if type(row.get('val')) in (int, Decimal) else None,
                dimensions=_json_value(row.get('dimensions') or row.get('segment') or {}),
                source_accession=row.get('accn'), source_filed_at=row.get('filed'),
                accounting_validation=item['accounting_validation'],
                reasons=item['reasons']))
    else:
        if issuer['scheme'] != 'LEI' or type(document) is not dict or document.get('lei') != issuer['identifier']:
            raise ValueError('SOURCE_ISSUER_MISSING_OR_AMBIGUOUS')
        try:
            facts, _ = extract_facts(document)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('ESEF_FACTS_INVALID') from exc
        pointers = ([f'/matching_facts/{index}' for index in range(len(facts))]
                    if isinstance(document.get('matching_facts'), list) else
                    ['/facts/' + key.replace('~', '~0').replace('/', '~1')
                     for key in document['facts']])
        for index, fact in enumerate(facts):
            if type(fact) is not dict:
                raise ValueError('ESEF_FACTS_INVALID')
            required = ('concept', 'unit', 'period', 'entity')
            if any(type(fact.get(key)) is not str for key in required):
                raise ValueError('ESEF_FACTS_INVALID')
            parsed_period = esef_period(fact['period'])
            value = str(fact['value']) if type(fact.get('value')) in (str, int, Decimal) else None
            reasons = []
            if _amount(value) is None:
                reasons.append('INVALID_VALUE')
            if parsed_period is None:
                reasons.append('INVALID_PERIOD')
            if fact['entity'] != 'scheme:' + issuer['identifier']:
                reasons.append('ENTITY_ASSERTION_DIVERGES')
            observations.append(dict(
                source_fact_id=fact.get('fact_id'), source_pointer=pointers[index],
                concept=fact['concept'], unit=fact['unit'],
                start=parsed_period[1] if parsed_period and parsed_period[0] == 'duration' else None,
                end=parsed_period[-1] if parsed_period else None,
                value=value, dimensions=_json_value(fact.get('dimensions', {})),
                source_accession=None, source_filed_at=source['filed_at'],
                accounting_validation='CONFLICT' if 'ENTITY_ASSERTION_DIVERGES' in reasons else 'UNCHECKED',
                reasons=reasons))
        if any(item['accounting_validation'] == 'CONFLICT' for item in observations):
            validation = 'CONFLICT'
    return observations, dict(source_verification='NOT_PERFORMED',
                              accounting_validation=validation, valuation_usable=False)


def _identity(issuer):
    if (type(issuer) is not dict or set(issuer) != {'scheme', 'identifier'}
            or issuer.get('scheme') not in ('CIK', 'LEI')
            or type(issuer.get('identifier')) is not str
            or not re.fullmatch(r'[0-9]{10}' if issuer['scheme'] == 'CIK' else r'[A-Z0-9]{20}',
                                issuer['identifier'])):
        raise ValueError('ISSUER_IDENTITY_INVALID')


def _record(report):
    _schema(report)
    if report.get('schema_version') != 'iic.annual-report.v1':
        raise ValueError('ANNUAL_REPORT_REQUIRED')
    issuer = report['issuer']
    _identity(issuer)
    period, source = report['reporting_period'], report['source']
    if not _annual(period['start'], period['end']):
        raise ValueError('ANNUAL_PERIOD_REQUIRED')
    if (date.fromisoformat(source['filed_at']) < date.fromisoformat(period['end'])
            or _stamp(report['received_at']).date() < date.fromisoformat(source['filed_at'])):
        raise ValueError('REPORT_DATES_INCONSISTENT')
    raw = _bytes(source)
    try:
        observations, controls = _extract(report, raw)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ValueError('SOURCE_CONTRACT_INVALID') from exc
    key = dict(issuer=issuer, document_id=source['document_id'], sha256=source['declared_sha256'])
    report_id = hashlib.sha256(canonical_json(key).encode()).hexdigest()
    immutable_input = {key: value for key, value in report.items() if key != 'received_at'}
    version_id = hashlib.sha256(canonical_json(immutable_input).encode()).hexdigest()
    return dict(version_id=version_id, report_id=report_id, input=copy.deepcopy(report),
                observations=observations, controls=controls)


def new_history(*, retained_output=None):
    """Archive an optional prior output artifact without claiming it is valid.

    No output is recalculated or promoted. Its date, bytes and input references
    are carried unchanged; authenticity and historic validity are not established.
    """
    result = dict(schema_version='iic.annual-history.v1', visibility='PRIVATE',
                  valuation_usable=False, reports=[], retained_output=copy.deepcopy(retained_output))
    validate_history(result)
    return result


def validate_history(history):
    """Recompute source controls and IDs; stored booleans/statuses are not trusted."""
    _schema(history)
    if history.get('schema_version') != 'iic.annual-history.v1':
        raise ValueError('ANNUAL_HISTORY_REQUIRED')
    seen = {}
    report_ids = set()
    for record in history['reports']:
        expected = _record(record['input'])
        if canonical_json(record) != canonical_json(expected) or record['version_id'] in seen:
            raise ValueError('HISTORY_INTEGRITY_INVALID')
        if record['report_id'] in report_ids:
            raise ValueError('DUPLICATE_REPORT')
        report_ids.add(record['report_id'])
        for previous in record['input']['revises']:
            if previous not in seen or seen[previous] != record['input']['issuer']:
                raise ValueError('REVISION_REFERENCE_INVALID')
        seen[record['version_id']] = record['input']['issuer']
    if history['retained_output'] is not None:
        _identity(history['retained_output']['issuer'])
        _bytes(history['retained_output'])
    return history


def import_report(history, report):
    """Copy-on-write and idempotent: no caller-owned object or disk is mutated."""
    validate_history(history)
    incoming = _record(report)
    for stored in history['reports']:
        if stored['report_id'] == incoming['report_id']:
            old_input, new_input = copy.deepcopy(stored['input']), copy.deepcopy(report)
            # Reimport does not rewrite first receipt or create another version.
            old_input.pop('received_at')
            new_input.pop('received_at')
            if old_input != new_input:
                raise ValueError('REPORT_METADATA_CONFLICT')
            return copy.deepcopy(history)
    result = copy.deepcopy(history)
    result['reports'].append(incoming)
    validate_history(result)
    return result


def issuer_views(history, issuer):
    validate_history(history)
    # Validate an identity independently even for an issuer with no reports.
    _identity(issuer)
    records = [item for item in history['reports'] if item['input']['issuer'] == issuer]
    periods = {}
    unassigned = []
    for record in records:
        report = record['input']
        declared = (report['reporting_period']['start'], report['reporting_period']['end'])
        annual = {declared} | {(o['start'], o['end']) for o in record['observations']
                               if _annual(o['start'], o['end'])}
        for start, end in annual:
            periods.setdefault((start, end), dict(start=start, end=end, version_ids=[],
                                                 observation_refs=[], status='MISSING',
                                                 conflicts=[], selected_version_id=None))
        for index, observation in enumerate(record['observations']):
            keys = [(observation['start'], observation['end'])] if _annual(observation['start'], observation['end']) else []
            ambiguous = False
            if observation['start'] is None:
                keys = [p for p in annual if p[1] == observation['end']]
                # Multiple annual starts with the same end cannot be silently chosen.
                if len(keys) != 1:
                    ambiguous = len(keys) > 1
                    keys = []
            if not keys:
                unassigned.append(dict(version_id=record['version_id'], observation_index=index,
                                       reason='AMBIGUOUS_ANNUAL_PERIOD' if ambiguous else 'NO_ANNUAL_PERIOD'))
            for key in keys:
                entry = periods[key]
                if record['version_id'] not in entry['version_ids']:
                    entry['version_ids'].append(record['version_id'])
                entry['observation_refs'].append(dict(version_id=record['version_id'], observation_index=index))
        # An incomplete report remains accessible even without assignable observations.
        if record['version_id'] not in periods[declared]['version_ids']:
            periods[declared]['version_ids'].append(record['version_id'])
    by_id = {record['version_id']: record for record in records}
    for entry in periods.values():
        groups = {}
        observations = []
        for ref in entry['observation_refs']:
            observation = by_id[ref['version_id']]['observations'][ref['observation_index']]
            observations.append(observation)
            key = canonical_json({k: observation[k] for k in ('concept', 'unit', 'start', 'end', 'dimensions')})
            groups.setdefault(key, []).append((ref, observation))
        for key, members in groups.items():
            amounts = {_amount(o['value']) for _, o in members} - {None}
            if len(amounts) > 1:
                entry['conflicts'].append(dict(observation_refs=[ref for ref, _ in members],
                                                status='CONFLICT', selected_observation=None))
        if observations:
            entry['status'] = ('CONFLICT' if entry['conflicts'] or any(
                o['accounting_validation'] == 'CONFLICT' for o in observations) else
                'INCOMPLETE' if any(_amount(o['value']) is None or o['reasons'] for o in observations)
                else 'UNCHECKED')
        entry.update(valuation_usable=False, origin_authenticity='UNCHECKED')
        entry['version_ids'].sort()
    annual_periods = sorted(periods.values(), key=lambda p: (p['end'], p['start']))
    available = [p for p in annual_periods if p['observation_refs']]
    latest_end = max((p['end'] for p in available), default=None)
    last_received = max((_stamp(r['input']['received_at']) for r in records), default=None)
    latest_received_ids = sorted(r['version_id'] for r in records
                                 if _stamp(r['input']['received_at']) == last_received)
    return dict(visibility='PRIVATE', valuation_usable=False, issuer=copy.deepcopy(issuer),
                annual_periods=annual_periods, unassigned_observation_refs=unassigned,
                latest_received=dict(status='RECEIVED_UNAUTHENTICATED' if records else 'MISSING',
                                     version_ids=latest_received_ids,
                                     received_at=last_received.isoformat() if last_received else None),
                latest_available=dict(status='AVAILABLE_UNAUTHENTICATED' if available else 'MISSING',
                                      periods=[p for p in annual_periods if p['end'] == latest_end]),
                latest_admissible=dict(status='BLOCKED_NO_AUTHENTICATED_CONTROL_PATH', period=None),
                retained_output=(copy.deepcopy(history['retained_output'])
                                 if history['retained_output'] is not None
                                 and history['retained_output']['issuer'] == issuer else None),
                retained_output_validation='NOT_REVALIDATED', last_valid_output=None)


def serialize(history, *, audience='private'):
    if audience != 'private':
        return canonical_json({}, audience=audience)
    validate_history(history)
    return canonical_json(history, audience=audience)
