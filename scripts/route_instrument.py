"""B1: decide from supplied, reconciled evidence. No discovery, I/O or valuation."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    kind: str
    issuer_id: str | None  # Underlying issuer for an ADR, never the depositary.
    identity_verified: bool = False
    identity_ref: str = ''
    conflicts: tuple[str, ...] = ()  # e.g. ISIN_CONFLICT, MIC_CONFLICT, AMBIGUOUS_TICKER
    listing_status: str = 'UNKNOWN'
    underlying_ref: str = ''
    shares_per_receipt: Decimal | None = None  # Exact positive finite Decimal; no coercion or rounding.
    ratio_ref: str = ''
    rights_ref: str = ''  # References to verified, effective evidence for this receipt.


@dataclass(frozen=True)
class SourceEvidence:
    """The caller supplies AUTHORIZED and refs from its own dated checks.

    The router evaluates those assertions without verifying rights, opening refs
    or probing a source. Fixture decisions do not establish real availability.
    """
    adapter: str
    issuer_id: str
    access: str = 'UNKNOWN'  # AUTHORIZED, DENIED, UNKNOWN
    official_source_ref: str = ''
    access_check_ref: str = ''  # Successful probe of the proposed resource for this issuer.
    filing_ref: str = ''  # XBRL filing for SEC; official package for ESEF.
    cik: str = ''
    taxonomy: str = ''
    xbrl_filing_ref: str = ''  # Evidence that this filing contains eligible tagged facts.


@dataclass(frozen=True)
class Decision:
    instrument_id: str
    issuer_id: str | None
    status: str
    reasons: tuple[str, ...]
    proposed_adapter: str | None = None
    adapter: str | None = None
    issuer_collection_eligible: bool = False
    instrument_valuation_eligible: bool = False  # Instrument gate only.
    collection_required: bool = False
    collection_key: tuple[str, str] | None = None
    evidence_refs: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()


def route(instrument: Instrument, source: SourceEvidence | None = None,
          collected: frozenset[tuple[str, str]] = frozenset()) -> Decision:
    """collected is the caller's set of issuer/source keys for this freshness window."""
    def stop(status, reasons, *, proposal=None, missing=(), refs=()):
        return Decision(instrument.instrument_id, instrument.issuer_id, status, reasons,
                        proposed_adapter=proposal, missing_fields=missing, evidence_refs=refs)

    if instrument.kind in {'ETF', 'ETN', 'FUND'}:
        return stop('EXCLUDED', (instrument.kind,))
    if instrument.kind == 'OTHER':
        return stop('EXCLUDED', ('OUT_OF_SCOPE',))
    if instrument.kind not in {'ORDINARY_SHARE', 'ADR'}:
        return stop('NEEDS_REVIEW', ('UNKNOWN_TYPE',))
    adr = instrument.kind == 'ADR'
    identity = ('instrument_id', 'issuer_id', 'identity_ref') + (('underlying_ref',) if adr else ())
    missing = tuple(field for field in identity if not getattr(instrument, field))
    if missing or instrument.conflicts or not instrument.identity_verified:
        return stop('NEEDS_REVIEW', instrument.conflicts or ('IDENTITY_UNVERIFIED',), missing=missing)
    if instrument.listing_status == 'DELISTED':
        return stop('ARCHIVED', ('DELISTED',))
    if instrument.listing_status != 'ACTIVE':
        return stop('NEEDS_REVIEW', ('LISTING_UNVERIFIED',))
    if source is None:
        return stop('UNVERIFIED', ('NO_SOURCE_EVIDENCE',))
    proposal = source.adapter
    refs = tuple(filter(None, (instrument.identity_ref, instrument.underlying_ref,
                               source.official_source_ref, source.filing_ref, source.xbrl_filing_ref, source.access_check_ref)))
    if source.access == 'DENIED':
        return stop('BLOCKED', ('ACCESS_DENIED',), proposal=proposal, refs=refs)
    if source.issuer_id != instrument.issuer_id:
        return stop('NEEDS_REVIEW', ('SOURCE_ISSUER_CONFLICT',), proposal=proposal, refs=refs)
    if proposal not in {'SEC_COMPANYFACTS', 'ESEF'}:
        return stop('UNVERIFIED', ('UNSUPPORTED_ROUTE',), proposal=proposal, refs=refs)
    required = ('official_source_ref', 'filing_ref', 'access_check_ref', 'taxonomy')
    if proposal == 'SEC_COMPANYFACTS':
        required += ('cik', 'xbrl_filing_ref')
    missing = tuple(field for field in required if not getattr(source, field))
    if missing or source.access != 'AUTHORIZED':
        return stop('UNVERIFIED', ('SOURCE_NOT_VERIFIED',), proposal=proposal, missing=missing, refs=refs)
    if proposal == 'SEC_COMPANYFACTS':
        if not (len(source.cik) == 10 and source.cik.isascii() and source.cik.isdigit()
                and int(source.cik) > 0 and source.taxonomy in {'us-gaap', 'ifrs-full'}):
            return stop('UNVERIFIED', ('SEC_EVIDENCE_INELIGIBLE',), proposal=proposal, refs=refs)
    elif source.taxonomy != 'ifrs-full':
        return stop('UNVERIFIED', ('ESEF_TAXONOMY_UNVERIFIED',), proposal=proposal, refs=refs)

    key = (instrument.issuer_id, proposal)
    missing = ()
    if adr:
        ratio = instrument.shares_per_receipt
        if not isinstance(ratio, Decimal) or not ratio.is_finite() or ratio <= 0 or not instrument.ratio_ref:
            missing += ('verified_shares_per_receipt',)
        if not instrument.rights_ref:
            missing += ('verified_receipt_rights',)
        refs += tuple(filter(None, (instrument.ratio_ref, instrument.rights_ref)))
    return Decision(instrument.instrument_id, instrument.issuer_id, 'ROUTABLE',
                    ('RECEIPT_TERMS_INCOMPLETE',) if missing else (), proposal, proposal,
                    True, not missing, key not in collected, key, refs, missing)
