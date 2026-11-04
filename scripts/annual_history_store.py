"""Private local annual-history persistence; no network or production integration."""
import argparse
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scripts.annual_history import import_report, new_history, serialize, validate_history
from scripts.normalize_sec import parse_raw
from scripts.offline_pipeline import canonical_json

FORMAT = 'iic.annual-history-store.v1'
MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
REPOSITORY = Path(__file__).resolve().parents[1]


class StoreError(ValueError):
    """Controlled rejection; diagnostics never contain private submitted values."""


class RevisionConflict(StoreError):
    pass


class StoreBusy(StoreError):
    pass


class CommitUncertain(StoreError):
    """Reload authoritative target before retrying; never roll back implicitly."""


@dataclass(frozen=True)
class Snapshot:
    revision: str
    generation: int
    history: dict
    directory_sync: str


def _private_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise StoreError('ABSOLUTE_PRIVATE_PATH_REQUIRED')
    # Refuse aliases into any public checkout, including linked worktrees.
    resolved = path.resolve()
    if resolved == REPOSITORY or REPOSITORY in resolved.parents or any(
            (parent / '.git').exists() for parent in resolved.parents):
        raise StoreError('STORE_INSIDE_REPOSITORY_FORBIDDEN')
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise StoreError('SYMLINK_STORAGE_PATH_FORBIDDEN')
    if not path.parent.is_dir():
        raise StoreError('EXISTING_PRIVATE_DIRECTORY_REQUIRED')
    if os.name == 'posix' and stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise StoreError('PRIVATE_DIRECTORY_PERMISSIONS_REQUIRED')
    return path


def _read(path, limit):
    if path.is_symlink():
        raise StoreError('SYMLINK_INPUT_FORBIDDEN')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise StoreError('LOCAL_FILE_SIZE_LIMIT')
    return raw


