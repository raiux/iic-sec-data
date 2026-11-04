"""Read flat SEC XBRL instances locally. All document claims are unauthenticated."""
import base64
from collections import Counter, defaultdict
from decimal import Decimal
import re
from xml.dom import Node, minidom
from xml.parsers import expat

from scripts.offline_pipeline import canonical_json
from scripts.sec_document_evidence import validate_package
from scripts.validate_sec_accounting import validate_bundle as validate_c2

INSTANCE = 'http://www.xbrl.org/2003/instance'
LINK = 'http://www.xbrl.org/2003/linkbase'
DIM = 'http://xbrl.org/2006/xbrldi'
ISO = 'http://www.xbrl.org/2003/iso4217'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'
CIK = 'http://www.sec.gov/CIK'
MAX_BYTES = 2 * 1024 * 1024
FAMILIES = {
    'us-gaap': r'https?://fasb\.org/us-gaap/[0-9]{4}(-[0-9]{2}-[0-9]{2})?',
    'ifrs-full': r'https?://xbrl\.ifrs\.org/taxonomy/[0-9]{4}-[0-9]{2}-[0-9]{2}/ifrs-full',
    'dei': r'https?://xbrl\.sec\.gov/dei/[0-9]{4}(-[0-9]{2}-[0-9]{2})?',
}


def children(node):
    return [n for n in node.childNodes if n.nodeType == Node.ELEMENT_NODE]


def text(node):
    return ''.join(n.data for n in node.childNodes if n.nodeType in
                   (Node.TEXT_NODE, Node.CDATA_SECTION_NODE)).strip()


def expanded(node):
    return (node.namespaceURI or '', node.localName)


def qname(node, value):
    bits = value.split(':')
    if len(bits) > 2 or not all(re.fullmatch(r'[\w.-]+', s) for s in bits):
        raise ValueError('QNAME_INVALID')
    attr = 'xmlns:' + bits[0] if len(bits) == 2 else 'xmlns'
    while node.nodeType == Node.ELEMENT_NODE:
        if node.hasAttribute(attr):
            return (node.getAttribute(attr), bits[-1])
        node = node.parentNode
    if len(bits) == 2:
        raise ValueError('QNAME_PREFIX_UNBOUND')
    return ('', value)


def parse(raw):
    count = depth = 0

    def start(name, attrs):
        nonlocal count, depth
        count += 1
        depth += 1
        if count > 20000 or depth > 64 or len(attrs) > 64:
            raise ValueError('XML_COMPLEXITY_LIMIT')

    def end(name):
        nonlocal depth
        depth -= 1

    def reject(*args):
        raise ValueError('DTD_OR_ENTITY_FORBIDDEN')

    guard = expat.ParserCreate()
    guard.StartElementHandler, guard.EndElementHandler = start, end
    guard.StartDoctypeDeclHandler = guard.EntityDeclHandler = guard.ExternalEntityRefHandler = reject
    guard.Parse(raw, True)
    return minidom.parseString(raw)


def direct(node, name):
    return [n for n in children(node) if expanded(n) == (INSTANCE, name)]


def context(node):
    entities = [e for ent in direct(node, 'entity') for e in direct(ent, 'identifier')]
    periods = direct(node, 'period')
    dates = [(expanded(n), text(n)) for p in periods for n in children(p)]
    dims = []
    for local in ('segment', 'scenario'):
        for holder in node.getElementsByTagNameNS(INSTANCE, local):
            for member in children(holder):
                kind = expanded(member)
                axis = qname(member, member.getAttribute('dimension')) if kind[0] == DIM else None
                val = qname(member, text(member)) if kind == (DIM, 'explicitMember') else member.toxml()
                dims.append(dict(container=local, kind=kind, axis=axis, value=val))
    return dict(identifier=text(entities[0]) if len(entities) == 1 else None,
                scheme=entities[0].getAttribute('scheme') if len(entities) == 1 else None,
                period=dates if len(periods) == 1 else [], dimensions=dims)


def unit(node):
    def measures(part):
        nodes = children(part)
        if not nodes or any(expanded(n) != (INSTANCE, 'measure') for n in nodes):
            return []
        return sorted(qname(n, text(n)) for n in nodes)
    div = direct(node, 'divide')
    if not div:
        return dict(numerator=measures(node), denominator=[], divided=False)
    parts = children(div[0])
    if len(children(node)) == 1 and [expanded(p) for p in parts] == [
            (INSTANCE, 'unitNumerator'), (INSTANCE, 'unitDenominator')]:
        return dict(numerator=measures(parts[0]), denominator=measures(parts[1]), divided=True)
    return dict(numerator=[], denominator=[], divided=True)


