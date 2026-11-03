"""Strict B2 boundary for untrusted JSON. Pure, offline, no identifier coercion."""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib import import_module

KINDS = {'ORDINARY_SHARE', 'ADR', 'ETF', 'ETN', 'FUND', 'OTHER', 'UNKNOWN'}


class InvalidRecord(ValueError):
    pass


def obj(value, required, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise InvalidRecord('OBJECT_FIELDS')
    return value


def text(value, pattern=None):
    if type(value) is not str or not value or value != value.strip():
        raise InvalidRecord('STRING_TYPE_OR_EMPTY')
    if pattern and not re.fullmatch(pattern, value):
        raise InvalidRecord('IDENTIFIER_FORMAT')
    return value


def choice(value, values):
    if type(value) is not str or value not in values:
        raise InvalidRecord('ENUM_VALUE')
    return value


def proof(value, as_of):
    obj(value, ('ref', 'checked_at', 'result'))
    text(value['ref'])
    choice(value['result'], {'VERIFIED', 'UNVERIFIED', 'CONFLICT'})
    stamp = text(value['checked_at'])
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed > as_of:
            raise ValueError
    except ValueError as exc:
        raise InvalidRecord('PROOF_DATE') from exc
    return value['result'] == 'VERIFIED'


@dataclass(frozen=True)
class Prepared:
    status: str
    reasons: tuple[str, ...] = ()
    instrument: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    keys: tuple[str, str, str] | None = None
    provenance: dict = field(default_factory=dict)


def prepare(record, *, as_of):
    """as_of is supplied explicitly so replay is deterministic.

    Trust assertions from the upstream evidence provider only after shape checks.
    No URL, official origin or access permission is independently verified here.
    """
    try:
        return _prepare(record, as_of)
    except (InvalidRecord, InvalidOperation) as exc:
        return Prepared('NEEDS_REVIEW', (str(exc),))


def _prepare(record, as_of):
    obj(record, ('issuer', 'instrument', 'listing'), ('source',))
    i = obj(record['instrument'], ('id', 'issuer_id', 'kind', 'isin', 'class', 'identity'),
            ('underlying', 'ratio', 'rights'))
    kind = choice(i['kind'], KINDS)
    # Absolute guard before reading any source, proof or financial route.
    if kind in {'ETF', 'ETN', 'FUND'}:
        return Prepared('EXCLUDED', (kind,))
    try:
        json.dumps(record, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidRecord('NON_JSON_RECORD') from exc
    e = obj(record['issuer'], ('id', 'legal_name', 'country', 'cik', 'lei', 'identity'))
    l = obj(record['listing'], ('id', 'instrument_id', 'ticker', 'mic', 'currency', 'status', 'identity'))
    for value in (e['id'], e['legal_name'], i['id'], i['issuer_id'], i['class'],
                  l['id'], l['instrument_id'], l['ticker']):
        text(value)
    text(e['country'], r'[A-Z]{2}')
    text(l['currency'], r'[A-Z]{3}')
    for value, pattern in ((e['cik'], r'[0-9]{10}'), (e['lei'], r'[A-Z0-9]{20}'),
                           (i['isin'], r'[A-Z]{2}[A-Z0-9]{9}[0-9]'),
                           (l['mic'], r'[A-Z0-9]{4}')):
        if value != 'UNKNOWN':
            text(value, pattern)
    choice(l['status'], {'ACTIVE', 'DELISTED', 'UNKNOWN'})
    identity_checks = [proof(v['identity'], as_of) for v in (e, i, l)]
    if (not all(identity_checks) or i['issuer_id'] != e['id'] or l['instrument_id'] != i['id']
            or kind == 'UNKNOWN' or i['isin'] == 'UNKNOWN' or l['mic'] == 'UNKNOWN'):
        return Prepared('NEEDS_REVIEW', ('IDENTITY_UNRESOLVED',), provenance=record)
    instrument = dict(instrument_id=i['id'], kind=kind, issuer_id=e['id'],
                      identity_verified=True, identity_ref=i['identity']['ref'],
                      listing_status=l['status'])
    invalid_ratio = False
    if kind == 'ADR':
        underlying = i.get('underlying')
        if underlying is None:
            return Prepared('NEEDS_REVIEW', ('UNDERLYING_UNKNOWN',), provenance=record)
        obj(underlying, ('issuer_id', 'identity'))
        if underlying['issuer_id'] != e['id'] or not proof(underlying['identity'], as_of):
            return Prepared('NEEDS_REVIEW', ('UNDERLYING_CONFLICT',), provenance=record)
        instrument['underlying_ref'] = underlying['identity']['ref']
        if 'ratio' in i:
            ratio = i['ratio']
            # Decimal is explicit and lossless at the JSON boundary. No bare JSON number.
            try:
                obj(ratio, ('decimal', 'identity'))
                text(ratio['decimal'], r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?')
                amount = Decimal(ratio['decimal'])
                if amount <= 0 or not proof(ratio['identity'], as_of):
                    raise InvalidRecord('RATIO_INVALID')
                instrument.update(shares_per_receipt=amount, ratio_ref=ratio['identity']['ref'])
            except (InvalidRecord, InvalidOperation):
                invalid_ratio = True
        if 'rights' in i:
            if proof(i['rights'], as_of):
                instrument['rights_ref'] = i['rights']['ref']
    keys = (e['id'], i['id'], l['id'])
    reasons = ('INVALID_ADR_RATIO',) if invalid_ratio else ()
    if l['status'] == 'DELISTED':
        return Prepared('ARCHIVED', ('DELISTED',), instrument, keys=keys, provenance=record)
    if l['status'] != 'ACTIVE':
        return Prepared('NEEDS_REVIEW', ('LISTING_UNKNOWN',), provenance=record)
    if 'source' not in record:
        return Prepared('UNVERIFIED', ('SOURCE_ABSENT',), instrument, keys=keys, provenance=record)
    try:
        source, source_status = source_evidence(record['source'], e, as_of)
    except InvalidRecord as exc:
        return Prepared('UNVERIFIED', ('SOURCE_INVALID', str(exc)), instrument, keys=keys, provenance=record)
    if source_status:
        return Prepared(source_status, ('SOURCE_' + source_status,), instrument, source, keys, record)
    return Prepared('PREPARED', reasons, instrument, source, keys, record)


def source_evidence(raw, issuer, as_of):
    obj(raw, ('adapter', 'issuer_id', 'reference', 'official_check', 'access', 'filing', 'xbrl'))
    adapter = choice(raw['adapter'], {'SEC_COMPANYFACTS', 'ESEF', 'ISSUER_REPORT', 'SEDAR_PLUS'})
    text(raw['issuer_id'])
    text(raw['reference'])
    official = proof(raw['official_check'], as_of)
    access = obj(raw['access'], ('result', 'authorization', 'identity'))
    choice(access['result'], {'AVAILABLE', 'UNAVAILABLE', 'UNVERIFIED'})
    choice(access['authorization'], {'AUTHORIZED', 'DENIED', 'UNVERIFIED'})
    access_verified = proof(access['identity'], as_of)
    if raw['issuer_id'] != issuer['id']:
        return {}, 'NEEDS_REVIEW'
    if access['authorization'] == 'DENIED' or access['result'] == 'UNAVAILABLE':
        return {}, 'BLOCKED'
    filing = obj(raw['filing'], ('ref', 'identity'))
    xbrl = obj(raw['xbrl'], ('taxonomy', 'identity'))
    text(filing['ref'])
    text(xbrl['taxonomy'])
    filing_verified = proof(filing['identity'], as_of)
    xbrl_verified = proof(xbrl['identity'], as_of)
    if (not all((official, access_verified, filing_verified, xbrl_verified))
            or access['result'] != 'AVAILABLE' or access['authorization'] != 'AUTHORIZED'
            or adapter not in {'SEC_COMPANYFACTS', 'ESEF'}):
        return {}, 'UNVERIFIED'
    return dict(adapter=adapter, issuer_id=issuer['id'], access='AUTHORIZED',
                official_source_ref=raw['reference'], access_check_ref=access['identity']['ref'],
                filing_ref=filing['ref'], xbrl_filing_ref=xbrl['identity']['ref'],
                cik='' if issuer['cik'] == 'UNKNOWN' else issuer['cik'],
                taxonomy=xbrl['taxonomy']), None


def load_json(payload):
    """Reject duplicate keys and non-JSON numeric constants before interpretation."""
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise InvalidRecord('DUPLICATE_JSON_KEY')
            output[key] = value
        return output

    def constant(value):
        raise InvalidRecord('NON_JSON_NUMBER')
    try:
        if type(payload) is not str:
            raise InvalidRecord('JSON_TEXT_REQUIRED')
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant), None
    except (ValueError, RecursionError) as exc:
        return None, Prepared('NEEDS_REVIEW', ('INVALID_JSON', str(exc)))


def reconcile(records, *, as_of):
    """Quarantine every side of a collision, regardless of arrival order."""
    if type(records) is not list:
        return [Prepared('NEEDS_REVIEW', ('RECORD_ARRAY_REQUIRED',))]
    prepared = [prepare(record, as_of=as_of) for record in records]
    signatures = {}
    owners = {}
    for index, item in enumerate(prepared):
        if item.keys is None:
            continue
        raw = item.provenance
        identities = (
            ('issuer', item.keys[0], tuple(raw['issuer'][k] for k in ('legal_name', 'country', 'cik', 'lei'))),
            ('instrument', item.keys[1],
             tuple(raw['instrument'][k] for k in ('issuer_id', 'kind', 'isin', 'class'))
             + (json.dumps({k: raw['instrument'].get(k) for k in ('underlying', 'ratio', 'rights')},
                           sort_keys=True),)),
            ('listing', item.keys[2], tuple(raw['listing'][k] for k in ('instrument_id', 'ticker', 'mic', 'currency', 'status'))),
            ('ticker_mic', (raw['listing']['ticker'], raw['listing']['mic']), item.keys[1:]),
            ('isin', raw['instrument']['isin'], item.keys[:2]),
        )
        identities = list(identities)
        for field in ('cik', 'lei'):
            if raw['issuer'][field] != 'UNKNOWN':
                identities.append((field, raw['issuer'][field], item.keys[0]))
        if item.status in {'PREPARED', 'BLOCKED'} and type(raw.get('source')) is dict:
            identities.append(('source', (item.keys[0], raw['source'].get('adapter')),
                               json.dumps(raw['source'], sort_keys=True)))
        for category, key, signature in identities:
            token = (category, key)
            signatures.setdefault(token, set()).add(signature)
            owners.setdefault(token, set()).add(index)
    conflicts = set()
    for token, values in signatures.items():
        if len(values) > 1:
            conflicts.update(owners[token])
    return [Prepared('NEEDS_REVIEW', ('IDENTITY_COLLISION',), provenance=item.provenance)
            if index in conflicts else item for index, item in enumerate(prepared)]


def route_prepared(item, collected=frozenset()):
    """Use B1 only when installed. No dynamic download or fallback implementation."""
    if item.status != 'PREPARED':
        return dict(status=item.status, reasons=item.reasons, issuer_collection_eligible=False,
                    instrument_valuation_eligible=False, collection_required=False)
    try:
        b1 = import_module('scripts.route_instrument')
    except ModuleNotFoundError as exc:
        if exc.name != 'scripts.route_instrument':
            raise
        return dict(status='DEPENDENCY_BLOCKED', reasons=('B1_NOT_INTEGRATED',),
                    issuer_collection_eligible=False, instrument_valuation_eligible=False,
                    collection_required=False)
    from dataclasses import asdict
    decision = b1.route(b1.Instrument(**item.instrument), b1.SourceEvidence(**item.source), collected)
    return asdict(decision)


def plan(records, *, as_of):
    seen = set()
    output = []
    for item in reconcile(records, as_of=as_of):
        decision = route_prepared(item, frozenset(seen))
        if decision.get('collection_required'):
            seen.add(decision['collection_key'])
        output.append(decision)
    return output