def _json(raw):
    try:
        return parse_raw(raw.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StoreError('LOCAL_JSON_INVALID') from exc


def _envelope(history, generation):
    serialize(history)  # Reuse full mission-08 validation/private serialization.
    document = dict(storage_format=FORMAT, generation=generation, history=history)
    document['payload_sha256'] = hashlib.sha256(canonical_json(document).encode('utf-8')).hexdigest()
    payload = canonical_json(document).encode('utf-8')
    if len(payload) > MAX_STORE_BYTES:
        raise StoreError('LOCAL_FILE_SIZE_LIMIT')
    _decode(payload)  # Revalidate the exact representation before touching the target.
    return payload


def _decode(raw):
    doc = _json(raw)
    if (type(doc) is not dict or set(doc) != {'storage_format', 'generation', 'history', 'payload_sha256'}
            or doc['storage_format'] != FORMAT or type(doc['generation']) is not int
            or not 1 <= doc['generation'] <= 2**63 - 1 or type(doc['payload_sha256']) is not str):
        raise StoreError('STORAGE_FORMAT_INVALID_OR_UNSUPPORTED')
    try:
        validate_history(doc['history'])
        content = {key: value for key, value in doc.items() if key != 'payload_sha256'}
        digest = hashlib.sha256(canonical_json(content).encode('utf-8')).hexdigest()
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise StoreError('STORED_HISTORY_INVALID') from exc
    if doc['payload_sha256'] != digest:
        raise StoreError('STORED_HISTORY_CHECKSUM_MISMATCH')
    return doc


def _sync_directory(directory):
    if os.name != 'posix':
        return 'UNAVAILABLE_ON_THIS_OS'
    fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return 'SYNCED'


class HistoryStore:
    """Cooperating processes use a stable OS lock; read/CAS/write share exclusion.

    Lock files are never unlinked: replacing the lock inode would split exclusion.
    Missing targets must be initialized explicitly; corruption never means empty.
    """
    def __init__(self, path):
        self.path = _private_path(path)
        self.lock_path = self.path.with_name(self.path.name + '.lock')

    @contextmanager
    def _locked(self):
        _private_path(self.path)
        if self.lock_path.is_symlink():
            raise StoreError('SYMLINK_LOCK_FORBIDDEN')
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            if os.name == 'nt':
                import msvcrt
                # A stable first byte supports Windows byte-range locks.
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise StoreBusy('STORE_BUSY_RETRY_EXPLICITLY') from exc
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise StoreBusy('STORE_BUSY_RETRY_EXPLICITLY') from exc
            locked = True
            yield
        finally:
            if locked:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load(self):
        raw = _read(self.path, MAX_STORE_BYTES)
        document = _decode(raw)
        return Snapshot(hashlib.sha256(raw).hexdigest(), document['generation'],
                        document['history'], 'NOT_ASSESSED_ON_LOAD')

    def load(self):
        with self._locked():
            return self._load()

    def _commit(self, payload):
        temporary = None
        replacement_attempted = False
        try:
            # Same-directory private temporary: same filesystem, no public artifacts.
            with tempfile.NamedTemporaryFile(mode='wb', dir=self.path.parent,
                                             prefix=self.path.name + '.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Read back the exact bytes before replacing the authoritative registry.
            if _read(temporary, MAX_STORE_BYTES) != payload:
                raise StoreError('TEMPORARY_WRITE_MISMATCH')
            _decode(_read(temporary, MAX_STORE_BYTES))
            replacement_attempted = True
            os.replace(temporary, self.path)
            directory_sync = _sync_directory(self.path.parent)
            doc = _decode(payload)
            return Snapshot(hashlib.sha256(payload).hexdigest(), doc['generation'], doc['history'], directory_sync)
        except OSError as exc:
            if replacement_attempted:
                raise CommitUncertain('COMMIT_OUTCOME_RELOAD_REQUIRED') from exc
            raise
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError as exc:
                    raise CommitUncertain('TEMPORARY_CLEANUP_FAILED_INSPECT_PRIVATE_DIRECTORY') from exc

    def initialize(self, history=None):
        # Explicit creation only; never replace an existing, even corrupt, registry.
        payload = _envelope(new_history() if history is None else history, 1)
        with self._locked():
            if os.path.lexists(self.path):
                raise StoreError('STORE_ALREADY_EXISTS')
            return self._commit(payload)

    def import_report(self, report, *, expected_revision=None):
        with self._locked():
            current = self._load()
            if expected_revision is not None and expected_revision != current.revision:
                raise RevisionConflict('STALE_REVISION_RELOAD_REQUIRED')
            updated = import_report(current.history, report)
            if serialize(updated) == serialize(current.history):
                return current  # No-op: preserve bytes, generation and first receipt.
            return self._commit(_envelope(updated, current.generation + 1))

    def import_local(self, report_path, *, expected_revision=None):
        # Local explicit file, never a URL or implicit source discovery.
        report_path = Path(report_path)
        if not report_path.is_absolute():
            raise StoreError('ABSOLUTE_REPORT_PATH_REQUIRED')
        report = _json(_read(report_path, MAX_REPORT_BYTES))
        return self.import_report(report, expected_revision=expected_revision)

    def cleanup_orphans(self):
        """Explicit operator action; never adopt a temporary as recovery state."""
        with self._locked():
            self._load()  # Corrupt authoritative state blocks automatic housekeeping.
            removed = 0
            for path in self.path.parent.iterdir():
                if path.name.startswith(self.path.name + '.') and path.name.endswith('.tmp'):
                    if path.is_symlink() or not path.is_file():
                        raise StoreError('UNEXPECTED_TEMPORARY_ENTRY')
                    path.unlink()
                    removed += 1
            return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'status', 'import', 'cleanup'])
    parser.add_argument('--store', required=True, help='Absolute path outside any Git checkout')
    parser.add_argument('--report', help='Absolute local annual-report.v1 JSON file')
    parser.add_argument('--expected-revision')
    args = parser.parse_args()
    try:
        store = HistoryStore(args.store)
        if args.action == 'init':
            snapshot = store.initialize()
        elif args.action == 'import':
            if not args.report:
                parser.error('--report required for import')
            snapshot = store.import_local(args.report, expected_revision=args.expected_revision)
        elif args.action == 'cleanup':
            print('Removed temporary files:', store.cleanup_orphans())
            return 0
        else:
            snapshot = store.load()
        # No source bytes, issuer IDs, paths, values or report metadata on stdout.
        print('PRIVATE valuation_usable=false generation=', snapshot.generation,
              'reports=', len(snapshot.history['reports']), 'revision=', snapshot.revision)
        return 0
    except (StoreError, OSError, ValueError, TypeError):
        print('PRIVATE_STORE_OPERATION_FAILED: inspect private state; no implicit recovery')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
