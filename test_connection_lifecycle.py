"""Real DJITelloPy registry/destructor semantics, with all networking blocked."""
import unittest
from unittest.mock import patch

import djitellopy.tello as transport
from drone_sdk import _driver as drv


class ConnectionLifecycleTests(unittest.TestCase):
    def setUp(self):
        for target, name, value in (
            (transport, 'threads_initialized', True),
            (transport, 'drones', {}),
            (drv, '_tello', None), (drv, 'SIMULATED', True),
            (drv, '_keepalive_stop', None), (drv, '_flying', False),
            (drv, '_height_cm', 0), (drv, '_faults', 0),
            (drv, '_link_ok', True), (drv, '_last_error', ''),
            (drv, 'CONNECT_RETRY_DELAY_S', 0),
        ):
            p = patch.object(target, name, value)
            p.start(); self.addCleanup(p.stop)
        for p in (
            patch('socket.socket', side_effect=AssertionError('No hardware')),
            patch.object(drv, 'set_speed', return_value=True),
            patch.object(drv, '_keepalive_loop', return_value=None),
        ):
            p.start(); self.addCleanup(p.stop)

    def test_retry_preserves_one_registry_owner_and_refreshes_handshake(self):
        attempts = []
        def handshake(client, wait_for_state=True):
            udp = client.get_own_udp_object()
            self.assertEqual(udp, {'responses': [], 'state': {}})
            attempts.append(client)
            if len(attempts) < 3:
                udp['responses'].append(b'old reply')
                udp['state'] = {'bat': 1}
                raise UnicodeDecodeError('utf-8', b'\xcc', 0, 1, 'invalid byte')
            udp['state'] = {'bat': 88, 'h': 0, 'time': 0}
        with patch.object(transport.Tello, 'connect', handshake):
            info = drv.connect()
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len({id(x) for x in attempts}), 1)
        self.assertTrue(info['connected'])
        self.assertEqual(info['battery'], 88)
        drv.disconnect(safe=False)

    def test_failed_session_destructor_cannot_delete_replacement(self):
        failed = []
        def handshake(client, wait_for_state=True):
            failed.append(client)
            client.get_own_udp_object()
            raise RuntimeError('No acknowledgement')
        with patch.object(transport.Tello, 'connect', handshake):
            with self.assertRaisesRegex(RuntimeError, 'tello_not_ready'):
                drv.connect()
        self.assertEqual(len(failed), drv.CONNECT_ATTEMPTS)
        self.assertIsNone(drv._tello)
        self.assertTrue(drv.SIMULATED)
        self.assertEqual(transport.drones, {})
        replacement = drv._tello_class(5)()
        entry = replacement.get_own_udp_object()
        for old in failed:
            old.__del__()
        self.assertIs(replacement.get_own_udp_object(), entry)
        replacement.end()

    def test_explicit_disconnect_then_destructor_is_idempotent(self):
        cls = drv._tello_class(5)
        old = cls()
        old.end()
        new = cls()
        entry = new.get_own_udp_object()
        old.__del__()
        self.assertIs(new.get_own_udp_object(), entry)
        new.end()


if __name__ == '__main__':
    unittest.main()
