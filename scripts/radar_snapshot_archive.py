"""Lossless, append-only compressed JSONL archive behind the legacy history path.

No network or account access. Readers accept a plain legacy file or an adjacent
<stem>/manifest.json archive. Every compressed chunk and the complete logical
byte stream are verified BEFORE any archived bytes are exposed to a consumer.
Writers publish immutable chunks first, then atomically replace the manifest.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

DEFAULT_PATH = Path('calibration/radar-snapshots.jsonl')
CHUNK_BYTES = 8 * 1024 * 1024
MAX_COMPRESSED_BYTES = CHUNK_BYTES + 64 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
STAGED_LIMIT = 95 * 1024 * 1024
CHUNK_NAME = re.compile(r'^chunks/[0-9a-f]{64}\.jsonl\.gz$')
HEX = re.compile(r'^[0-9a-f]{64}$')


class ArchiveError(RuntimeError):
    """Missing/corrupt/ambiguous history is never replaced with an empty set."""


def archive_dir(path=DEFAULT_PATH):
    return Path(path).with_suffix('')


def manifest_path(path=DEFAULT_PATH):
    return archive_dir(path) / 'manifest.json'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _reject_constant(value):
    raise ArchiveError('Non-finite JSON value: ' + value)


def _json(data):
    try:
        return json.loads(data, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArchiveError('Invalid JSON/UTF-8 in archive') from exc


def _integer(value, upper=None):
    return (isinstance(value, int) and not isinstance(value, bool) and value >= 0
            and (upper is None or value <= upper))


def _regular(path):
    if path.is_symlink() or not path.is_file():
        raise ArchiveError(f'Expected a regular archive file: {path}')


def _record_count(data):
    # Do not normalize whitespace, key order, CRLF, duplicates or timestamps.
    count = 0
    try:
        data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ArchiveError('Invalid UTF-8 history') from exc
    for line in data.splitlines():
        if not line.strip():
            continue
        if not isinstance(_json(line), dict):
            raise ArchiveError('Every nonblank snapshot line must be an object')
        count += 1
    return count


def _manifest(path):
    directory, mp = archive_dir(path), manifest_path(path)
    if directory.is_symlink():
        raise ArchiveError('Archive directory may not be a symlink')
    if not mp.exists():
        if mp.is_symlink():
            raise ArchiveError('Broken manifest symlink')
        return None, None
    _regular(mp)
    if mp.stat().st_size > MAX_MANIFEST_BYTES:
        raise ArchiveError('Manifest size bound exceeded')
    raw = mp.read_bytes()
    m = _json(raw)
    if (not isinstance(m, dict) or type(m.get('schemaVersion')) is not int or m.get('schemaVersion') != 1
            or m.get('role') != 'LOSSLESS_RADAR_SNAPSHOT_ARCHIVE'
            or m.get('compression') != 'gzip' or m.get('encoding') != 'utf-8'
            or not isinstance(m.get('chunks'), list)
            or not _integer(m.get('logicalBytes')) or not _integer(m.get('records'))
            or not HEX.fullmatch(str(m.get('logicalSha256', '')))
            or not isinstance(m.get('endsWithNewline'), bool)):
        raise ArchiveError('Invalid snapshot manifest schema')
    for c in m['chunks']:
        if (not isinstance(c, dict) or not CHUNK_NAME.fullmatch(str(c.get('file', '')))
                or not _integer(c.get('rawBytes'), CHUNK_BYTES) or c['rawBytes'] == 0
                or not _integer(c.get('compressedBytes'), MAX_COMPRESSED_BYTES)
                or not _integer(c.get('records'))
                or not HEX.fullmatch(str(c.get('rawSha256', '')))
                or not HEX.fullmatch(str(c.get('compressedSha256', '')))
                or c['file'] != 'chunks/' + c['rawSha256'] + '.jsonl.gz'):
            raise ArchiveError('Invalid chunk descriptor')
    if (sum(c['rawBytes'] for c in m['chunks']) != m['logicalBytes']
            or sum(c['records'] for c in m['chunks']) != m['records']):
        raise ArchiveError('Manifest totals do not match chunk descriptors')
    return m, raw


def history_exists(path):
    path = Path(path)
    if manifest_path(path).exists() or manifest_path(path).is_symlink():
        _manifest(path)
        return True
    if path.exists():
        return True
    if (archive_dir(path) / 'chunks').exists():
        raise ArchiveError('Archive chunks exist but manifest is missing')
    return False


def _chunk_raw(directory, c):
    cp = directory / c['file']
    if (directory / 'chunks').is_symlink():
        raise ArchiveError('Chunk directory may not be a symlink')
    _regular(cp)
    if cp.stat().st_size != c['compressedBytes']:
        raise ArchiveError('Compressed chunk size mismatch')
    zipped = cp.read_bytes()
    if _sha(zipped) != c['compressedSha256']:
        raise ArchiveError('Compressed chunk digest mismatch')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(zipped)) as handle:
            raw = handle.read(c['rawBytes'] + 1)
    except (OSError, EOFError) as exc:
        raise ArchiveError('Invalid gzip chunk') from exc
    if len(raw) != c['rawBytes'] or _sha(raw) != c['rawSha256']:
        raise ArchiveError('Uncompressed chunk integrity mismatch')
    if _record_count(raw) != c['records']:
        raise ArchiveError('Chunk record count mismatch')
    return raw


def _verified_binary(path, m):
    """Materialize to an anonymous temporary file, never an untracked repo copy."""
    tmp = tempfile.TemporaryFile('w+b')
    h, size, records, last = hashlib.sha256(), 0, 0, b''
    try:
        for c in m['chunks']:
            raw = _chunk_raw(archive_dir(path), c)
            if last and last != b'\n' and not raw.startswith(b'\n'):
                raise ArchiveError('Chunk join would split or concatenate JSON records')
            tmp.write(raw)
            h.update(raw)
            size += len(raw)
            records += c['records']
            last = raw[-1:]
        if (h.hexdigest() != m['logicalSha256'] or size != m['logicalBytes']
                or records != m['records'] or (last == b'\n') != m['endsWithNewline']):
            raise ArchiveError('Logical history digest/count/size mismatch')
        if Path(path).exists():
            _regular(Path(path))
            with Path(path).open('rb') as old:
                if hashlib.file_digest(old, 'sha256').hexdigest() != m['logicalSha256']:
                    raise ArchiveError('Legacy file conflicts with archived history')
        anchor = m.get('migrationAnchor')
        if anchor is not None:
            if (not isinstance(anchor, dict) or not _integer(anchor.get('logicalBytes'), size)
                    or not _integer(anchor.get('records'), records)
                    or not HEX.fullmatch(str(anchor.get('logicalSha256', '')))):
                raise ArchiveError('Invalid original-history preservation anchor')
            tmp.seek(0)
            remaining, prefix_hash = anchor['logicalBytes'], hashlib.sha256()
            while remaining:
                piece = tmp.read(min(remaining, 1024 * 1024))
                if not piece: raise ArchiveError('Original history prefix is truncated')
                prefix_hash.update(piece)
                remaining -= len(piece)
            if prefix_hash.hexdigest() != anchor['logicalSha256']:
                raise ArchiveError('Original history bytes were not preserved')
        tmp.seek(0)
        return tmp
    except BaseException:
        tmp.close()
        raise


def open_history(path, mode='r', encoding='utf-8'):
    """Read a logical history or ordinary file; writers must use append_history."""
    if mode not in ('r', 'rb'):
        raise ValueError('open_history is read-only')
    path = Path(path)
    m, _ = _manifest(path)
    if m is None:
        if not history_exists(path):
            raise FileNotFoundError(path)
        return path.open(mode, **({} if mode == 'rb' else {'encoding': encoding}))
    raw = _verified_binary(path, m)
    return raw if mode == 'rb' else io.TextIOWrapper(raw, encoding=encoding)


def read_history_text(path, encoding='utf-8'):
    with open_history(path, encoding=encoding) as handle:
        return handle.read()


def history_digest(path):
    with open_history(path, 'rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


@contextmanager
def _writer_lock(path):
    try:
        import fcntl
    except ImportError as exc:
        raise ArchiveError('Archive writing requires POSIX file locking; readers are portable') from exc
    directory = archive_dir(path)
    if directory.is_symlink():
        raise ArchiveError('Archive directory may not be a symlink')
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / '.writer.lock'
    if lock.is_symlink():
        raise ArchiveError('Writer lock may not be a symlink')
    with lock.open('a+b') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArchiveError('Concurrent archive writer; retry later') from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if hasattr(os, 'O_DIRECTORY'):
            dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        Path(name).unlink(missing_ok=True)


def _store_chunk(directory, raw):
    records = _record_count(raw)
    name = 'chunks/' + _sha(raw) + '.jsonl.gz'
    target = directory / name
    if (directory / 'chunks').is_symlink():
        raise ArchiveError('Chunk directory may not be a symlink')
    if target.exists():
        _regular(target)
        zipped = target.read_bytes()
        if len(zipped) > MAX_COMPRESSED_BYTES:
            raise ArchiveError('Existing immutable chunk is too large')
    else:
        zipped = gzip.compress(raw, compresslevel=6, mtime=0)
        if len(zipped) > MAX_COMPRESSED_BYTES:
            raise ArchiveError('Compressed chunk size bound exceeded')
        _atomic_file(target, zipped)
    c = dict(file=name, rawBytes=len(raw), rawSha256=_sha(raw), records=records,
             compressedBytes=len(zipped), compressedSha256=_sha(zipped))
    if _chunk_raw(directory, c) != raw:
        raise ArchiveError('Content-address collision/corruption')
    return c


def _chunk_stream(handle, limit):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= CHUNK_BYTES:
        raise ValueError('Chunk limit must be in 1..8 MiB')
    buffer = bytearray()
    while True:
        line = handle.readline(CHUNK_BYTES + 1)
        if not line:
            break
        if len(line) > CHUNK_BYTES:
            raise ArchiveError('Individual JSONL line exceeds 8 MiB; no data discarded')
        if buffer and len(buffer) + len(line) > limit:
            yield bytes(buffer)
            buffer.clear()
        buffer.extend(line)
        if len(buffer) >= limit:
            yield bytes(buffer)
            buffer.clear()
    if buffer:
        yield bytes(buffer)


def _blank_manifest():
    return dict(schemaVersion=1, role='LOSSLESS_RADAR_SNAPSHOT_ARCHIVE', encoding='utf-8',
                compression='gzip', logicalBytes=0, records=0, logicalSha256=_sha(b''),
                endsWithNewline=False, chunks=[])


def _publish(path, m):
    raw = (json.dumps(m, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ArchiveError('Manifest exceeds configured bound')
    _atomic_file(manifest_path(path), raw)


def _migrate_locked(path, limit):
    m, _ = _manifest(path)
    if m is not None:
        with _verified_binary(path, m):
            pass
        if path.exists():
            path.unlink()
        return m
    _regular(path)
    m, h, last = _blank_manifest(), hashlib.sha256(), b''
    with path.open('rb') as handle:
        for raw in _chunk_stream(handle, limit):
            c = _store_chunk(archive_dir(path), raw)
            m['chunks'].append(c)
            m['records'] += c['records']
            m['logicalBytes'] += len(raw)
            h.update(raw)
            last = raw[-1:]
    m.update(logicalSha256=h.hexdigest(), endsWithNewline=last == b'\n')
    m['migrationAnchor'] = {k: m[k] for k in ('logicalBytes','logicalSha256','records')}
    with _verified_binary(path, m):
        pass
    _publish(path, m)
    path.unlink()
    return m


def migrate_history(path=DEFAULT_PATH, *, chunk_bytes=CHUNK_BYTES):
    path = Path(path)
    with _writer_lock(path):
        return _migrate_locked(path, chunk_bytes)


def append_history(path, rows, *, expected_sha256=None):
    """Append exact newly serialized rows. Upstream identity/dedup logic is unchanged.

