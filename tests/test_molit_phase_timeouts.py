import unittest
from unittest.mock import patch
import collect_molit_capital_csv as c

class PhaseTimeoutTests(unittest.TestCase):
    def test_phase_timeouts_and_failure_diagnostics(self):
        for path, expected in [(c.PAGE_PATH,30),(c.SIDO_PATH,30),(c.COUNT_PATH,30),(c.CSV_PATH,120)]:
            with self.subTest(path=path):
                client=c.PublicCSVClient(timeout=45,pause=0,metadata_timeout=30,csv_timeout=120)
                with patch.object(client.opener,"open",side_effect=TimeoutError()) as call:
                    with self.assertRaises(c.TransportError):
                        client._request(path)
                self.assertEqual(call.call_args.kwargs["timeout"],expected)
                self.assertEqual(client.last_request["category"],"timeout")
                self.assertIn("elapsed_seconds",client.last_request)
    def test_legacy_timeout(self):
        client=c.PublicCSVClient(timeout=17)
        self.assertEqual((client.metadata_timeout,client.csv_timeout),(17,17))
    def test_cli_invalid_timeout(self):
        for value in ("0","-1","nan","inf"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as raised:
                c.main(["--metadata-timeout",value,"--plan-only"])
            self.assertEqual(raised.exception.code,2)

if __name__=="__main__":
    unittest.main()
