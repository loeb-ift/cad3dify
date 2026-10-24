import hashlib
import io
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image

from cad3dify.local_ocr import inspect_region, reviewed_context


class LocalOCRTests(unittest.TestCase):
    def setUp(self):
        stream = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
        self.source = stream.getvalue()

    def test_actual_ocr_path_and_original_coordinates(self):
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t2\t3\t10\t5\t88\t104\n")
        outputs = [subprocess.CompletedProcess([], 0, tsv, ""),
                   subprocess.CompletedProcess([], 0, "tesseract test\n", "")]
        with patch("cad3dify.local_ocr.subprocess.run", side_effect=outputs) as run:
            report, crop = inspect_region(self.source, (10, 20, 50, 60), executable="tesseract")
        self.assertEqual(run.call_count, 2)
        self.assertIn("tsv", run.call_args_list[0].args[0])
        self.assertEqual(report["observations"][0]["source_box"], [12, 23, 22, 28])
        self.assertEqual(report["crop_sha256"], hashlib.sha256(crop).hexdigest())
        reviews = [{"id": 0, "confirmed": False, "reviewed_text": "104"}]
        self.assertEqual(reviewed_context(report, reviews, self.source), "")
        reviews[0]["confirmed"] = True
        self.assertIn('"reviewed_text": "104"', reviewed_context(report, reviews, self.source))
        with self.assertRaises(ValueError):
            reviewed_context(report, reviews, b"another drawing")

    def test_failed_backend_is_not_reported_as_success(self):
        with patch("cad3dify.local_ocr.subprocess.run", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(RuntimeError):
                inspect_region(self.source, (0, 0, 50, 50), executable="missing")

    def test_invalid_crop_does_not_run_ocr(self):
        with patch("cad3dify.local_ocr.subprocess.run") as run:
            with self.assertRaises(ValueError):
                inspect_region(self.source, (0, 0, 101, 50), executable="tesseract")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
