import unittest

from app import parse_multipart


class MultipartTests(unittest.TestCase):
    def test_preserves_binary_content_ending_in_newlines(self):
        boundary = "test-boundary"
        binary = b"%PDF-1.7\x00payload\r\n"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="scan_pdf"; filename="scan.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + binary + f"\r\n--{boundary}--\r\n".encode()

        fields = parse_multipart(body, f"multipart/form-data; boundary={boundary}")

        self.assertEqual(fields["_files"]["scan_pdf"]["content"], binary)


if __name__ == "__main__":
    unittest.main()
