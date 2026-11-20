"""The inspection PLC retains both the bin and the corresponding material tag."""


def update_latch(previous, part, within_tolerance, stale_ok):
    if stale_ok and previous is not None and previous[1] == "OK":
        return previous
    return part, "OK" if within_tolerance else "NG"