def facts_from(document):
    root = document.documentElement
    if expanded(root) != (INSTANCE, 'xbrl'):
        raise ValueError('UNSUPPORTED_FORMAT')
    ids = Counter(n.getAttribute('id') for n in document.getElementsByTagName('*') if n.hasAttribute('id'))
    contexts = {n.getAttribute('id'): context(n) for n in direct(root, 'context')}
    units = {n.getAttribute('id'): unit(n) for n in direct(root, 'unit')}
    facts = []
    for n in children(root):
        if n.namespaceURI in (INSTANCE, LINK):
            continue
        if children(n):
            raise ValueError('UNSUPPORTED_COMPLEX_FACT')
        ref, uref = n.getAttribute('contextRef'), n.getAttribute('unitRef')
        ctx = contexts.get(ref) if ref and ids[ref] == 1 else None
        u = units.get(uref) if uref and ids[uref] == 1 else None
        nil = n.getAttributeNS(XSI, 'nil') if n.hasAttributeNS(XSI, 'nil') else None
        decimals = n.getAttribute('decimals') if n.hasAttribute('decimals') else None
        value = text(n)
        errors = []
        if not ctx:
            errors.append('CONTEXT_MISSING_OR_AMBIGUOUS')
        if not u or not u['numerator'] or (u['divided'] and not u['denominator']):
            errors.append('UNIT_MISSING_INVALID_OR_AMBIGUOUS')
        if n.hasAttribute('id') and ids[n.getAttribute('id')] != 1:
            errors.append('FACT_ID_AMBIGUOUS')
        if nil in ('true', '1'):
            errors.append('NIL_FACT')
        elif nil not in (None, 'false', '0') or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', value):
            errors.append('INVALID_NUMERIC_FACT')
        if decimals is not None and not re.fullmatch(r'INF|[+-]?[0-9]+', decimals):
            errors.append('INVALID_DECIMALS')
        facts.append(dict(index=len(facts), id=n.getAttribute('id') or None, qname=expanded(n),
                          context_ref=ref, unit_ref=uref, value=value, decimals=decimals, nil=nil,
                          reasons=errors))
    return contexts, units, facts


def compatible(f, obs, contexts, units):
    if f['reasons']:
        return False
    c, norm = contexts[f['context_ref']], obs['normalized']
    ident = c['identifier'] or ''
    if c['scheme'] != CIK or not re.fullmatch(r'[0-9]{1,10}', ident) or ident.zfill(10) != obs['cik']:
        return False
    if c['dimensions']:
        return False
    period = [((INSTANCE, 'instant'), norm['end'])]
    if norm['start'] is not None:
        period = [((INSTANCE, 'startDate'), norm['start']), ((INSTANCE, 'endDate'), norm['end'])]
    measure = (INSTANCE, 'shares') if norm['unit'] == 'shares' else (ISO, norm['unit'])
    return c['period'] == period and units[f['unit_ref']] == dict(numerator=[measure], denominator=[], divided=False)


def reconcile(bundle, package, *, evidence=()):
    """Return C2 unchanged alongside document extraction and independent diagnostics."""
    result = dict(visibility='PRIVATE', valuation_usable=False, origin_authenticity='UNCHECKED',
                  consolidation='UNCHECKED', parent_attribution='UNCHECKED', c2=validate_c2(bundle, evidence=evidence),
                  document=None, facts=[], contexts={}, units={}, comparisons=[], status='INVALID', reasons=[])
    encoded = package.get('raw_bytes_base64') if isinstance(package, dict) else None
    if isinstance(encoded, str) and len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        result['reasons'] = ['DOCUMENT_SIZE_LIMIT']
        return result
    doc = result['document'] = validate_package(package)
    if doc['status'] != 'CONSISTENT_UNAUTHENTICATED':
        result['reasons'] = doc['reasons']
        return result
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_BYTES:
            raise ValueError('DOCUMENT_SIZE_LIMIT')
        with parse(raw) as document:
            contexts, units, facts = facts_from(document)
    except (expat.ExpatError, LookupError):
        result['reasons'] = ['MALFORMED_XML']
        return result
    except ValueError as exc:
        result['reasons'] = [str(exc)]
        return result
    result.update(contexts=contexts, units=units, facts=facts)
    index = defaultdict(list)
    for f in facts:
        uri, local = f['qname']
        for family, pattern in FAMILIES.items():
            if re.fullmatch(pattern, uri):
                index[family, local].append(f)
    work = 0
    for obs in result['c2']['observations']:
        norm, identity = obs['normalized'], package['identity']
        matches = []
        if not norm or obs['checks']['raw_normalized'] != 'PASS' or obs['checks']['raw_sha256'] != 'PASS':
            status = 'OBSERVATION_INELIGIBLE'
        elif (identity['issuer_cik'], identity['accession']) != (obs['cik'], norm['accession']):
            status = 'PACKAGE_IDENTITY_DIVERGES'
        else:
            pool = index[obs['namespace'], obs['concept']]
            work += len(pool)
            if work > 100000:
                result.update(reasons=['COMPARISON_LIMIT'], comparisons=[])
                return result
            matches = [f for f in pool if compatible(f, obs, contexts, units)]
            if not matches:
                status = 'NO_COMPARABLE_FACT'
            elif any(Decimal(f['value']) != Decimal(norm['value']) for f in matches):
                status = 'VALUE_CONFLICT'
            elif len(matches) > 1:
                status = 'MULTIPLE_MATCHES_UNSELECTED'
            else:
                status = 'MATCH_UNAUTHENTICATED'
        result['comparisons'].append(dict(observation_id=obs['id'], status=status,
                                          fact_indices=[f['index'] for f in matches]))
    result['status'] = 'PARSED_UNAUTHENTICATED'
    return result


def serialize(report, *, audience='private'):
    return canonical_json(report, audience=audience)
