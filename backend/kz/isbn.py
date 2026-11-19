"""ISBN-10 / ISBN-13 validation and normalisation (same rules as the prototype's ISBN control)."""
import re


def clean(s):
    return re.sub(r'[\s-]', '', str(s or '')).upper()


def valid10(s):
    if not re.fullmatch(r'\d{9}[\dX]', s):
        return False
    total = sum((10 - i) * (10 if c == 'X' else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def valid13(s):
    if not re.fullmatch(r'97[89]\d{10}', s):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def to13(s):
    core = '978' + s[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def check(raw):
    """Returns (clean, isbn13, error). error is None when valid."""
    s = clean(raw)
    if not s:
        return '', None, 'ISBN is required'
    if len(s) == 10:
        return (s, to13(s), None) if valid10(s) else (s, None, 'Invalid ISBN-10 check digit')
    if len(s) == 13:
        return (s, s, None) if valid13(s) else (s, None, 'Invalid ISBN-13 (must start 978/979 and have a correct check digit)')
    return s, None, 'ISBN must have 10 or 13 characters'
