"""B3: private planning envelope. Public export requires a separate reviewed projection."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from scripts.ingest_identity import load_json, reconcile, route_prepared

BASE = Path(__file__).resolve().parents[1]

class PublicExportBlocked(ValueError):
    """No public projection or verified public-reference allowlist exists in B3."""


def validator(name):
    schema = json.loads((BASE / 'schemas' / name).read_text(encoding='utf-8'))
    # Schemas are versioned locally. Remote references are forbidden in this pipeline.
    def local_only(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {'$ref', '$dynamicRef'}:
                    if not isinstance(value, str):
                        raise ValueError('Schema reference must be a string')
                    if not value.startswith('#'):
                        raise ValueError('Remote schema references are forbidden')
                local_only(value)
        elif isinstance(node, list):
            for value in node:
                local_only(value)
    local_only(schema)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker(['date-time']))


def errors(checker, value):
    """Diagnostics carry paths/rule names, never submitted values or free-text messages."""
    result = []
    for error in checker.iter_errors(value):
        path = '/'.join(map(str, error.absolute_path))
        if error.validator == 'required' and isinstance(error.instance, dict):
            for field in error.validator_value:
                if field not in error.instance:
                    result.append(dict(path='/'.join(filter(None, (path, field))), rule='required'))
        else:
            result.append(dict(path=path, rule=error.validator))
    return sorted(result, key=lambda error: (error['path'], error['rule']))


def unresolved_fields(record):
    """Names of missing or unresolved assertions, without guessing their values."""
    if not isinstance(record, dict):
        return set()
    missing = set()
    for section in ('issuer', 'instrument', 'listing'):
        value = record.get(section)
        if not isinstance(value, dict):
            continue
        for field, entry in value.items():
            if entry == 'UNKNOWN':
                missing.add(section + '/' + field)
        proof = value.get('identity')
        if isinstance(proof, dict) and proof.get('result') != 'VERIFIED':
            missing.add(section + '/identity')
    instrument = record.get('instrument', {})
    if isinstance(instrument, dict) and instrument.get('kind') == 'ADR' and not instrument.get('underlying'):
        missing.add('instrument/underlying')
    source = record.get('source')
    if not isinstance(source, dict):
        missing.add('source')
    else:
        for name in ('official_check', 'access', 'filing', 'xbrl'):
            value = source.get(name)
            if name != 'official_check' and isinstance(value, dict):
                value = value.get('identity')
            if not isinstance(value, dict) or value.get('result') != 'VERIFIED':
                missing.add('source/' + name)
        access = source.get('access')
        if isinstance(access, dict):
            if access.get('result') != 'AVAILABLE':
                missing.add('source/access/result')
            if access.get('authorization') != 'AUTHORIZED':
                missing.add('source/access/authorization')
    return missing



def provenance(record):
    """Preserve recognized assertions for private audit; contents may be sensitive."""
    if not isinstance(record, dict):
        return []
    items = []
    def assertion(role, value):
        if (type(value) is dict and set(value) == {'ref', 'checked_at', 'result'}
                and all(type(v) is str for v in value.values())):
            items.append(dict(role=role, **value))

    for name in ('issuer', 'instrument', 'listing'):
        section = record.get(name)
        if isinstance(section, dict):
            assertion(name + '.identity', section.get('identity'))
    instrument = record.get('instrument', {})
    if isinstance(instrument, dict):
        for name in ('underlying', 'ratio'):
            section = instrument.get(name)
            if isinstance(section, dict):
                assertion('instrument.' + name, section.get('identity'))
        assertion('instrument.rights', instrument.get('rights'))
    source = record.get('source')
    if isinstance(source, dict):
        for name in ('reference',):
            if type(source.get(name)) is str:
                items.append(dict(role='source.' + name, ref=source[name],
                                  checked_at=None, result=None))
        assertion('source.official_check', source.get('official_check'))
        for name in ('access', 'filing', 'xbrl'):
            section = source.get(name)
            if isinstance(section, dict):
                assertion('source.' + name, section.get('identity'))
                # Distinguish the filing locator, taxonomy, access result and authorization
                # from the proof reference. These remain caller assertions.
                for field in {'access': ('result', 'authorization'),
                              'filing': ('ref',), 'xbrl': ('taxonomy',)}[name]:
                    if type(section.get(field)) is str:
                        items.append(dict(role='source.' + name + '.' + field,
                                          ref=section[field], checked_at=None, result=None))
    return items


def identity(record):
    result = {}
    for name in ('issuer', 'instrument', 'listing'):
        value = record.get(name) if type(record) is dict else None
        key = value.get('id') if type(value) is dict else None
        result[name + '_id'] = key if type(key) is str else None
    return result


def build_plan(payload, *, as_of):
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as_of must be an aware datetime')
    input_validator = validator('identity-input.schema.json')
    output_validator = validator('collection-plan.schema.json')
    records, parse_error = load_json(payload)
    envelope = dict(schema_version='iic.collection-plan.v1', mode='OFFLINE_SIMULATION',
                    visibility='PRIVATE', export_policy='SEPARATE_PUBLIC_PROJECTION_REQUIRED',
                    as_of=as_of.astimezone(timezone.utc).isoformat(),
                    status='PLANNED', decisions=[], collection_intents=[],
                    issues=[], source_verification='NOT_PERFORMED')
    if parse_error or type(records) is not list:
        envelope.update(status='REJECTED', issues=['INVALID_JSON' if parse_error else 'RECORD_ARRAY_REQUIRED'])
        output_validator.validate(envelope)
        return envelope

    input_errors = [errors(input_validator.evolve(schema=input_validator.schema['items']), r)
                    for r in records]
    prepared = reconcile(records, as_of=as_of)
    seen = set()
    intents = {}
    for index, (record, item, schema_errors) in enumerate(zip(records, prepared, input_errors)):
        # Known ETF/ETN/FUND remain excluded even if the source part is malformed.
        ratio_only = bool(schema_errors) and all(
            error['path'] == 'instrument/ratio' or error['path'].startswith('instrument/ratio/')
            for error in schema_errors)
        if schema_errors and item.status != 'EXCLUDED' and not ratio_only:
            routed = dict(status='NEEDS_REVIEW', reasons=['INPUT_SCHEMA_INVALID'])
        else:
            routed = route_prepared(item, frozenset(seen))
        status = routed['status']
        chosen = routed.get('adapter')
        required = bool(routed.get('collection_required', False))
        key = routed.get('collection_key')
        proposal = routed.get('proposed_adapter')
        if proposal is None and isinstance(record, dict) and isinstance(record.get('source'), dict):
            candidate = record['source'].get('adapter')
            if candidate in ('SEC_COMPANYFACTS', 'ESEF', 'ISSUER_REPORT', 'SEDAR_PLUS'):
                proposal = candidate
        # For an excluded record no source proposal is relevant.
        if status == 'EXCLUDED':
            proposal = None
        decision = dict(input_index=index, identity=identity(record), status=status,
                        reasons=sorted(set(routed.get('reasons', ())) | set(item.reasons)),
                        proposed_adapter=proposal, adapter=chosen,
                        issuer_collection_eligible=bool(routed.get('issuer_collection_eligible', False)),
                        instrument_valuation_eligible=bool(routed.get('instrument_valuation_eligible', False)),
                        collection_required=required,
                        collection_key=list(key) if key else None,
                        missing_fields=sorted(set(routed.get('missing_fields', ())) |
                                              {e['path'] or '$record' for e in schema_errors} |
                                              (unresolved_fields(record) if status != 'EXCLUDED' else set())),
                        quality=dict(structure='INVALID' if schema_errors else 'VALID',
                                     accounting='NOT_PERFORMED', source='CALLER_ASSERTIONS_ONLY'),
                        schema_errors=schema_errors,
                        provenance=provenance(record))
        envelope['decisions'].append(decision)
        if required:
            seen.add(key)
            intents[key] = dict(issuer_id=key[0], adapter=key[1],
                                state='SIMULATED', input_indices=[])
        if key in intents:
            intents[key]['input_indices'].append(index)
    envelope['collection_intents'] = [intents[key] for key in sorted(intents)]
    output_validator.validate(envelope)
    return envelope


def canonical_json(document, *, audience='private'):
    """Private serialization only; no heuristic can certify arbitrary reference text."""
    if audience != 'private':
        raise PublicExportBlocked('Public export requires a separate reviewed projection')
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--as-of', required=True, help='Aware ISO datetime, fixed for replay')
    parser.add_argument('--audience', choices=('private', 'public'), default='private',
                        help='Private by default; public export is currently blocked')
    args = parser.parse_args()
    try:
        as_of = datetime.fromisoformat(args.as_of.replace('Z', '+00:00'))
        # Refuse publication before reading potentially private input.
        if args.audience == 'public':
            raise PublicExportBlocked('Public export requires a separate reviewed projection')
        result = build_plan(Path(args.input).read_text(encoding='utf-8'), as_of=as_of)
        serialized = canonical_json(result, audience=args.audience)
    except PublicExportBlocked:
        parser.error('Public export blocked: a separate reviewed projection is required')
    except (ValueError, OSError):
        parser.error('Unreadable input, invalid as-of or invalid local schema')
    print(serialized, end='')


if __name__ == '__main__':
    main()
