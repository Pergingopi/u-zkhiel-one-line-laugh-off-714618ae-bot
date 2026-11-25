"""One-Line Laugh-Off entry.

Writes exactly one funny line to $MAKEFUN_OUTPUT_DIR/entry.txt
(default /output). Deterministic: the same fixed line every run.

Gate (sealed scorer): /output/entry.txt must be UTF-8 text of
1 to 200 characters, not only white space.

This entrypoint validates harder than the gate itself: no control
characters at all (not even tab), no Unicode line/paragraph
separators or invisible format characters, and no leading or
trailing whitespace, so the published file is exactly one clean
line. Writes are atomic (temp file + rename), fsynced, read back
twice (temp file, then the published target), and stale temp
files left by a crashed earlier run are swept before writing.
"""

import os
import stat
import tempfile
import time
import unicodedata

LINE = (
    "I taught my parrot to say 'we need to talk' \u2014 now my "
    "girlfriend thinks the bird is dumping her."
)

DEFAULT_OUTPUT_DIR = "/output"
TMP_PREFIX = ".entry-"
TMP_SUFFIX = ".tmp"
# A temp file this old can only belong to a dead run: a live run
# holds its temp file for milliseconds. The age guard also keeps a
# concurrent run from ever sweeping another run's fresh temp file.
STALE_TMP_AGE_SECONDS = 60.0


def _resolve_output_dir():
    """Output dir from the environment, blank/whitespace-only = default."""
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None:
        return DEFAULT_OUTPUT_DIR
    cleaned = raw.strip()
    return cleaned or DEFAULT_OUTPUT_DIR


def _valid(text):
    """Strict gate check.

    1..200 chars, not only whitespace, exactly one line: no control
    characters of any kind, no Unicode line/paragraph separators,
    no invisible format characters (zero-width etc.), and no
    leading/trailing whitespace.
    """
    if not isinstance(text, str):
        return False
    if not 1 <= len(text) <= 200:
        return False
    if not text.strip():
        return False
    if text != text.strip():
        return False
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            return False
    return True


def _sweep_stale_tmp(out_dir):
    """Remove temp files a crashed earlier run left behind.

    Only our own prefix/suffix, only older than STALE_TMP_AGE_SECONDS.
    Best effort: any failure here must not block the run.
    """
    now = time.time()
    try:
        names = os.listdir(out_dir)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not (name.startswith(TMP_PREFIX) and name.endswith(TMP_SUFFIX)):
            continue
        path = os.path.join(out_dir, name)
        try:
            if now - os.path.getmtime(path) < STALE_TMP_AGE_SECONDS:
                continue
            os.unlink(path)
            removed += 1
        except OSError:
            continue
    return removed


def _read_and_validate(path, expected):
    """Read a file back, require exact bytes and a gate-passing line."""
    with open(path, "rb") as f:
        back = f.read()
    if back != expected:
        raise SystemExit("read-back mismatch in %s" % path)
    try:
        text = back.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit("read-back is not valid UTF-8 in %s" % path)
    if not _valid(text):
        raise SystemExit("read-back fails the gate check in %s" % path)
    return text


def main():
    out_dir = _resolve_output_dir()
    os.makedirs(out_dir, exist_ok=True)

    if not _valid(LINE):
        raise SystemExit("entry line fails the gate check")

    data = LINE.encode("utf-8")
    _sweep_stale_tmp(out_dir)

    fd, tmp_path = tempfile.mkstemp(
        prefix=TMP_PREFIX, suffix=TMP_SUFFIX, dir=out_dir
    )
    try:
        os.chmod(tmp_path, 0o644)
        with os.fdopen(fd, "wb") as f:
            written = f.write(data)
            if written != len(data):
                raise SystemExit(
                    "short write: %d of %d bytes" % (written, len(data))
                )
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o644)

        # Read-back + validate the temp file before publishing.
        _read_and_validate(tmp_path, data)

        target = os.path.join(out_dir, "entry.txt")
        os.replace(tmp_path, target)
        os.chmod(target, 0o644)

        # fsync the directory so the rename is durable.
        dir_fd = os.open(out_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

        # Read-back + validate the published target itself, and its mode.
        _read_and_validate(target, data)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        if mode != 0o644:
            raise SystemExit("output mode is not 0644: %o" % mode)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    main()
