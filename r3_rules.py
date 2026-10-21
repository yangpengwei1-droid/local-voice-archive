"""Read-only source numbering and exact date reconciliation rules."""
import datetime
import re
from collections import Counter


def same_fields(saved, incoming, date1904=False):
    if saved['G'] != incoming['G'] or saved['V'] != incoming['V']:
        return False
    if 'U' in saved and saved['U']!=incoming.get('U'):return False
    a, b = saved['X'], incoming['X']
    if a == b:
        return True
    def canonical(value):
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                return datetime.date.fromisoformat(value)
            # Preserve fractional times and malformed dates as raw; never truncate.
            if not re.fullmatch(r'\d+(?:\.0+)?', value):
                return None
            serial = int(float(value))
            if (not date1904 and serial < 1) or (not date1904 and serial == 60):
                return None
            epoch = datetime.date(1904, 1, 1) if date1904 else datetime.date(1899, 12, 31) if serial < 60 else datetime.date(1899, 12, 30)
            return epoch + datetime.timedelta(days=serial)
        except (ValueError, OverflowError):
            return None
    ca, cb = canonical(a), canonical(b)
    return ca is not None and cb is not None and ca == cb


def annual_preview(rows, year):
    valid, anomalous = [], []
    for r in rows:
        n = r['number']
        if not n.startswith(str(year)):
            continue
        suffix = n[4:]
        if not re.fullmatch(r'[1-9]\d*', suffix):
            anomalous.append(r['row'])
        else:
            valid.append(int(suffix))
    duplicates = [n for n, count in Counter(valid).items() if count > 1]
    blocked = bool(anomalous or duplicates)
    return dict(year=year, next_number=None if blocked else str(year)+str(max(valid, default=0)+1),
                anomalous_rows=anomalous, duplicate_sequences=duplicates, blocked=blocked,
                advisory_only=True, message='源快照预览，未占号；正式提交未启用，尚未同步线上')
