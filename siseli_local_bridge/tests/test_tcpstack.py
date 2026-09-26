import unittest
from unittest.mock import patch

from src.siseli_local_bridge import tcpstack
from src.siseli_local_bridge.tcpstack import Connection


class TestReceiveClassification(unittest.TestCase):
    """Connection.receive() logs a duplicate, a gap and an overlap differently.

    A healthy Wi-Fi session re-sends replies whose ACK the peer has not seen, so a
    duplicate must not raise the same warning as a segment that actually skipped ahead.
    """

    EXPECTED = 1000

    def setUp(self):
        patcher = patch.object(tcpstack, "sendp")
        self.sendp = patcher.start()
        self.addCleanup(patcher.stop)
        log_patcher = patch.object(tcpstack, "log")
        self.log = log_patcher.start()
        self.addCleanup(log_patcher.stop)

        self.conn = Connection("192.168.1.50", 50003, "192.168.1.60", 1883, "aa:bb:cc:dd:ee:ff", None)
        self.conn.their_next_seq = self.EXPECTED

    def _logged(self):
        """(level, message) of every log call since setUp."""
        return [(call.kwargs.get("level", "info"), call.args[0]) for call in self.log.call_args_list]

    def _assert_reacked_untouched(self, buffer_before=b""):
        self.assertEqual(self.conn.recv_buffer, buffer_before)
        self.assertEqual(self.conn.their_next_seq, self.EXPECTED)
        self.sendp.assert_called_once()  # the re-ACK

    def test_in_order_segment_extends_buffer_and_logs_nothing(self):
        self.assertTrue(self.conn.receive(self.EXPECTED, b"abcd"))
        self.assertEqual(self.conn.recv_buffer, b"abcd")
        self.assertEqual(self.conn.their_next_seq, self.EXPECTED + 4)
        self.assertEqual(self._logged(), [])

    def test_exact_duplicate_of_last_segment_is_debug_not_warning(self):
        self.assertFalse(self.conn.receive(self.EXPECTED - 428, b"x" * 428))
        self._assert_reacked_untouched()
        levels = [level for level, _ in self._logged()]
        self.assertEqual(levels, ["debug"])
        message = self._logged()[0][1]
        self.assertIn("[LOCAL CLOUD DUP]", message)
        self.assertIn("duplicate 428B", message)
        self.assertNotIn("DIAG", message)
        self.assertEqual(self.conn.duplicate_segments, 1)
        self.assertEqual(self.conn.gap_segments, 0)

    def test_older_duplicate_is_still_a_duplicate(self):
        # Two segments back: seq is 1376 bytes behind, the segment is 688 bytes long.
        self.assertFalse(self.conn.receive(self.EXPECTED - 1376, b"x" * 688))
        self._assert_reacked_untouched()
        self.assertEqual([level for level, _ in self._logged()], ["debug"])
        self.assertEqual(self.conn.duplicate_segments, 1)

    def test_duplicate_counter_counts_every_duplicate(self):
        for _ in range(3):
            self.conn.receive(self.EXPECTED - 10, b"x" * 10)
        self.assertEqual(self.conn.duplicate_segments, 3)
        self.assertIn("#3", self._logged()[-1][1])

    def test_gap_is_a_diag_warning_and_keeps_nothing(self):
        self.assertFalse(self.conn.receive(self.EXPECTED + 688, b"x" * 688))
        self._assert_reacked_untouched()
        self.assertEqual([level for level, _ in self._logged()], ["warning"])
        message = self._logged()[0][1]
        self.assertIn("[LOCAL CLOUD DIAG]", message)
        self.assertIn("gap 688B", message)
        self.assertIn("688B beyond expected=1000", message)
        self.assertEqual(self.conn.gap_segments, 1)
        self.assertEqual(self.conn.duplicate_segments, 0)

    def test_overlap_with_new_bytes_is_a_diag_warning(self):
        # Starts 40 bytes before expected, 100 bytes long: 40 old bytes, 60 new ones.
        self.assertFalse(self.conn.receive(self.EXPECTED - 40, b"x" * 100))
        self._assert_reacked_untouched()
        self.assertEqual([level for level, _ in self._logged()], ["warning"])
        message = self._logged()[0][1]
        self.assertIn("[LOCAL CLOUD DIAG]", message)
        self.assertIn("overlap 100B", message)
        self.assertIn("40B already received, 60B new discarded", message)
        self.assertEqual(self.conn.duplicate_segments, 0)
        self.assertEqual(self.conn.gap_segments, 0)

    def test_empty_off_sequence_segment_stays_silent(self):
        # A bare ACK or keepalive probe is normal traffic; handle_tcp never even calls
        # receive() for it, and a FIN goes through with an empty payload.
        self.assertFalse(self.conn.receive(self.EXPECTED - 1, b""))
        self.assertFalse(self.conn.receive(self.EXPECTED + 5, b""))
        self.assertEqual(self._logged(), [])
        self.assertEqual(self.conn.duplicate_segments, 0)
        self.assertEqual(self.conn.gap_segments, 0)

    def test_classification_survives_sequence_wraparound(self):
        # expected has wrapped past 2**32; a segment sent just before the wrap is an
        # older duplicate, one sent after it but ahead of expected is a gap.
        self.conn.their_next_seq = 50
        self.assertFalse(self.conn.receive(0xFFFFFFF0, b"x" * 16))  # ends at 0, 50 bytes behind
        self.assertEqual(self.conn.duplicate_segments, 1)
        self.assertFalse(self.conn.receive(70, b"x" * 8))  # 20 beyond expected
        self.assertEqual(self.conn.gap_segments, 1)
        self.assertIn("20B beyond expected=50", self._logged()[-1][1])

    def test_receive_before_syn_returns_false_without_logging(self):
        self.conn.their_next_seq = None
        self.assertFalse(self.conn.receive(5, b"data"))
        self.assertEqual(self._logged(), [])
        self.sendp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
