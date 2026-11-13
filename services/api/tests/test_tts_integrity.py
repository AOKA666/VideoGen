import base64
import http.client
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import generation_service as g


class TtsIntegrityTests(unittest.TestCase):
    def test_rejects_missing_completion_frame(self):
        body = json.dumps({"code": 0, "data": base64.b64encode(b"audio").decode()})
        with self.assertRaises(RuntimeError):
            g.extract_tts_response(body.encode())
        result = g.extract_tts_response((body + '{"code":20000000}').encode())
        self.assertEqual(result["audio"], b"audio")

    def test_rejects_interrupted_transport(self):
        response = Mock()
        response.read.side_effect = http.client.IncompleteRead(b"partial")
        with self.assertRaises(RuntimeError):
            g.read_tts_response(response)

    def test_missing_sentence_and_tail_are_not_interpolated(self):
        for text in ["甲乙", "甲乙丙丁"]:
            with self.assertRaises(RuntimeError):
                g._character_timeline_from_words(
                    [{"text": "甲", "start": 0, "end": 1}], text, 1.1)

    def test_numeric_normalization_is_supported(self):
        chars = g._character_timeline_from_words(
            [{"text": "十二年", "start": 0, "end": 3}], "12年", 3)
        self.assertEqual("".join(c["char"] for c in chars), "12年")
        self.assertEqual(chars[1]["end"], 2)

    def test_missing_or_out_of_bounds_timestamps_fail(self):
        with self.assertRaises(RuntimeError):
            g.align_voice_to_script(Path("missing.mp3"), [{"voice_text": "甲"}], 1, [])
        with self.assertRaises(RuntimeError):
            g._character_timeline_from_words(
                [{"text": "甲", "start": 0, "end": 20}], "甲", 1)

    def test_chunking_preserves_text_and_bounds(self):
        text = ("第一句话。第二句话，接下来继续说。\n" * 100) + "尾" * 500
        chunks = g.split_voice_text(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(0 < len(c) <= 240 for c in chunks))

    def test_real_audio_join_offsets_and_failure_preserves_previous_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=1:sample_rate=24000", str(fixture)],
                           check=True, capture_output=True)
            response = {"audio": fixture.read_bytes(), "sentences": [{"words": [
                {"word": "甲", "startTime": 0.1, "endTime": 0.8}]}]}
            output = Path(tmp) / "main.mp3"
            with patch.object(g, "split_voice_text", return_value=["甲。", "甲。"]), \
                    patch.object(g, "synthesize_volcengine_tts", return_value=response):
                result = g.synthesize_project_voice(output, [{"id": "n", "voice_text": "甲。甲。"}])
            cues = result["subtitle_timings"]
            self.assertAlmostEqual(cues[1]["start_time"], 1.1, places=2)
            self.assertAlmostEqual(result["duration_sec"], 2, delta=0.1)
            previous = output.read_bytes()
            with patch.object(g, "synthesize_volcengine_tts", return_value=response):
                with self.assertRaises(RuntimeError):
                    g.synthesize_project_voice(output, [{"id": "n", "voice_text": "甲乙。"}])
            self.assertEqual(output.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