Small legacy fixtures remain compatible. Existing large legacy files migrate
before append; scheduled collection explicitly migrates even smaller files.
"""
    path = Path(path)
    raw = b''.join((json.dumps(row, separators=(',', ':'), ensure_ascii=False,
                               allow_nan=False) + '\n').encode('utf-8') for row in rows)
    _record_count(raw)
    if not raw:
        return
    with _writer_lock(path):
        m, before_manifest = _manifest(path)
        if m is None and path.exists() and path.stat().st_size + len(raw) < CHUNK_BYTES:
            old = path.read_bytes()
            _record_count(old)
            if expected_sha256 is not None and _sha(old) != expected_sha256:
                raise ArchiveError('History changed since deduplication')
            separator = b'\n' if old and not old.endswith(b'\n') else b''
            _atomic_file(path, old + separator + raw)
            return
        if m is None:
            if path.exists():
                m = _migrate_locked(path, CHUNK_BYTES)
            else:
                if (archive_dir(path) / 'chunks').exists():
                    raise ArchiveError('No manifest or legacy source; refusing to discard orphan history')
                m = _blank_manifest()
                _publish(path, m)
            _, before_manifest = _manifest(path)
        if expected_sha256 is not None and m['logicalSha256'] != expected_sha256:
            raise ArchiveError('History changed since deduplication')
        h = hashlib.sha256()
        with _verified_binary(path, m) as old:
            while piece := old.read(1024 * 1024):
                h.update(piece)
        if path.exists():
            path.unlink()
        if m['logicalBytes'] and not m['endsWithNewline']:
            raw = b'\n' + raw
        new = dict(m, chunks=list(m['chunks']))
        for piece in _chunk_stream(io.BytesIO(raw), CHUNK_BYTES):
            c = _store_chunk(archive_dir(path), piece)
            new['chunks'].append(c)
            new['records'] += c['records']
            new['logicalBytes'] += len(piece)
            h.update(piece)
        new.update(logicalSha256=h.hexdigest(), endsWithNewline=raw.endswith(b'\n'))
        if manifest_path(path).read_bytes() != before_manifest:
            raise ArchiveError('Manifest changed concurrently')
        _publish(path, new)


def verify_history(path=DEFAULT_PATH):
    path = Path(path)
    m, _ = _manifest(path)
    if m is None:
        with path.open('rb') as handle:
            raw = handle.read()
        return dict(storage='LEGACY', logicalBytes=len(raw), records=_record_count(raw), logicalSha256=_sha(raw))
    first, last, invalid_times = None, None, 0
    with _verified_binary(path, m) as handle:
        for line in handle:
            if not line.strip(): continue
            row = _json(line)
            try:
                at = datetime.fromisoformat(str(row.get('collected_at')).replace('Z', '+00:00'))
                if at.tzinfo is None or at.utcoffset() is None: raise ValueError('aware timestamp required')
                at = at.astimezone(timezone.utc)
                first, last = min(first, at) if first else at, max(last, at) if last else at
            except (ValueError, TypeError):
                invalid_times += 1
    return dict(storage='VERIFIED_SHARDS', logicalBytes=m['logicalBytes'], records=m['records'],
                firstCollectedAt=first.isoformat() if first else None,
                lastCollectedAt=last.isoformat() if last else None, recordsWithoutAwareReceiptTime=invalid_times,
                logicalSha256=m['logicalSha256'], chunks=len(m['chunks']),
                compressedBytes=sum(c['compressedBytes'] for c in m['chunks']),
                largestCompressedChunk=max((c['compressedBytes'] for c in m['chunks']), default=0),
                migrationAnchor=m.get('migrationAnchor'))


def stage_history(path=DEFAULT_PATH):
    """Stage ONLY manifest-referenced chunks and the obsolete legacy-path removal."""
    path = Path(path)
    verify_history(path)
    m, _ = _manifest(path)
    if m is None:
        raise ArchiveError('Migrate before staging')
    paths = [str(manifest_path(path)), *(str(archive_dir(path)/c['file']) for c in m['chunks'])]
    subprocess.run(['git', 'add', '--', *sorted(set(paths))], check=True)
    tracked = subprocess.run(['git','ls-files','--error-unmatch','--',str(path)], capture_output=True)
    if tracked.returncode == 0:
        if path.exists():
            raise ArchiveError('Remove equivalent legacy copy before staging')
        subprocess.run(['git','add','-u','--',str(path)],check=True)
    check_staged_files()


def check_staged_files():
    names = subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z']).split(b'\0')
    for name in names:
        if not name:
            continue
        size = int(subprocess.check_output(['git','cat-file','-s',b':' + name]))
        if size > STAGED_LIMIT:
            raise ArchiveError(f'Staged file exceeds 95 MiB safety cap: {os.fsdecode(name)} ({size} bytes)')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=('migrate', 'verify', 'stage', 'staged-guard'))
    ap.add_argument('--history', type=Path, default=DEFAULT_PATH)
    args = ap.parse_args()
    if args.command == 'migrate': migrate_history(args.history)
    elif args.command == 'stage': stage_history(args.history)
    elif args.command == 'staged-guard':
        check_staged_files()
        print('STAGED FILE SIZE GUARD OK')
        return
    print(json.dumps(verify_history(args.history), sort_keys=True))


if __name__ == '__main__':
    main()
