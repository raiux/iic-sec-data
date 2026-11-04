"""Offline, private adapter from eligible B3 issuer intents to C1 SEC candidates."""
from datetime import datetime, timezone

from scripts.ingest_identity import load_json
from scripts.normalize_sec import normalize_snapshot
from scripts.offline_pipeline import build_plan, validator


def _records(payload):
    records, error = load_json(payload)
    # build_plan is the authority for rejection diagnostics. Do not turn its
    # deterministic REJECTED envelope into a second, less informative failure.
    return records if error is None and type(records) is list else []


def _decision_groups(plan):
    grouped = {}
    for decision in plan['decisions']:
        issuer_id = decision['identity']['issuer_id']
        if issuer_id is not None:
            grouped.setdefault(issuer_id, []).append(decision)
    return grouped


def build_bundle(identity_payload, sec_snapshot_payload, *, as_of, source_locator):
    """Join B3's simulated SEC issuer intents to C1 output by verified CIK.

    The caller-supplied B3 source assertions remain assertions only. This function
    performs no network or accounting validation and never makes a valuation usable.
    Raw SEC payload text is passed unchanged to C1.
    """
    if not isinstance(as_of, datetime):
        raise ValueError('Explicit aware as_of datetime required')
    records = _records(identity_payload)
    plan = build_plan(identity_payload, as_of=as_of)
    grouped = _decision_groups(plan)

    issuer_by_id = {}
    for record in records:
        if type(record) is dict and type(record.get('issuer')) is dict:
            issuer_id = record['issuer'].get('id')
            if type(issuer_id) is str:
                issuer_by_id.setdefault(issuer_id, []).append(record['issuer'].get('cik'))

    intent_by_issuer = {}
    unsupported = []
    for intent in plan['collection_intents']:
        issuer_id = intent['issuer_id']
        adapter = intent['adapter']
        if adapter != 'SEC_COMPANYFACTS':
            unsupported.append(dict(issuer_id=issuer_id, adapter=adapter,
                                   status='SOURCE_ADAPTER_NOT_SUPPORTED_BY_C1'))
            continue
        decisions = grouped.get(issuer_id, [])
        eligible = any(d['issuer_collection_eligible'] and d['adapter'] == adapter
                       and d['status'] == 'ROUTABLE' for d in decisions)
        if not eligible:
            unsupported.append(dict(issuer_id=issuer_id, adapter=adapter,
                                   status='ISSUER_GATE_NOT_ELIGIBLE'))
            continue
        cik_values = {value for value in issuer_by_id.get(issuer_id, [])
                      if type(value) is str and value != 'UNKNOWN'}
        if len(cik_values) != 1:
            unsupported.append(dict(issuer_id=issuer_id, adapter=adapter,
                                   status='CIK_UNRESOLVED_OR_CONFLICTING'))
            continue
        intent_by_issuer[issuer_id] = dict(intent=intent, cik=next(iter(cik_values)))

    # Preserve routes B3 withheld from collection. Proposals/statuses are never
    # redirected to another source.
    for decision in plan['decisions']:
        proposal = decision.get('proposed_adapter')
        issuer_id = decision['identity']['issuer_id']
        if (proposal is not None and issuer_id is not None
                and (decision['status'] != 'ROUTABLE'
                     or not decision['issuer_collection_eligible'])
                and not any(item['issuer_id'] == issuer_id and item['adapter'] == proposal
                            for item in unsupported)):
            unsupported.append(dict(issuer_id=issuer_id, adapter=proposal,
                                    status='B3_GATE_' + decision['status']))

    normalization = None
    companies = {}
    if intent_by_issuer:
        normalization = normalize_snapshot(
            sec_snapshot_payload, as_of=as_of.astimezone(timezone.utc).date(),
            source_locator=source_locator)
        # A cache CIK is unique in C1 input. Keep the source ordering deterministic.
        companies = {company['cik']: company for company in normalization['coverage']}
        observations = normalization['observations']
        groups = normalization['groups']
    else:
        observations, groups = [], []

    issuer_results = []
    for issuer_id in sorted(intent_by_issuer):
        joined = intent_by_issuer[issuer_id]
        cik = joined['cik']
        c1_coverage = companies.get(cik)
        issuer_observations = [item for item in observations if item['cik'] == cik]
        issuer_groups = [item for item in groups if item['cik'] == cik]
        blockers = {'ACCOUNTING_VALIDATION_UNCHECKED'}
        if c1_coverage is None:
            normalization_status = 'CIK_NOT_PRESENT_IN_SNAPSHOT'
            blockers.add('NO_C1_OBSERVATIONS_FOR_CIK')
        else:
            normalization_status = 'C1_NORMALIZED'
            if any(group['status'] == 'CONFLICT' for group in issuer_groups):
                blockers.add('CONFLICT_UNARBITRATED')
            if any(group['status'] == 'REPEATED_VALUE_CANDIDATES' for group in issuer_groups):
                blockers.add('REPEATED_CANDIDATES_UNSELECTED')
            if any(item['structure'] != 'CANDIDATE' for item in issuer_observations):
                blockers.add('OBSERVATIONS_REJECTED_OR_UNMAPPED')

        instrument_decisions = []
        for decision in grouped.get(issuer_id, []):
            instrument_eligible = bool(decision['instrument_valuation_eligible'])
            reasons = set()
            if not instrument_eligible:
                reasons.add('B3_INSTRUMENT_GATE_CLOSED')
            if decision['quality']['structure'] != 'VALID':
                reasons.add('B3_STRUCTURE_INVALID')
            instrument_decisions.append(dict(
                input_index=decision['input_index'], identity=decision['identity'],
                status=decision['status'], reasons=decision['reasons'],
                issuer_collection_eligible=decision['issuer_collection_eligible'],
                instrument_valuation_eligible=instrument_eligible,
                quality=decision['quality'], missing_fields=decision['missing_fields'],
                provenance=decision['provenance'], valuation_usable=False,
                valuation_blockers=sorted(reasons | blockers)))

        issuer_results.append(dict(
            issuer_id=issuer_id, cik=cik, adapter=joined['intent']['adapter'],
            collection_intent=joined['intent'], issuer_collection_eligible=True,
            normalization_status=normalization_status,
            source_sha256=normalization['source']['sha256'] if normalization else None,
            raw_document_pointer='/raw_document' if normalization else None,
            coverage=c1_coverage,
            observations=issuer_observations, groups=issuer_groups,
            accounting_validation='UNCHECKED', valuation_usable=False,
            valuation_blockers=sorted(blockers), instruments=instrument_decisions))

    result = dict(
        schema_version='iic.b3-c1-integration.v1', visibility='PRIVATE',
        mode='OFFLINE_SIMULATION', source_verification='NOT_PERFORMED',
        valuation_usable=False, b3_plan=plan, normalization=normalization,
        issuers=issuer_results, unsupported_intents=sorted(
            unsupported, key=lambda item: (item['issuer_id'], item['adapter'], item['status'])))
    validator('b3-c1-integration.schema.json').validate(result)
    return result
