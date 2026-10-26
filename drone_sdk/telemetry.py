"""Stamp actual decoded state packets; querying cached state never renews age."""
import math
import time

STAMP = '_r3_received_at'


def install_receiver_stamp(tello_class):
    # DJITelloPy's receiver calls the BASE class static parse_state, not a
    # subclass override. Install once before its receiver thread starts.
    parser = tello_class.parse_state
    if getattr(parser, '_r3_timestamped', False):
        return

    def stamped(data):
        received_at = time.time()
        packet = parser(data)
        if packet:
            packet[STAMP] = received_at
        return packet

    stamped._r3_timestamped = True
    tello_class.parse_state = staticmethod(stamped)


def read_height(tello, flying, *, now=None):
    """Return (height, receipt time, source), or None for legacy test doubles.

    An empty/stale real packet returns an explicitly invalid tuple. Receipt
    time means arrival at this PC, not the aircraft sensor's sampling time.
    """
    getter = getattr(tello, 'get_current_state', None)
    if not callable(getter):
        return None
    try:
        packet = getter()
    except Exception:
        return (None, None, 'telemetry_unavailable')
    if not isinstance(packet, dict):
        return (None, None, 'telemetry_invalid')
    stamp = packet.get(STAMP)
    now = time.time() if now is None else now
    if (type(stamp) not in (float, int) or not math.isfinite(stamp)
            or not 0 <= now-stamp <= .5):
        return (None, None, 'telemetry_stale')
    height, tof = packet.get('h'), packet.get('tof')
    if type(height) is int and 0 < height <= 1000:
        return height, stamp, 'h'
    if flying and type(tof) is int and 20 <= tof <= 300:
        return tof, stamp, 'tof'
    if not flying and type(height) is int and height == 0:
        return 0, stamp, 'ground'
    return (None, None, 'height_invalid')
