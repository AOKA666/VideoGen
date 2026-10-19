from __future__ import annotations
import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException, UploadFile
from PIL import Image
from services import store
from services.generation_service import build_export_subtitles
from services.media_service import assemble_clips, media_command, probe_video, render_video
from routers import media
from routers.projects import ProjectCreate, create_project, get_project


def asset(identifier, duration):
    return {"id": identifier, "duration_sec": duration, "file_url": "/test.mp4", "local_path": "test.mp4", "file_name": identifier}


class AssemblyTests(unittest.TestCase):
    def test_single_long_clip_is_trimmed(self):
        shots = assemble_clips([asset('a', 15)], 3.123456, 'p')
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[-1]['end_time'], 3.123456)
        self.assertAlmostEqual(shots[0]['trimmed_sec'], 11.876544)

    def test_exhausts_pool_before_reusing(self):
        shots = assemble_clips([asset('a', 2), asset('b', 2)], 11, 'p')
        self.assertEqual(len(shots), 6)
        self.assertEqual(len({s['selected_asset_id'] for s in shots[:2]}), 2)
        self.assertEqual(shots[-1]['end_time'], 11)
        self.assertEqual(shots[-1]['duration_sec'], 1)
        for a, b in zip(shots, shots[1:]):
            self.assertEqual(a['end_time'], b['start_time'])
            self.assertNotEqual(a['selected_asset_id'], b['selected_asset_id'])

    def test_exact_match_has_no_extra_clip(self):
        self.assertEqual(len(assemble_clips([asset('a', 2)], 6, 'p')), 3)

    def test_invalid_media_and_duration_rejected(self):
        for assets, duration in [([], 1), ([asset('a', 0)], 1), ([asset('a', 1)], 0), ([asset('a', 1)], float('nan'))]:
            with self.assertRaises(ValueError): assemble_clips(assets, duration, 'p')

    def test_random_lengths_have_no_gaps_or_drift(self):
        for _ in range(60):
            duration = random.uniform(0.1, 100)
            shots = assemble_clips([asset('a', 0.123456), asset('b', 2.654321)], duration, 'p')
            self.assertEqual(round(sum(s['duration_sec'] for s in shots), 6), round(duration, 6))
            self.assertEqual(shots[-1]['end_time'], round(duration, 6))


class SubtitleRuleTests(unittest.TestCase):
    def test_video_subtitle_lines_are_at_most_nine_characters(self):
        shot = {
            "id": "narration", "shot_index": 1, "start_time": 0, "end_time": 6,
            "voice_text": "真正厉害的人往往懂得沉默并且保持清醒，这是一种难得的智慧。",
        }
        subtitles = build_export_subtitles([shot], max_hanzi=9)
        self.assertTrue(subtitles)
        self.assertTrue(all(item["hanzi_count"] <= 9 for item in subtitles))
        self.assertEqual(subtitles[-1]["end_time"], 6)


