"""C3 offline document integrity; every origin and identity remains untrusted."""
import argparse
import base64
import binascii
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import ValidationError
from scripts.offline_pipeline import canonical_json, validator
from scripts.validate_sec_accounting import validate_bundle as validate_c2


_NO_EXPECTED = object()


def validate_package(package, *, expected=_NO_EXPECTED):
    """Compare bytes and optional C2 manifest assertions, never authenticate them.

    expected is a C2 filing-assertion record, not an authenticated identity.
    Neither the document's contents nor XBRL facts are interpreted here.
    """
    result = dict(visibility='PRIVATE', valuation_usable=False,
                  origin_authenticity='UNCHECKED', accounting_validation='UNCHECKED',
                  status='INVALID', byte_integrity='UNCHECKED',
                  identity_comparison='NOT_COMPARED', manifest_sha256='NOT_COMPARED',
                  calculated_sha256=None, package=None, reasons=[])
    try:
        validator('c3-document-package.schema.json').validate(package)
    except ValidationError:
        result['reasons'] = ['PACKAGE_SCHEMA_INVALID']
        return result
    result['package'] = copy.deepcopy(package)
    encoded = package['raw_bytes_base64']
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or base64.b64encode(raw).decode('ascii') != encoded:
            raise ValueError('Noncanonical or empty bytes')
    except (ValueError, binascii.Error):
        result['reasons'] = ['DOCUMENT_BYTES_INVALID']
        return result
    digest = hashlib.sha256(raw).hexdigest()
    result['calculated_sha256'] = digest
    result['byte_integrity'] = 'MATCH' if digest == package['declared_sha256'] else 'MISMATCH'
    if result['byte_integrity'] == 'MISMATCH':
        result['reasons'].append('DECLARED_SHA256_MISMATCH')
    if expected is not _NO_EXPECTED:
        # Reuse C2's strict assertion validation without exporting a weaker contract.
        from scripts.validate_sec_accounting import _evidence_errors
        if _evidence_errors(expected):
            result['reasons'].append('C2_ASSERTION_INVALID')
            result['identity_comparison'] = 'NOT_COMPARED'
            result['status'] = 'INVALID'
            return result
        identity = package['identity']
        matches = (identity['issuer_cik'] == expected['issuer_cik']
                   and identity['accession'] == expected['accession']
                   and identity['document_name'] == urlsplit(expected['document_url']).path.rsplit('/', 1)[-1]
                   and package['declared_origin']['locator'] == expected['document_url'])
        result['identity_comparison'] = 'DECLARATIONS_MATCH' if matches else 'CONFLICT'
        result['manifest_sha256'] = 'MATCH' if digest == expected['document_sha256'] else 'MISMATCH'
        if not matches:
            result['reasons'].append('DOCUMENT_IDENTITY_DECLARATIONS_DIVERGE')
        if result['manifest_sha256'] == 'MISMATCH':
            result['reasons'].append('C2_DECLARED_SHA256_MISMATCH')
    result['status'] = 'CONFLICT' if result['reasons'] else 'CONSISTENT_UNAUTHENTICATED'
    return result


def validate_bundle(bundle, *, evidence=(), documents=()):
    """Keep C2 unchanged; bind each package explicitly to a C2 assertion index.

    documents is an array of {evidence_index, package}. No implicit document
    selection, assertion generation or promotion of C2 states is performed.
    """
    c2 = validate_c2(bundle, evidence=evidence)
    if type(documents) not in (list, tuple):
        raise ValueError('DOCUMENTS_ARRAY_REQUIRED')
    checks = []
    for index, attachment in enumerate(documents):
        if (type(attachment) is not dict or set(attachment) != {'evidence_index', 'package'}
                or type(attachment['evidence_index']) is not int
                or not 0 <= attachment['evidence_index'] < len(evidence)):
            checks.append(dict(input_index=index, evidence_index=None, status='INVALID',
                               reasons=['DOCUMENT_BINDING_INVALID']))
            continue
        check = validate_package(attachment['package'], expected=evidence[attachment['evidence_index']])
        checks.append(dict(input_index=index, evidence_index=attachment['evidence_index'], **check))
    return dict(schema_version='iic.c3-evidence-foundation.v1', visibility='PRIVATE',
                valuation_usable=False, origin_authenticity='UNCHECKED',
                c2=c2, document_checks=checks,
                limitations=['ORIGIN_AND_ACQUISITION_ARE_CALLER_DECLARATIONS',
                             'DOCUMENT_CONTENT_IDENTITY_AND_FACTS_NOT_EXTRACTED',
                             'NO_AUTHENTICATION_OR_ACCOUNTING_PROMOTION'])


def serialize(report, *, audience='private'):
    return canonical_json(report, audience=audience)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Private C3 document package JSON')
    args = parser.parse_args()
    try:
        package = json.loads(Path(args.input).read_text(encoding='utf-8'))
        report = validate_package(package)
    except (OSError, ValueError):
        parser.error('Unreadable private document package')
    print(serialize(report), end='')


if __name__ == '__main__':
    main()
