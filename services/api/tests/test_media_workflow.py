from __future__ import annotations
import io
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException, UploadFile
from PIL import Image
from services import store
from services.generation_service import build_export_subtitles
from services.media_service import assemble_clips, media_command
from routers import media
from routers.projects import ProjectCreate, create_project


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

    def test_uploaded_audio_is_saved(self):
        project = create_project(ProjectCreate(raw_script='上传音频测试。【带货开始】'))['project']
        path = self.root / 'voice.mp3'
        media_command(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
                       '-t','1.4','-q:a','4',str(path)])
        uploaded = media.upload_project_audio(
            project['id'], UploadFile(file=io.BytesIO(path.read_bytes()), filename='旁白.mp3'))
        self.assertAlmostEqual(uploaded['duration_sec'], 1.4, places=2)
        self.assertEqual(uploaded['audio_source'], 'upload')
        with self.assertRaises(HTTPException):
            media.upload_project_audio(project['id'], UploadFile(file=io.BytesIO(b'x'), filename='bad.txt'))
        self.assertTrue((store.project_dir(project['id']) / 'voice').is_dir())


if __name__ == '__main__': unittest.main()
