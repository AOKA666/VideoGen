from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.generation_service import generate_openai_image  # noqa: E402


class _Response:
    def __init__(self, body: dict):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")


class OpenAiImageGenerationTests(unittest.TestCase):
    def test_generates_portrait_png_from_base64_response(self) -> None:
        response = _Response({"data": [{"b64_json": base64.b64encode(b"png-data").decode()}]})
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "OPENAI_IMAGE_API_KEY": "test-key",
                "OPENAI_ENDPOINT": "https://openai.test/v1",
                "OPENAI_IMAGE_MODEL": "gpt-image-test",
                "OPENAI_IMAGE_QUALITY": "high",
            },
        ), patch("services.generation_service.urllib.request.urlopen", return_value=response) as urlopen:
            output = Path(temp_dir) / "shot.png"
            result = generate_openai_image(output, {}, prompt_override="test prompt")
            output_bytes = output.read_bytes()

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("https://openai.test/v1/images/generations", request.full_url)
        self.assertEqual("gpt-image-test", payload["model"])
        self.assertEqual("1440x2560", payload["size"])
        self.assertEqual("high", payload["quality"])
        self.assertEqual(b"png-data", output_bytes)
        self.assertEqual("openai", result["provider"])


if __name__ == "__main__":
    unittest.main()