class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'source.mp4'
        media_command(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=red:s=320x180:r=30',
                       '-t','0.7','-c:v','libx264','-pix_fmt','yuv420p',str(cls.source)])

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.previous = store.storage_dir()
        self.storage = tempfile.TemporaryDirectory()
        store.configure_storage(self.storage.name)
        store.ensure_storage()

    def tearDown(self):
        store.configure_storage(self.previous)
        self.storage.cleanup()

    def upload(self):
        return media.upload_video(UploadFile(file=io.BytesIO(self.source.read_bytes()), filename='测试素材.mp4'))

    def test_upload_validates_video_and_persists_duration(self):
        uploaded = self.upload()
        self.assertAlmostEqual(uploaded['duration_sec'], .7, places=2)
        self.assertEqual(media.list_media()['videos'][0]['id'], uploaded['id'])
        media.delete_video(uploaded['id'])
        self.assertFalse(Path(uploaded['local_path']).exists())

    def test_invalid_upload_cleans_up_file(self):
        with self.assertRaises(HTTPException):
            media.upload_video(UploadFile(file=io.BytesIO(b'not video'), filename='bad.mp4'))
        self.assertEqual(list((store.assets_dir() / 'videos').iterdir()), [])

    def test_template_dimensions_and_builtin(self):
        self.assertEqual(media.templates()[0]['id'], 'daodejing')
        content=io.BytesIO(); Image.new('RGB',(160,90)).save(content,format='PNG');content.seek(0)
        with self.assertRaises(HTTPException):
            media.upload_template(UploadFile(file=content,filename='wide.png'))
        content=io.BytesIO(); Image.new('RGB',(90,160)).save(content,format='PNG');content.seek(0)
        result=media.upload_template(UploadFile(file=content,filename='portrait.png'))
        self.assertTrue(Path(result['local_path']).is_file())

    def test_generate_and_render_end_to_end_with_stub_voice(self):
        uploaded = self.upload()
        project = create_project(ProjectCreate(raw_script='测试视频拼接。', workflow_type='video_library'))['project']
        def synthesize(path, shots, voice_type, speech_rate):
            self.assertEqual(voice_type, 'S_6Sd6jOE42')
            media_command(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','1.13',str(path)])
            return {'duration_sec':1.13,'subtitle_timings':[{'text':'测试视频拼接','start_time':0,'end_time':1.13}]}
        with patch.object(media,'synthesize_project_voice',side_effect=synthesize):
            media.generate_worker(project['id'])
        state = get_project(project['id'])
        result = state['project']
        # One click produces the finished MP4; the storyboard never surfaces as a separate step.
        self.assertEqual(result['status'], 'video_done', result.get('generation_error'))
        self.assertTrue(result['render_url'])
        self.assertEqual(state['shots'][-1]['end_time'], 1.13)
        with self.assertRaises(HTTPException): media.delete_video(uploaded['id'])
        output = store.storage_dir() / result['render_url'].removeprefix('/storage/')
        probe = probe_video(output)
        self.assertEqual((probe['width'],probe['height']), (1080,1920))
        self.assertLessEqual(abs(probe['duration_sec']-1.13), 1/30)
        details = json.loads(media_command(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
        self.assertEqual(sum(s['codec_type']=='audio' for s in details['streams']), 1)
        screenshot = self.root / 'render-check.png'
        media_command(['ffmpeg','-y','-v','error','-ss','0.2','-i',str(output),'-frames:v','1',str(screenshot)])
        with Image.open(screenshot) as frame:
            r,g,b=frame.getpixel((540,960)); self.assertGreater(r,200); self.assertLess(g,30)
            r,g,b=frame.getpixel((540,100)); self.assertLess(r,30); self.assertGreater(g,30)
            subtitle_pixels = sum(
                1
                for x in range(0, 1080, 2)
                for y in range(1200, 1330, 2)
                if all(channel > 220 for channel in frame.getpixel((x, y)))
            )
            self.assertGreater(subtitle_pixels, 100)

    def test_uploaded_audio_skips_tts_and_renders(self):
        self.upload()
        project = create_project(ProjectCreate(raw_script='上传音频测试。', workflow_type='video_library'))['project']
        path = self.root / 'voice.mp3'
        media_command(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
                       '-t','1.4','-q:a','4',str(path)])
        uploaded = media.upload_project_audio(
            project['id'], UploadFile(file=io.BytesIO(path.read_bytes()), filename='旁白.mp3'))
        self.assertAlmostEqual(uploaded['duration_sec'], 1.4, places=2)
        self.assertEqual(uploaded['audio_source'], 'upload')
        with self.assertRaises(HTTPException):
            media.upload_project_audio(project['id'], UploadFile(file=io.BytesIO(b'x'), filename='bad.txt'))
        with patch.object(media, 'synthesize_project_voice', side_effect=AssertionError('TTS must be skipped')):
            media.generate_worker(project['id'], 'upload')
        state = get_project(project['id'])
        result = state['project']
        self.assertEqual(result['status'], 'video_done', result.get('generation_error'))
        self.assertEqual(result['audio_source'], 'upload')
        self.assertTrue(result['subtitle_url'])
        self.assertTrue((store.storage_dir() / result['render_url'].removeprefix('/storage/')).is_file())

    def test_empty_library_and_failed_voice_are_actionable(self):
        project=create_project(ProjectCreate(raw_script='test',workflow_type='video_library'))['project']
        with self.assertRaises(HTTPException): media.generate_video(project['id'])
        self.upload()
        with patch.object(media,'synthesize_project_voice',side_effect=RuntimeError('TTS unavailable')):
            media.generate_worker(project['id'])
        self.assertEqual(get_project(project['id'])['project']['status'],'video_failed')


if __name__ == '__main__': unittest.main()
