import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.generation_service import (
    SUBTITLE_ONSET_MAX_LAG_SEC, _correct_subtitle_onsets,
    _normalize_export_subtitle_timings, _timed_export_subtitle_chunks,
    narration_shot,
)


class SubtitleAlignmentTests(unittest.TestCase):
    def test_late_phrase_moves_to_uncovered_speech_onset_only(self):
        cues = [
            {"text": "前句", "start_time": 0, "end_time": 2},
            {"text": "后句", "start_time": 3.5, "end_time": 4},
            {"text": "连读", "start_time": 4, "end_time": 5},
        ]
        self.assertEqual(_correct_subtitle_onsets(cues, [(2.1, 2.5), (3.8, 3.99)]), 1)
        self.assertEqual(cues[1]["start_time"], 2.5)
        self.assertEqual(cues[2]["start_time"], 4)
        self.assertEqual(_correct_subtitle_onsets(cues, [(2.1, 2.5)]), 0)

    def test_does_not_cross_previous_cue_or_shift_normal_onsets(self):
        cues = [{"text": "前", "start_time": 0, "end_time": 2.8},
                {"text": "后", "start_time": 3, "end_time": 4}]
        self.assertEqual(_correct_subtitle_onsets(cues, [(2.1, 2.5), (2.81, 2.95)]), 0)

    def test_overlaps_do_not_delay_subsequent_onsets(self):
        result = _normalize_export_subtitle_timings([
            {"text": "一", "start_time": 0, "end_time": 1.5},
            {"text": "二", "start_time": 1, "end_time": 2.5},
            {"text": "三", "start_time": 2, "end_time": 3},
        ])
        self.assertEqual([r["start_time"] for r in result], [0, 1, 2])
        self.assertEqual([r["end_time"] for r in result], [1, 2, 3])

    def test_resplitting_retains_word_timestamps_across_pauses(self):
        cue = {"text": "甲乙", "start_time": 0, "end_time": 4,
               "character_timings": [
                   {"char": "甲", "start": 0, "end": 0.5},
                   {"char": "乙", "start": 3, "end": 4},
               ]}
        result = _timed_export_subtitle_chunks(cue, 1)
        self.assertEqual([r["start_time"] for r in result], [0, 3])
        self.assertEqual(result[0]["end_time"], 0.5)

    def test_long_lag_is_corrected(self):
        cues = [{"text": "前", "start_time": 0, "end_time": 1},
                {"text": "后", "start_time": 3.5, "end_time": 4}]
        lag = SUBTITLE_ONSET_MAX_LAG_SEC
        self.assertGreater(lag, 1.5)
        self.assertEqual(_correct_subtitle_onsets(cues, [(1.1, 1.4)]), 1)
        self.assertEqual(cues[1]["start_time"], 1.4)

    def test_narration_shot_rechecks_saved_timings_against_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "voice.mp3"
            audio.write_bytes(b"stub")
            project = {
                "audio_path": str(audio),
                "audio_duration_sec": 5,
                "raw_script": "甲乙",
                "narration_subtitle_timings": [
                    {"text": "甲乙", "start_time": 1.2, "end_time": 2.0},
                ],
            }
            with patch(
                "services.generation_service.correct_subtitle_onsets",
                return_value=1,
            ) as correct:
                shot = narration_shot(project)
            correct.assert_called_once_with(audio, shot["subtitle_timings"])

    def test_narration_shot_skips_audio_analysis_without_audio(self):
        with patch("services.generation_service.correct_subtitle_onsets") as correct:
            narration_shot({
                "audio_duration_sec": 5,
                "raw_script": "甲乙",
                "narration_subtitle_timings": [
                    {"text": "甲乙", "start_time": 1.2, "end_time": 2.0},
                ],
            })
        correct.assert_not_called()
