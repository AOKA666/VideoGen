import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Archive, Check, ChevronLeft, ChevronRight, Download, Film, FolderOpen, ImagePlus, Mic, Music, Save, Scissors, Settings, Trash2, Wand2 } from 'lucide-react';
import './styles.css';
import './studio.css';
import { MediaLibrary, TemplatePicker } from './MediaWorkflow';
import { ProductPlacementPanel } from './ProductWorkflow';

const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'videogen.sidebarCollapsed';
const MAX_VOICE_VOLUME_PERCENT = 300;
const PRODUCT_MARKER = '【带货开始】';

function renderScriptHighlight(text) {
  const value = String(text || '');
  const nodes = [];
  let cursor = 0;
  let key = 0;
  while (cursor < value.length) {
    const index = value.indexOf(PRODUCT_MARKER, cursor);
    if (index < 0) {
      nodes.push(value.slice(cursor));
      break;
    }
    if (index > cursor) nodes.push(value.slice(cursor, index));
    nodes.push(<mark key={key} className="product-marker-highlight">{PRODUCT_MARKER}</mark>);
    key += 1;
    cursor = index + PRODUCT_MARKER.length;
  }
  nodes.push('\uFEFF');
  return nodes;
}
const DEFAULT_COVER_TITLE_POSITIONS = {
  line1: { x: 0.5, y: 0.18, font_size: 100 },
  line2: { x: 0.5, y: 0.25, font_size: 100 },
};
const VOICE_OPTIONS = [
  { value: 'zh_male_m191_uranus_bigtts', label: '男声 · 沉稳叙事' },
  { value: 'zh_male_dongfanghaoran_uranus_bigtts', label: '男声 · 东方浩然' },
  { value: 'zh_male_dayi_uranus_bigtts', label: '男声 · 大义' },
  { value: 'zh_female_vv_uranus_bigtts', label: '女声 · 清晰自然' },
  { value: 'S_6Sd6jOE42', label: '王立群' },
];

async function request(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function stripScriptParagraphNumbers(value) {
  return String(value || '').replace(/(^|\n)([ \t]*)\[\d+\][ \t]*/g, '$1$2').trim();
}

function scriptCharacterCount(value) {
  return Array.from(
    stripScriptParagraphNumbers(value).replaceAll(PRODUCT_MARKER, '').replace(/\s+/g, ''),
  ).length;
}

function projectStage(project) {
  if (project?.workflow_type !== 'video_library') return { key: 'todo', label: '旧项目（不再支持）' };
  const labels = {
    created: '等待配音',
    voice_ready: '配音已完成',
    draft_ready: '草稿已导出',
    video_generating: '处理中',
    video_ready: '处理中',
    video_rendering: '合成视频中',
    video_done: '视频已完成',
    video_failed: '处理失败',
  };
  const done = project.has_export || ['draft_ready', 'video_done'].includes(project.status);
  return {
    key: done ? 'done' : (['video_generating', 'video_rendering'].includes(project.status) ? 'active' : 'todo'),
    label: labels[project.status] || '等待配音',
  };
}

function formatProjectTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function normalizeCoverTitlePositions(value, upgradeLegacyDefault = false) {
  const source = value && typeof value === 'object' ? value : {};
  return Object.fromEntries(Object.entries(DEFAULT_COVER_TITLE_POSITIONS).map(([key, fallback]) => {
    const line = source[key] && typeof source[key] === 'object' ? source[key] : {};
    return [key, {
      x: Math.max(0.03, Math.min(0.97, Number(line.x ?? fallback.x) || fallback.x)),
      y: Math.max(0.03, Math.min(0.97, Number(line.y ?? fallback.y) || fallback.y)),
      font_size: Math.max(32, Math.min(260, Number(upgradeLegacyDefault && [80, 124, 162].includes(Number(line.font_size)) ? fallback.font_size : (line.font_size ?? fallback.font_size)) || fallback.font_size)),
    }];
  }));
}

function CoverTitleEditor({ imageUrl, line1, line2, positions, maskOpacity, onChange }) {
  const canvasRef = useRef(null);
  const [canvasWidth, setCanvasWidth] = useState(405);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const updateWidth = () => setCanvasWidth(canvas.getBoundingClientRect().width || 405);
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  function beginInteraction(event, key, mode) {
    event.preventDefault();
    event.stopPropagation();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const start = { ...positions[key] };

    const applyInteraction = (moveEvent) => {
      const dx = moveEvent.clientX - startX;
      const dy = moveEvent.clientY - startY;
      if (mode === 'resize') {
        const delta = ((dx / rect.width) * 1080 + (dy / rect.height) * 1920) / 2;
        onChange(key, {
          ...start,
          font_size: Math.max(32, Math.min(260, Math.round(start.font_size + delta))),
        });
        return;
      }
      onChange(key, {
        ...start,
        x: Math.max(0.03, Math.min(0.97, start.x + dx / rect.width)),
        y: Math.max(0.03, Math.min(0.97, start.y + dy / rect.height)),
      });
    };
    const handleMove = (moveEvent) => applyInteraction(moveEvent);
    const cleanup = () => {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleCancel);
    };
    const handleUp = (upEvent) => {
      // Some browsers do not emit a final pointermove before pointerup. Applying
      // the release coordinates keeps the last visible size as the saved value.
      applyInteraction(upEvent);
      cleanup();
    };
    const handleCancel = () => cleanup();
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleCancel);
  }

  return (
    <div className="cover-title-editor" ref={canvasRef}>
      <img src={imageUrl} alt="封面人物图片" draggable="false" />
      <div
        className="cover-image-mask"
        style={{ opacity: Math.max(0, Math.min(100, Number(maskOpacity) || 0)) / 100 }}
        aria-hidden="true"
      />
      {[
        ['line1', line1, '第一行标题'],
        ['line2', line2, '第二行标题'],
      ].map(([key, text, label], index) => {
        const position = positions[key];
        const previewFontSize = Math.max(12, position.font_size * canvasWidth / 1080);
        return (
          <div
            key={key}
            className={`cover-title-box cover-title-box-${index + 1}`}
            style={{
              left: `${position.x * 100}%`,
              top: `${position.y * 100}%`,
              fontSize: `${previewFontSize}px`,
              WebkitTextStrokeWidth: `${Math.max(1, previewFontSize / 15.5)}px`,
            }}
            role="button"
            tabIndex="0"
            aria-label={`拖动${label}，右下角可缩放`}
            onPointerDown={(event) => beginInteraction(event, key, 'drag')}
          >
            <span>{text || label}</span>
            {index === 1 && <i aria-hidden="true" />}
            <button
              type="button"
              className="cover-title-resize-handle"
              title={`调整${label}大小`}
              aria-label={`调整${label}大小`}
              onPointerDown={(event) => beginInteraction(event, key, 'resize')}
            />
          </div>
        );
      })}
    </div>
  );
}

function App() {
  const [media, setMedia] = useState({ videos: [], products: [], templates: [] });
  const [libraryTab, setLibraryTab] = useState('normal');
  const [backgroundTemplateId, setBackgroundTemplateId] = useState('daodejing');
  async function refreshMedia() { const data = await request('/api/media'); setMedia(data); }
  useEffect(() => { refreshMedia().catch(error => setMessage(error.message)); }, []);

  const [tab, setTab] = useState('create');
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState(null);
  const [musicLibrary, setMusicLibrary] = useState([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [rawScriptDraft, setRawScriptDraft] = useState('');
  const [voiceType, setVoiceType] = useState('S_6Sd6jOE42');
  const [speechRate, setSpeechRate] = useState(0);
  const [audioSource, setAudioSource] = useState('tts');
  const [audioFile, setAudioFile] = useState(null);
  const [voicePreviewUrl, setVoicePreviewUrl] = useState('');
  const [previewingVoice, setPreviewingVoice] = useState(false);
  const [voiceVolume, setVoiceVolume] = useState(100);
  const [voiceAudioVersion, setVoiceAudioVersion] = useState(0);
  const [projectNameDraft, setProjectNameDraft] = useState('');
  const [titleLine1, setTitleLine1] = useState('');
  const [titleLine2, setTitleLine2] = useState('');
  const [titleCandidates, setTitleCandidates] = useState([]);
  const [titleConfirmed, setTitleConfirmed] = useState(false);
  const [coverImage, setCoverImage] = useState(null);
  const [coverImagePreviewUrl, setCoverImagePreviewUrl] = useState('');
  const [coverTitlePositions, setCoverTitlePositions] = useState(DEFAULT_COVER_TITLE_POSITIONS);
  const [coverMaskOpacity, setCoverMaskOpacity] = useState(0);
  const projectCoverTitlePositionsKey = JSON.stringify(project?.cover_title_positions ?? null);
  const [backgroundMusicId, setBackgroundMusicId] = useState('');
  const [backgroundMusicStart, setBackgroundMusicStart] = useState(0);
  const [backgroundMusicVolume, setBackgroundMusicVolume] = useState(20);
  const [presets, setPresets] = useState([]);
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [presetName, setPresetName] = useState('');
  const [coverPresetId, setCoverPresetId] = useState('');
  const [coverPresetImageUrl, setCoverPresetImageUrl] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [creationStatus, setCreationStatus] = useState({ active: false, label: '', error: '', failedIndex: 0 });
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [exportResult, setExportResult] = useState(null);
  const [projectSearch, setProjectSearch] = useState('');
  const [projectFilter, setProjectFilter] = useState('current');
  const [appSettings, setAppSettings] = useState({
    project_directory: '',
    jianying_drafts_directory: '',
  });
  const [selectingDirectory, setSelectingDirectory] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true',
  );
  const scriptTextareaRef = useRef(null);
  const scriptHighlightRef = useRef(null);
  const productPlacementSaveRef = useRef(null);
  const coverInputRef = useRef(null);
  const musicInputRef = useRef(null);
  const musicPreviewRef = useRef(null);
  const voicePreviewRef = useRef(null);
  const mainVoicePreviewRef = useRef(null);
  const mainVoiceAudioGraphRef = useRef({ element: null, context: null, gain: null });
  const playVoicePreviewAfterLoadRef = useRef(false);
  const lastCoverProjectIdRef = useRef('');
  const creationInFlightRef = useRef(false);
  const creationProjectIdRef = useRef('');
  const creationDraftRef = useRef(null);

  function syncScriptHighlight() {
    const mirror = scriptHighlightRef.current;
    const editor = scriptTextareaRef.current;
    if (!mirror || !editor) return;
    const styles = window.getComputedStyle(editor);
    const border = (parseFloat(styles.borderLeftWidth) || 0)
      + (parseFloat(styles.borderRightWidth) || 0);
    const scrollbar = Math.max(0, editor.offsetWidth - editor.clientWidth - border);
    mirror.parentElement?.style.setProperty('--script-scrollbar', `${scrollbar}px`);
    mirror.scrollTop = editor.scrollTop;
  }

  async function run(label, fn) {
    setBusy(true);
    setMessage(`${label}中...`);
    try {
      const result = await fn();
      setMessage(`${label}完成`);
      return result;
    } catch (err) {
      setMessage(`失败：${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function refreshAll(id = projectId) {
    const [projectList, musicData, projectData, presetData] = await Promise.all([
      request('/api/projects'),
      request('/api/music'),
      id ? request(`/api/projects/${id}`) : Promise.resolve(null),
      request('/api/video-library/presets'),
    ]);
    setProjects(projectList.projects);
    setMusicLibrary(musicData.music || []);
    setPresets(presetData.presets || []);
    if (!projectData) {
      setProject(null);
      setProjectId('');
      return;
    }
    if (projectData.project.workflow_type !== 'video_library') {
      setProject(null);
      setProjectId('');
      setTab('projects');
      setMessage('旧项目数据保留，但已停止支持，可归档或删除');
      return;
    }
    if (creationProjectIdRef.current && creationProjectIdRef.current !== id) {
      setCreationStatus({ active: false, label: '', error: '', failedIndex: 0 });
    }
    setProject(projectData.project);
    setProjectId(id);
    setTab('export');
  }

  async function refreshProject(id = projectId) {
    if (!id) return;
    const data = await request(`/api/projects/${id}`);
    setProject(data.project);
    setProjects(current => current.map(item => item.id === id ? { ...item, ...data.project, shot_count: data.shots.length } : item));
  }

  async function refreshSettings() {
    const data = await request('/api/settings');
    setAppSettings(data.settings || {});
  }

  async function selectAppDirectory(kind) {
    setSelectingDirectory(kind);
    try {
      const data = await request('/api/settings/select-directory', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind }),
      });
      if (data.cancelled) {
        setMessage('已取消选择目录');
        return;
      }
      setAppSettings(data.settings || {});
      if (kind === 'project') {
        await refreshAll('');
        await refreshMedia();
        setMessage('项目目录已切换');
      } else {
        setMessage('剪映草稿目录已保存');
      }
    } catch (err) {
      setMessage(`目录设置失败：${err.message}`);
    } finally {
      setSelectingDirectory('');
    }
  }


  useEffect(() => {
    refreshAll();
    refreshSettings().catch((err) => setMessage(`读取目录设置失败：${err.message}`));
  }, []);

  useEffect(() => {
    if (!window.FontFace || !document.fonts) return undefined;
    const coverFont = new FontFace('GongfanNufang', `url(${API}/api/projects/_fonts/cover-title)`);
    let cancelled = false;
    coverFont.load().then((loadedFont) => {
      if (!cancelled) document.fonts.add(loadedFont);
    }).catch(() => {
      // The generated cover still uses the bundled backend font if preview loading fails.
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (coverImage) {
      const objectUrl = URL.createObjectURL(coverImage);
      setCoverImagePreviewUrl(objectUrl);
      return () => URL.revokeObjectURL(objectUrl);
    }
    const projectCover = tab === 'create' ? '' : (project?.cover_source_url || '');
    const fallback = coverPresetImageUrl || projectCover;
    setCoverImagePreviewUrl(fallback ? `${API}${fallback}` : '');
    return undefined;
  }, [coverImage, coverPresetImageUrl, project?.cover_source_url, tab]);

  useEffect(() => {
    syncScriptHighlight();
  }, [rawScriptDraft, sidebarCollapsed, tab]);

  useEffect(() => {
    window.addEventListener('resize', syncScriptHighlight);
    return () => window.removeEventListener('resize', syncScriptHighlight);
  }, []);

  useEffect(() => {
    if (!project) return;
    if (lastCoverProjectIdRef.current !== (project.id || '')) {
      lastCoverProjectIdRef.current = project.id || '';
      setCoverPresetId('');
      setCoverPresetImageUrl('');
    }
    setProjectNameDraft(project.name || '');
    setTitleLine1(project.title_line1 || '');
    setTitleLine2(project.title_line2 || '');
    setTitleCandidates(project.title_candidates || []);
    setTitleConfirmed(Boolean(project.title_line1 && project.title_line2) || Boolean(project.cover_url));
    setCoverImage(null);
    setCoverTitlePositions(normalizeCoverTitlePositions(project.cover_title_positions, true));
    setCoverMaskOpacity(Math.round(Number(project.cover_mask_opacity ?? 0) * 100));
    setBackgroundMusicId(project.background_music_id || '');
    setBackgroundMusicStart(Number(project.background_music_start_sec || 0));
    setBackgroundMusicVolume(Math.round(Number(project.background_music_volume ?? 0.2) * 100));
    setVoiceVolume(Math.round(Number(project.voice_volume ?? 1) * 100));
    setAudioSource(project.audio_source === 'upload' ? 'upload' : 'tts');
    if (project.voice_type) setVoiceType(project.voice_type);
    if (project.speech_rate !== undefined && project.speech_rate !== null) setSpeechRate(Number(project.speech_rate));
  }, [
    project?.id,
    project?.name,
    project?.title_line1,
    project?.title_line2,
    project?.cover_url,
    project?.cover_source_url,
    projectCoverTitlePositionsKey,
    project?.cover_mask_opacity,
    project?.background_music_id,
    project?.background_music_start_sec,
    project?.background_music_volume,
    project?.voice_volume,
    project?.audio_source,
    project?.voice_type,
    project?.speech_rate,
  ]);

  useEffect(() => {
    const player = musicPreviewRef.current;
    if (!player) return;
    player.volume = Math.max(0, Math.min(1, backgroundMusicVolume / 100));
  }, [backgroundMusicVolume, backgroundMusicId]);

  useEffect(() => {
    const player = mainVoicePreviewRef.current;
    const graph = mainVoiceAudioGraphRef.current;
    if (!player) return;
    if (graph.element === player && graph.gain && graph.context) {
      graph.gain.gain.setValueAtTime(voiceVolume / 100, graph.context.currentTime);
      player.volume = 1;
    } else {
      player.volume = Math.max(0, Math.min(1, voiceVolume / 100));
    }
  }, [voiceVolume, project?.audio_url, tab]);

  useEffect(() => () => {
    const context = mainVoiceAudioGraphRef.current.context;
    if (context && context.state !== 'closed') context.close().catch(() => {});
  }, []);

  useEffect(() => {
    const player = musicPreviewRef.current;
    if (!player || !Number.isFinite(player.duration)) return;
    player.currentTime = Math.min(
      backgroundMusicStart,
      Math.max(player.duration - 0.1, 0),
    );
  }, [backgroundMusicStart, backgroundMusicId]);

  useEffect(() => {
    if (!voicePreviewUrl || !playVoicePreviewAfterLoadRef.current) return;
    playVoicePreviewAfterLoadRef.current = false;
    const player = voicePreviewRef.current;
    if (!player) return;
    player.currentTime = 0;
    player.play().catch(() => {
      setMessage('试听已生成，请点击播放器开始播放');
    });
  }, [voicePreviewUrl]);

  async function generateVideoVoice(identifier, source, file) {
    if (!identifier) return false;
    if (source === 'upload') {
      if (!file) { setMessage('请选择要上传的音频文件'); return false; }
      const body = new FormData();
      body.append('file', file);
      const uploaded = await run('上传音频', () => request(`/api/projects/${identifier}/audio`, { method: 'POST', body }));
      if (!uploaded) return false;
    }
    const voice = await run('生成配音', () => request(`/api/projects/${identifier}/generate-voice`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_type: voiceType, speech_rate: speechRate, audio_source: source }),
    }));
    if (!voice) return false;
    const subtitles = await run('生成字幕', () => request(`/api/projects/${identifier}/generate-subtitles`, { method: 'POST' }));
    if (!subtitles) return false;
    setVoiceAudioVersion(Date.now());
    await refreshProject(identifier);
    setMessage('配音与字幕已生成，可以继续调整音量');
    return true;
  }

  async function applyCreationSettings(identifier, startIndex = 0) {
    const draft = creationDraftRef.current || {
      audioSource, audioFile, titleLine1, titleLine2,
      backgroundMusicId, backgroundMusicStart, backgroundMusicVolume,
      coverImage, coverPresetId, coverTitlePositions, coverMaskOpacity,
      voiceType, speechRate, voiceVolume,
    };
    const steps = [];
    if (draft.audioSource === 'upload') {
      steps.push({
        label: '上传音频',
        run: async () => {
          const body = new FormData();
          body.append('file', draft.audioFile);
          await request(`/api/projects/${identifier}/audio`, { method: 'POST', body });
        },
      });
    }
    steps.push({
      label: '保存标题',
      run: () => request(`/api/projects/${identifier}/script`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title_line1: draft.titleLine1.trim(), title_line2: draft.titleLine2.trim() }),
      }),
    });
    if (draft.backgroundMusicId) {
      steps.push({
        label: '保存背景音乐',
        run: () => request(`/api/projects/${identifier}/music-settings`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            music_id: draft.backgroundMusicId,
            start_sec: Number(draft.backgroundMusicStart) || 0,
            volume: Math.max(0, Math.min(100, Number(draft.backgroundMusicVolume) || 0)) / 100,
          }),
        }),
      });
    }
    if (draft.coverImage || draft.coverPresetId) {
      steps.push({
        label: '生成封面',
        warningOnly: true,
        run: async () => {
          const body = new FormData();
          if (draft.coverImage) body.append('file', draft.coverImage, draft.coverImage.name);
          else body.append('preset_id', draft.coverPresetId);
          body.append('title_positions', JSON.stringify(draft.coverTitlePositions));
          body.append('mask_opacity', String(draft.coverMaskOpacity / 100));
          await request(`/api/projects/${identifier}/generate-cover`, { method: 'POST', body });
        },
      });
    }
    steps.push({
      label: '生成配音与字幕',
      run: () => request(`/api/projects/${identifier}/generate-voice`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voice_type: draft.voiceType, speech_rate: draft.speechRate, audio_source: draft.audioSource,
        }),
      }),
    });
    steps.push({
      label: '保存配音音量',
      run: () => request(`/api/projects/${identifier}/voice-settings`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ volume: Math.max(0, Math.min(300, Number(draft.voiceVolume) || 0)) / 100 }),
      }),
    });

    const warnings = [];
    for (let index = startIndex; index < steps.length; index += 1) {
      const step = steps[index];
      setCreationStatus({ active: true, label: `正在${step.label}…`, error: '', failedIndex: index });
      try {
        await step.run();
      } catch (error) {
        if (step.warningOnly) {
          warnings.push(`${step.label}：${error.message}`);
          continue;
        }
        setCreationStatus({ active: false, label: '', error: `${step.label}失败：${error.message}`, failedIndex: index });
        await refreshAll(identifier);
        setTab('export');
        setSettingsOpen(true);
        setMessage(`${step.label}失败：${error.message}`);
        return false;
      }
    }
    setCreationStatus({ active: false, label: '', error: '', failedIndex: 0 });
    setVoiceAudioVersion(Date.now());
    await refreshAll(identifier);
    setTab('export');
    setSettingsOpen(false);
    setMessage(warnings.length ? `项目已创建，但有提示：${warnings.join('；')}` : '项目创建完成，可以导出剪映草稿');
    return true;
  }

  async function createProject(ev) {
    ev.preventDefault();
    if (busy || creationInFlightRef.current) return;
    if (!rawScriptDraft.trim()) { setMessage('请先输入文案'); return; }
    if (!rawScriptDraft.includes(PRODUCT_MARKER)) {
      setMessage(`文案中缺少${PRODUCT_MARKER}标记，请把光标放到需要开始展示带货画面的位置后点击插入`);
      return;
    }
    if (!media.videos.length) { setMessage('请先到素材库上传视频'); return; }
    if (!titleLine1.trim() || !titleLine2.trim()) { setMessage('请先填写两行标题'); return; }
    if (audioSource === 'upload' && !audioFile) { setMessage('请选择要上传的音频文件'); return; }
    creationDraftRef.current = {
      audioSource, audioFile, titleLine1, titleLine2,
      backgroundMusicId, backgroundMusicStart, backgroundMusicVolume,
      coverImage, coverPresetId, coverTitlePositions, coverMaskOpacity,
      voiceType, speechRate, voiceVolume,
    };
    creationInFlightRef.current = true;
    creationProjectIdRef.current = '';
    setBusy(true);
    setCreationStatus({ active: true, label: '正在创建项目…', error: '', failedIndex: 0 });
    try {
      const data = await request('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_script: rawScriptDraft.trim(), workflow_type: 'video_library',
          background_template_id: backgroundTemplateId, voice_type: voiceType, speech_rate: speechRate, video_ratio: '9:16' }),
      });
      if (!data?.project_id) throw new Error('服务器未返回项目 ID');
      creationProjectIdRef.current = data.project_id;
      const created = await applyCreationSettings(data.project_id, 0);
      if (created) {
        creationDraftRef.current = null;
        setRawScriptDraft('');
        setAudioFile(null);
        resetPerVideoDraft();
      }
    } catch (error) {
      setCreationStatus({ active: false, label: '', error: `创建项目失败：${error.message}`, failedIndex: 0 });
      setMessage(`创建项目失败：${error.message}`);
    } finally {
      creationInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function retryCreation() {
    const identifier = creationProjectIdRef.current;
    if (!identifier || busy || creationInFlightRef.current) return;
    creationInFlightRef.current = true;
    setBusy(true);
    try {
      await applyCreationSettings(identifier, creationStatus.failedIndex || 0);
    } finally {
      creationInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function saveProjectName() {
    if (!projectId) return;
    const name = projectNameDraft.trim();
    if (!name) {
      setMessage('项目名称不能为空');
      return;
    }
    if (name === (project?.name || '').trim()) return;
    const result = await run('保存项目名称', () => request(`/api/projects/${projectId}/script`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }));
    if (!result) return;
    await refreshAll(projectId);
  }

  async function deleteProject() {
    if (!projectId || !project) return;
    if (!window.confirm(`确认删除项目「${project.name}」吗？项目文案、素材、生成文件和导出包都会删除。`)) return;
    const deletedId = projectId;
    const result = await run('删除项目', () => request(`/api/projects/${deletedId}`, { method: 'DELETE' }));
    if (!result) return;
    setTab('create');
    await refreshAll('');
  }

  async function setProjectArchived(item, archived) {
    const result = await run(archived ? '归档项目' : '恢复项目', () => request(`/api/projects/${item.id}/script`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archived }),
    }));
    if (!result) return;
    if (archived && item.id === projectId) {
      setTab('create');
      await refreshAll('');
    } else {
      await refreshAll(projectId);
    }
  }

  async function deleteProjectItem(item) {
    if (!window.confirm(`确认删除项目「${item.name}」吗？项目文案、素材、生成文件和导出包都会删除。`)) return;
    const result = await run('删除项目', () => request(`/api/projects/${item.id}`, { method: 'DELETE' }));
    if (!result) return;
    if (item.id === projectId) {
      setTab('create');
      await refreshAll('');
    } else {
      await refreshAll(projectId);
    }
  }

  function insertProductMarker() {
    if (rawScriptDraft.includes(PRODUCT_MARKER)) {
      setMessage(`文案中已有${PRODUCT_MARKER}标记，不能重复插入`);
      return;
    }
    const textarea = scriptTextareaRef.current;
    const start = textarea?.selectionStart ?? rawScriptDraft.length;
    const end = textarea?.selectionEnd ?? start;
    const next = rawScriptDraft.slice(0, start) + PRODUCT_MARKER + rawScriptDraft.slice(end);
    setRawScriptDraft(next);
    window.requestAnimationFrame(() => {
      textarea?.focus();
      const cursor = start + PRODUCT_MARKER.length;
      textarea?.setSelectionRange(cursor, cursor);
    });
  }

  async function copyProjectScript() {
    const text = project?.raw_script || '';
    if (!text) {
      setMessage('当前项目没有保存文案');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setMessage('项目文案已复制');
    } catch {
      setMessage('复制失败，请手动选择文案');
    }
  }

  async function deleteProjects(ids) {
    const unique = [...new Set(ids.filter(Boolean))];
    if (!unique.length) return false;
    if (!window.confirm(`确认删除选中的 ${unique.length} 个项目吗？项目文案、素材、生成文件和导出包都会删除。`)) {
      return false;
    }
    const result = await run('批量删除项目', () => request('/api/projects/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: unique }),
    }));
    if (!result) return false;
    if (unique.includes(projectId)) {
      setTab('create');
      await refreshAll('');
    } else {
      await refreshAll(projectId);
    }
    const deleted = result.deleted_ids?.length || 0;
    const skipped = result.skipped?.length || 0;
    setMessage(skipped
      ? `已删除 ${deleted} 个项目，${skipped} 个未删除（正在处理或已不存在）`
      : `已删除 ${deleted} 个项目`);
    return true;
  }

  async function previewVoice({ draft = false } = {}) {
    playVoicePreviewAfterLoadRef.current = true;
    setPreviewingVoice(true);
    setMessage('正在生成音色试听...');
    if (draft && !rawScriptDraft.trim()) {
      setPreviewingVoice(false);
      playVoicePreviewAfterLoadRef.current = false;
      setMessage('请先输入文案');
      return;
    }
    try {
      const response = await fetch(
        draft ? `${API}/api/video-library/voice-preview` : `${API}/api/projects/${projectId}/voice-preview`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(draft
            ? { script: rawScriptDraft, voice_type: voiceType, speech_rate: speechRate }
            : { voice_type: voiceType, speech_rate: speechRate }),
        },
      );
      if (!response.ok) throw new Error(await response.text());
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      setVoicePreviewUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return url;
      });
      setMessage('试听已生成，内容为当前文案第一句');
    } catch (err) {
      playVoicePreviewAfterLoadRef.current = false;
      setMessage(`试听失败：${err.message}`);
    } finally {
      setPreviewingVoice(false);
    }
  }

  async function generateCover(options = {}) {
    if (!coverImage && !coverPresetId && !project?.cover_source_url) {
      setMessage('请先上传一张人物图片或选择预设封面');
      return;
    }
    if (!titleLine1.trim() || !titleLine2.trim()) {
      setMessage('请填写两行标题');
      return;
    }
    if (!titleConfirmed) {
      const saved = await confirmTitle({ silent: true });
      if (!saved) return;
    }
    const data = new FormData();
    if (coverImage) data.append('file', coverImage, coverImage.name);
    else if (coverPresetId) data.append('preset_id', coverPresetId);
    data.append('title_positions', JSON.stringify(coverTitlePositions));
    data.append('mask_opacity', String(coverMaskOpacity / 100));
    const result = await run('生成视频封面', () => request(`/api/projects/${projectId}/generate-cover`, {
      method: 'POST',
      body: data,
    }));
    if (!result) return;
    setCoverPresetId('');
    setCoverPresetImageUrl('');
    await refreshAll(projectId);
    if (!options.silent) setMessage('9:16 视频封面生成完成');
    return true;
  }

  function updateCoverTitlePosition(key, value) {
    setCoverTitlePositions((current) => normalizeCoverTitlePositions({
      ...current,
      [key]: value,
    }));
  }

  async function downloadCover() {
    if (!project?.cover_url) return;
    const updated = await generateCover({ silent: true });
    if (!updated) return;
    const link = document.createElement('a');
    link.href = `${API}/api/projects/${projectId}/download-cover?v=${Date.now()}`;
    link.download = '视频封面.png';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setMessage('已应用当前标题位置、大小和遮罩透明度，封面下载已开始');
  }

  function updateVoiceVolume(value) {
    const nextVolume = Math.max(0, Math.min(MAX_VOICE_VOLUME_PERCENT, Number(value) || 0));
    setVoiceVolume(nextVolume);
    const player = mainVoicePreviewRef.current;
    const graph = mainVoiceAudioGraphRef.current;
    if (graph.element === player && graph.gain && graph.context) {
      graph.gain.gain.setValueAtTime(nextVolume / 100, graph.context.currentTime);
    } else if (player) {
      player.volume = Math.min(1, nextVolume / 100);
    }
  }

  function activateMainVoicePreview(player) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      player.volume = Math.min(1, voiceVolume / 100);
      return;
    }
    let graph = mainVoiceAudioGraphRef.current;
    if (graph.element !== player || !graph.gain || !graph.context || graph.context.state === 'closed') {
      if (graph.context && graph.context.state !== 'closed') graph.context.close().catch(() => {});
      try {
        const context = new AudioContextClass();
        const source = context.createMediaElementSource(player);
        const gain = context.createGain();
        source.connect(gain);
        gain.connect(context.destination);
        graph = { element: player, context, gain };
        mainVoiceAudioGraphRef.current = graph;
      } catch {
        player.volume = Math.min(1, voiceVolume / 100);
        return;
      }
    }
    player.volume = 1;
    graph.gain.gain.setValueAtTime(voiceVolume / 100, graph.context.currentTime);
    if (graph.context.state === 'suspended') graph.context.resume().catch(() => {});
  }

  async function saveVoiceVolume() {
    const result = await run('保存配音音量', () => request(
      `/api/projects/${projectId}/voice-settings`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ volume: voiceVolume / 100 }),
      },
    ));
    if (result) await refreshProject(projectId);
  }

  async function saveMusicSettings(overrides = {}) {
    if (!projectId) return;
    const musicId = overrides.musicId ?? backgroundMusicId;
    const startSec = overrides.startSec ?? backgroundMusicStart;
    const volumePercent = overrides.volumePercent ?? backgroundMusicVolume;
    const result = await run('保存配乐设置', () => request(
      `/api/projects/${projectId}/music-settings`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          music_id: musicId || null,
          start_sec: Number(startSec) || 0,
          volume: Math.max(0, Math.min(100, Number(volumePercent) || 0)) / 100,
        }),
      },
    ));
    if (result) await refreshProject(projectId);
  }

  async function uploadBackgroundMusic(file, { persist = Boolean(projectId) } = {}) {
    if (!file) return;
    const data = new FormData();
    data.append('file', file, file.name);
    const result = await run('上传背景音乐', () => request('/api/music', {
      method: 'POST',
      body: data,
    }));
    if (!result?.music) return;
    const musicData = await request('/api/music');
    setMusicLibrary(musicData.music || []);
    setBackgroundMusicId(result.music.id);
    setBackgroundMusicStart(0);
    if (persist) {
      await saveMusicSettings({
        musicId: result.music.id,
        startSec: 0,
        volumePercent: backgroundMusicVolume,
      });
    }
  }

  async function deleteBackgroundMusic() {
    if (!backgroundMusicId) return;
    const selectedMusic = musicLibrary.find((music) => music.id === backgroundMusicId);
    if (!selectedMusic) return;
    if (!window.confirm(`确认删除音乐「${selectedMusic.name}」吗？正在使用它的项目会清空配乐设置。`)) return;
    const result = await run('删除背景音乐', () => request(`/api/music/${backgroundMusicId}`, {
      method: 'DELETE',
    }));
    if (!result) return;
    const musicData = await request('/api/music');
    setMusicLibrary(musicData.music || []);
    setBackgroundMusicId('');
    setBackgroundMusicStart(0);
    await refreshProject(projectId);
    await refreshPresets();
  }

  async function refreshPresets() {
    const data = await request('/api/video-library/presets');
    setPresets(data.presets || []);
  }

  function applyPreset(preset) {
    if (!preset) return;
    setSelectedPresetId(preset.id || '');
    setPresetName(preset.name || '');
    if (preset.voice_type) setVoiceType(preset.voice_type);
    setSpeechRate(Number(preset.speech_rate || 0));
    setVoiceVolume(Math.round(Number(preset.voice_volume ?? 1) * 100));
    if (preset.background_template_id) setBackgroundTemplateId(preset.background_template_id);
    const music = musicLibrary.find((item) => item.id === preset.background_music_id);
    setBackgroundMusicId(music ? music.id : '');
    setBackgroundMusicStart(Number(preset.background_music_start_sec || 0));
    setBackgroundMusicVolume(Math.round(Number(preset.background_music_volume ?? 0.2) * 100));
    setCoverTitlePositions(normalizeCoverTitlePositions(preset.cover_title_positions, true));
    setCoverMaskOpacity(Math.round(Number(preset.cover_mask_opacity ?? 0) * 100));
    setCoverImage(null);
    setCoverPresetId(preset.cover_available ? preset.id : '');
    setCoverPresetImageUrl(preset.cover_available ? preset.cover_source_url : '');
    setMessage(`已应用预设「${preset.name}」`);
  }

  async function savePreset({ overwrite = false } = {}) {
    const name = presetName.trim();
    if (!name) { setMessage('请填写预设名称'); return; }
    const data = new FormData();
    data.append('name', name);
    if (overwrite && selectedPresetId) data.append('preset_id', selectedPresetId);
    data.append('voice_type', voiceType);
    data.append('speech_rate', String(speechRate));
    data.append('voice_volume', String(voiceVolume / 100));
    data.append('background_template_id', backgroundTemplateId);
    data.append('background_music_id', backgroundMusicId || '');
    data.append('background_music_start_sec', String(backgroundMusicStart));
    data.append('background_music_volume', String(backgroundMusicVolume / 100));
    data.append('cover_title_positions', JSON.stringify(coverTitlePositions));
    data.append('cover_mask_opacity', String(coverMaskOpacity / 100));
    if (coverImage) data.append('file', coverImage, coverImage.name);
    else if (coverPresetId) data.append('cover_source_preset_id', coverPresetId);
    const result = await run('保存预设', () => request('/api/video-library/presets', {
      method: 'POST',
      body: data,
    }));
    if (!result?.preset) return;
    const saved = result.preset;
    setPresets((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
    setSelectedPresetId(saved.id);
    setPresetName(saved.name);
    setCoverImage(null);
    setCoverPresetId(saved.cover_available ? saved.id : '');
    setCoverPresetImageUrl(saved.cover_available ? saved.cover_source_url : '');
    setMessage(`预设「${saved.name}」已保存`);
  }

  async function deletePreset() {
    if (!selectedPresetId) return;
    const preset = presets.find((item) => item.id === selectedPresetId);
    if (!window.confirm(`删除预设「${preset?.name || ''}」？已创建的项目不受影响。`)) return;
    const result = await run('删除预设', () => request(
      `/api/video-library/presets/${selectedPresetId}`,
      { method: 'DELETE' },
    ));
    if (!result) return;
    setPresets((current) => current.filter((item) => item.id !== selectedPresetId));
    setSelectedPresetId('');
    setPresetName('');
    setCoverPresetId('');
    setCoverPresetImageUrl('');
    setMessage('预设已删除');
  }

  async function previewTitleCandidates() {
    const script = rawScriptDraft.trim();
    if (!script) { setMessage('请先输入文案'); return; }
    const result = await run('生成标题候选', () => request('/api/video-library/title-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script }),
    }));
    if (!result) return;
    setTitleCandidates(result.candidates || []);
    setMessage(`已生成 ${result.candidates?.length || 0} 组标题候选，点击选择后可继续编辑`);
  }

  function resetPerVideoDraft() {
    creationDraftRef.current = null;
    setTitleLine1('');
    setTitleLine2('');
    setTitleCandidates([]);
    setTitleConfirmed(false);
    setCoverImage(null);
    setCoverPresetId('');
    setCoverPresetImageUrl('');
    setCreationStatus({ active: false, label: '', error: '', failedIndex: 0 });
  }

  function startNewVideo() {
    if (busy || creationInFlightRef.current) return;
    creationDraftRef.current = null;
    creationProjectIdRef.current = '';
    setProjectId('');
    setProject(null);
    setProjectNameDraft('');
    setProjectMenuOpen(false);
    setRawScriptDraft('');
    setTitleLine1('');
    setTitleLine2('');
    setTitleCandidates([]);
    setTitleConfirmed(false);
    setCoverImage(null);
    setCoverImagePreviewUrl('');
    setCoverTitlePositions(DEFAULT_COVER_TITLE_POSITIONS);
    setCoverMaskOpacity(0);
    setBackgroundTemplateId('daodejing');
    setVoiceType('S_6Sd6jOE42');
    setSpeechRate(0);
    setAudioSource('tts');
    setAudioFile(null);
    setVoicePreviewUrl('');
    setPreviewingVoice(false);
    setVoiceVolume(100);
    setBackgroundMusicId('');
    setBackgroundMusicStart(0);
    setBackgroundMusicVolume(20);
    setSelectedPresetId('');
    setPresetName('');
    setCoverPresetId('');
    setCoverPresetImageUrl('');
    setCreationStatus({ active: false, label: '', error: '', failedIndex: 0 });
    setExportResult(null);
    setSettingsOpen(false);
    setMessage('');
    setTab('create');
  }

  function updateMusicPreviewStart(value) {
    const nextStart = Math.max(0, Number(value) || 0);
    setBackgroundMusicStart(nextStart);
    const player = musicPreviewRef.current;
    if (player?.duration) {
      player.currentTime = Math.min(nextStart, Math.max(player.duration - 0.1, 0));
    }
  }

  function updateMusicPreviewVolume(value) {
    const nextVolume = Math.max(0, Math.min(100, Number(value) || 0));
    setBackgroundMusicVolume(nextVolume);
    if (musicPreviewRef.current) {
      musicPreviewRef.current.volume = nextVolume / 100;
    }
  }

  async function playMusicPreview() {
    const player = musicPreviewRef.current;
    if (!player) return;
    player.currentTime = Math.min(
      backgroundMusicStart,
      Math.max((player.duration || backgroundMusicStart) - 0.1, 0),
    );
    player.volume = backgroundMusicVolume / 100;
    try {
      await player.play();
    } catch {
      setMessage('浏览器阻止了自动播放，请点击播放器的播放按钮');
    }
  }

  async function generateTitle() {
    const result = await run('生成爆款标题', () => request(`/api/projects/${projectId}/generate-title`, {
      method: 'POST',
    }));
    if (!result) return;
    setTitleCandidates(result.candidates || []);
    setTitleConfirmed(false);
    await refreshAll(projectId);
    setMessage(`已生成 ${result.candidates?.length || 0} 组标题，请选择或手动编辑`);
  }

  async function confirmTitle(options = {}) {
    if (!titleLine1.trim() || !titleLine2.trim()) {
      setMessage('请填写两行标题');
      return false;
    }
    const result = await run('保存标题', () => request(`/api/projects/${projectId}/script`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title_line1: titleLine1.trim(),
        title_line2: titleLine2.trim(),
      }),
    }));
    if (!result) return false;
    setTitleConfirmed(true);
    if (projectId) await refreshProject(projectId);
    if (!options.silent) setMessage('标题已确认，可以生成封面');
    return true;
  }

  async function exportPackage() {
    if (!project?.audio_url) { setMessage('请先点击“生成配音与字幕”'); return; }
    const data = await run('导出剪映草稿', async () => {
      const panel = productPlacementSaveRef.current;
      if (panel?.projectId === projectId && !await panel.save()) return null;
      return request(
        `/api/projects/${projectId}/export/package`,
        { method: 'POST' },
      );
    });
    if (!data) return;
    setExportResult(data);
    await refreshProject(projectId);
    setMessage(`剪映草稿导出完成：${data.verification?.jianying?.draft_name || ''}`);
  }

  async function openExportFolder() {
    const data = await run('打开导出文件夹', () => request(
      `/api/projects/${projectId}/export/open-folder`,
      { method: 'POST' },
    ));
    if (data?.path) setMessage(`已打开导出文件夹：${data.path}`);
  }

  async function openDraftFolder() {
    const data = await run('打开剪映草稿文件夹', () => request(
      `/api/projects/${projectId}/export/open-draft-folder`,
      { method: 'POST' },
    ));
    if (data?.path) setMessage(`已打开剪映草稿文件夹：${data.path}`);
  }

  const workflowBusy = busy;
  const workflowMessage = creationStatus.active ? (creationStatus.label || '处理中…') : (message || '就绪');

  function toggleSidebar() {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next));
      if (next) setProjectMenuOpen(false);
      return next;
    });
  }

  function renderPresetPanel() {
    return (
      <div className="preset-panel">
        <label>
          预设
          <select
            value={selectedPresetId}
            onChange={(event) => {
              const preset = presets.find((item) => item.id === event.target.value);
              if (preset) applyPreset(preset);
              else {
                setSelectedPresetId('');
                setPresetName('');
                setCoverPresetId('');
                setCoverPresetImageUrl('');
              }
            }}
          >
            <option value="">不使用预设</option>
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>{preset.name}</option>
            ))}
          </select>
        </label>
        <label>
          预设名称
          <input value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="例如：沉稳男声·墨绿" maxLength={60} />
        </label>
        <div className="preset-actions">
          <button type="button" disabled={busy || !presetName.trim()} onClick={() => savePreset()}>保存为新预设</button>
          <button type="button" disabled={busy || !selectedPresetId} onClick={() => savePreset({ overwrite: true })}>覆盖保存</button>
          <button type="button" className="danger" disabled={busy || !selectedPresetId} onClick={deletePreset}>删除预设</button>
        </div>
        <small className="preset-hint">预设保存配音、模板、音乐和封面；标题文字不保存。</small>
      </div>
    );
  }

  function renderTitleSettings(mode) {
    const isCreate = mode === 'create';
    return (
      <div className="title-section">
        <h3>{isCreate ? '两行标题' : '标题'}</h3>
        <div className="title-inputs">
          <label>
            标题第一行
            <input
              value={titleLine1}
              onChange={(event) => { setTitleLine1(event.target.value); setTitleConfirmed(false); }}
              placeholder="标题第一行，完整表达"
            />
          </label>
          <label>
            标题第二行
            <input
              value={titleLine2}
              onChange={(event) => { setTitleLine2(event.target.value); setTitleConfirmed(false); }}
              placeholder="标题第二行，完整表达"
            />
          </label>
        </div>
        {titleCandidates.length > 0 && (
          <div className="title-candidates">
            <div className="title-candidates-heading">
              <strong>AI 标题候选</strong>
              <small>系统不评分，点击选择后可继续编辑</small>
            </div>
            <div className="title-candidate-grid">
              {titleCandidates.map((candidate, index) => {
                const selected = titleLine1 === candidate.line1 && titleLine2 === candidate.line2;
                return (
                  <button
                    type="button"
                    className={selected ? 'title-candidate selected' : 'title-candidate'}
                    key={`${candidate.line1}-${candidate.line2}-${index}`}
                    onClick={() => {
                      setTitleLine1(candidate.line1 || '');
                      setTitleLine2(candidate.line2 || '');
                      setTitleConfirmed(false);
                      setMessage(`已选择候选 ${index + 1}，可以编辑后确认`);
                    }}
                  >
                    <span className="title-candidate-index">{index + 1}</span>
                    <span className="title-candidate-copy">
                      <b>{candidate.line1}</b>
                      <b>{candidate.line2}</b>
                    </span>
                    {candidate.style && <small>{candidate.style}</small>}
                    {candidate.evidence_quote && (
                      <small className="title-candidate-evidence">依据：{candidate.evidence_quote}</small>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <div className="actions">
          <button className="primary" disabled={busy} onClick={isCreate ? previewTitleCandidates : generateTitle}>
            <Wand2 size={18} /> {titleLine1 ? '重新生成标题' : 'AI 生成爆款标题'}
          </button>
          {isCreate ? (
            <small>创建项目时会自动保存这两行标题。</small>
          ) : (
            <button disabled={busy || !titleLine1.trim() || !titleLine2.trim()} onClick={confirmTitle}>
              <Save size={18} /> {titleConfirmed ? '标题已确认 ✓' : '确认标题'}
            </button>
          )}
        </div>
      </div>
    );
  }

  function renderVoiceSettings(mode) {
    const isCreate = mode === 'create';
    return (
      <div className="voice-volume-section">
        <h3>{isCreate ? '配音' : '配音与音量'}</h3>
        <div className="audio-source-choice" role="group" aria-label="配音来源">
          <button type="button" aria-pressed={audioSource === 'tts'} className={audioSource === 'tts' ? 'selected' : ''} onClick={() => setAudioSource('tts')}>AI 生成配音</button>
          <button type="button" aria-pressed={audioSource === 'upload'} className={audioSource === 'upload' ? 'selected' : ''} onClick={() => setAudioSource('upload')}>上传音频</button>
        </div>
        {audioSource === 'tts' ? (
          <div className="video-create-options">
            <VoiceSelect value={voiceType} onChange={setVoiceType} />
            <SpeechRateSelect value={speechRate} onChange={setSpeechRate} />
            <button type="button" onClick={() => previewVoice({ draft: isCreate })} disabled={previewingVoice}>
              <Mic size={18} /> {previewingVoice ? '试听生成中…' : '试听音色'}
            </button>
            {voicePreviewUrl && <audio ref={voicePreviewRef} className="voice-preview-player" controls src={voicePreviewUrl} />}
          </div>
        ) : (
          <div className="video-create-options">
            <label className="media-upload audio-upload">{audioFile ? audioFile.name : '选择音频文件'}<input aria-label="上传配音音频" type="file" accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,.wma,audio/*" onChange={(event) => { setAudioFile(event.target.files[0] || null); event.target.value = ''; }} /></label>
            <small>支持 MP3、WAV、M4A、AAC、OGG、FLAC、WMA；字幕按文案长度均分配音时长生成。</small>
          </div>
        )}
        {!isCreate && (
          <div className="actions">
            <button
              className="primary"
              disabled={busy || (audioSource === 'upload' && !audioFile)}
              onClick={async () => {
                await generateVideoVoice(projectId, audioSource, audioFile);
                setAudioFile(null);
              }}
            >
              <Mic size={18} /> {project?.audio_url ? '重新生成配音与字幕' : '生成配音与字幕'}
            </button>
            {project?.subtitle_url && <a className="media-upload" href={`${API}${project.subtitle_url}`} download>下载字幕</a>}
          </div>
        )}
        <div className="voice-volume-inline">
          <label>
            配音音量：{voiceVolume}%
            <input
              type="range"
              min="0"
              max={MAX_VOICE_VOLUME_PERCENT}
              step="1"
              value={voiceVolume}
              onChange={(event) => updateVoiceVolume(event.target.value)}
            />
          </label>
          {!isCreate && (
            <button type="button" className="primary" disabled={busy} onClick={saveVoiceVolume}>
              <Save size={18} /> 保存配音音量
            </button>
          )}
          <small>0%–300%，导出剪映草稿时使用相同的音量增益。</small>
        </div>
      </div>
    );
  }

  function renderMusicSettings(mode) {
    const isCreate = mode === 'create';
    const selectedMusic = musicLibrary.find((music) => music.id === backgroundMusicId);
    const maxStart = selectedMusic ? Math.max(0, Number(selectedMusic.duration_sec || 0) - 0.1) : 0;
    return (
      <div className="music-section">
        <h3>背景音乐</h3>
        <input
          ref={musicInputRef}
          className="hidden-input"
          type="file"
          accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,audio/*"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) uploadBackgroundMusic(file, { persist: !isCreate });
            event.target.value = '';
          }}
        />
        <div className="music-library-field">
          <span>音乐库</span>
          <div className="music-library-row">
            <div className="music-chips" role="radiogroup" aria-label="背景音乐">
              <button
                type="button"
                role="radio"
                aria-checked={!backgroundMusicId}
                className={!backgroundMusicId ? 'music-chip selected' : 'music-chip'}
                onClick={() => setBackgroundMusicId('')}
              >
                <span>无音乐</span>
              </button>
              {musicLibrary.map((music) => (
                <button
                  type="button"
                  role="radio"
                  aria-checked={backgroundMusicId === music.id}
                  className={backgroundMusicId === music.id ? 'music-chip selected' : 'music-chip'}
                  key={music.id}
                  title={`${music.name} · ${Math.round(Number(music.duration_sec || 0))}秒`}
                  onClick={() => {
                    setBackgroundMusicId(music.id);
                    setBackgroundMusicStart(0);
                  }}
                >
                  <span>{music.name}</span>
                  <small>{Math.round(Number(music.duration_sec || 0))}秒</small>
                </button>
              ))}
            </div>
            <button
              type="button"
              className="danger icon-button"
              title="删除当前选中的音乐"
              aria-label="删除当前选中的音乐"
              disabled={!backgroundMusicId || busy}
              onClick={deleteBackgroundMusic}
            >
              <Trash2 size={16} />
            </button>
          </div>
        </div>
        <button type="button" onClick={() => musicInputRef.current?.click()}>
          <Music size={18} /> 上传音乐并加入音乐库
        </button>
        {selectedMusic && (
          <>
            <audio
              ref={musicPreviewRef}
              controls
              src={`${API}${selectedMusic.file_url}`}
              onLoadedMetadata={(event) => {
                try {
                  event.currentTarget.currentTime = Math.min(backgroundMusicStart, event.currentTarget.duration || 0);
                  event.currentTarget.volume = backgroundMusicVolume / 100;
                } catch {
                  // Browsers may reject seeking before metadata is ready.
                }
              }}
              onPlay={(event) => {
                event.currentTarget.volume = backgroundMusicVolume / 100;
                if (event.currentTarget.currentTime < backgroundMusicStart) {
                  event.currentTarget.currentTime = backgroundMusicStart;
                }
              }}
            />
            <button type="button" onClick={playMusicPreview}>
              <Music size={18} /> 从设置位置试听
            </button>
            <label>
              音乐起始位置：{Number(backgroundMusicStart).toFixed(1)} 秒
              <input
                type="range"
                min="0"
                max={maxStart}
                step="0.1"
                value={Math.min(backgroundMusicStart, maxStart)}
                onChange={(event) => updateMusicPreviewStart(event.target.value)}
              />
            </label>
            <label>
              音乐音量：{backgroundMusicVolume}%
              <input
                type="range"
                min="0"
                max="100"
                step="1"
                value={backgroundMusicVolume}
                onChange={(event) => updateMusicPreviewVolume(event.target.value)}
              />
            </label>
          </>
        )}
        <small>音乐不足视频时长时会自动循环，相邻循环片段使用 1 秒淡入淡出衔接。</small>
        {!isCreate && (
          <button type="button" className="primary" disabled={busy} onClick={() => saveMusicSettings()}>
            <Save size={18} /> 保存配乐设置
          </button>
        )}
      </div>
    );
  }

  function renderCoverWorkspace(mode) {
    const isCreate = mode === 'create';
    return (
      <div className={coverImagePreviewUrl ? 'cover-workspace' : 'cover-workspace single'}>
        <div className="cover-section">
          <h3>封面</h3>
          <p className="cover-layout-note">封面保持完整 9:16 竖屏画面，标题显示在图片上层。上传后可在右侧分别拖动两行文字，拖动右下角圆点调整字号。</p>
          <input
            ref={coverInputRef}
            className="hidden-input"
            type="file"
            accept="image/*"
            onChange={(event) => {
              setCoverImage(event.target.files?.[0] || null);
              setCoverPresetId('');
              setCoverPresetImageUrl('');
            }}
          />
          <button type="button" onClick={() => {
            if (!coverInputRef.current) return;
            coverInputRef.current.value = '';
            coverInputRef.current.click();
          }}>
            <ImagePlus size={18} /> {coverImage || coverPresetId ? '更换封面图片' : '上传封面图片'}
          </button>
          {coverImage && <small className="cover-file-name">{coverImage.name}</small>}
          {!coverImage && coverPresetId && <small>当前使用预设封面</small>}
          <label className="cover-mask-control">
            遮罩不透明度：{coverMaskOpacity}%
            <input
              type="range"
              min="0"
              max="100"
              step="1"
              value={coverMaskOpacity}
              onChange={(event) => setCoverMaskOpacity(Number(event.target.value))}
            />
            <small>数值越高，背景越暗，标题越醒目。</small>
          </label>
          {isCreate ? (
            <small>创建项目时会自动生成并保存封面。</small>
          ) : (
            <div className="actions">
              <button
                className="primary"
                disabled={busy || !titleLine1.trim() || !titleLine2.trim() || (!coverImage && !coverPresetId && !project?.cover_source_url)}
                onClick={() => generateCover()}
              >
                <ImagePlus size={18} /> {project?.cover_url ? '重新合成封面' : '生成 9:16 封面'}
              </button>
            </div>
          )}
        </div>
        {coverImagePreviewUrl && (
          <div className="cover-preview">
            <CoverTitleEditor
              imageUrl={coverImagePreviewUrl}
              line1={titleLine1}
              line2={titleLine2}
              positions={coverTitlePositions}
              maskOpacity={coverMaskOpacity}
              onChange={updateCoverTitlePosition}
            />
            <small className="cover-editor-hint">拖动标题框调整位置，拖动右下角圆点调整字号</small>
            {!isCreate && project?.cover_url && (
              <button type="button" className="cover-download-button" disabled={busy} onClick={downloadCover}>
                <Download size={18} /> 应用调整并下载封面
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={sidebarCollapsed ? 'app sidebar-collapsed' : 'app'}>
      <aside className={sidebarCollapsed ? 'sidebar collapsed' : 'sidebar'}>
        <button
          type="button"
          className="sidebar-toggle"
          title={sidebarCollapsed ? '展开导航栏' : '折叠导航栏'}
          aria-label={sidebarCollapsed ? '展开导航栏' : '折叠导航栏'}
          aria-expanded={!sidebarCollapsed}
          onClick={toggleSidebar}
        >
          {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
        <div className="brand"><span className="brand-symbol"><Film size={22} /></span><span className="brand-label">VideoGen<small>短视频创作工作台</small></span></div>
        <div className="nav-caption">工作空间</div>
        <nav aria-label="主导航">
          <button
            title="项目"
            className={tab === 'create' ? 'active' : ''}
            onClick={startNewVideo}
          ><Scissors size={18} /><span>新建视频</span></button>
          <button title="项目工作台" className={tab === 'projects' ? 'active' : ''} onClick={() => setTab('projects')}><Archive size={18} /><span>项目工作台</span></button>
          <button title="素材库" className={tab === 'library' ? 'active' : ''} onClick={() => { setTab('library'); refreshMedia().catch(error => setMessage(error.message)); }}><Film size={18} /><span>素材库</span></button>
          {project?.workflow_type === 'video_library' && (
            <button title="导出" className={tab === 'export' ? 'active' : ''} onClick={() => setTab('export')}><Download size={18} /><span>导出</span></button>
          )}
          <button title="设置" className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}><Settings size={18} /><span>设置</span></button>
        </nav>
        <div className="project-picker">
          <ProjectSelect
            projects={projects.filter((item) => !item.archived).sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))}
            projectId={projectId}
            open={projectMenuOpen}
            onOpenChange={setProjectMenuOpen}
            onSelect={(id) => {
              setProjectMenuOpen(false);
              refreshAll(id);
            }}
            disabled={busy}
          />
          <button className="danger icon-only" disabled={!projectId || busy} title="删除项目" onClick={deleteProject}><Trash2 size={18} /></button>
        </div>
      </aside>

      <main>
        <header className={tab === 'create' ? 'topbar compact-topbar' : 'topbar'}>
          {tab === 'create' && <div className="workspace-breadcrumb"><span>工作空间</span><ChevronRight size={14} /><strong>新建视频</strong></div>}
          {tab !== 'create' && (
            <div>
              <h1>{tab === 'settings' ? '本地设置' : tab === 'projects' ? '项目工作台' : (project?.name || 'AI 短视频草稿工作台')}</h1>
              <p>{tab === 'settings' ? '项目与剪映草稿保存位置' : '输入文案 · 配音 · 标题 · 音乐 · 封面 · 导出剪映草稿'}</p>
            </div>
          )}
          <div className="top-actions">
            {project && tab !== 'create' && tab !== 'projects' && (
              <div className="project-name-editor">
                <input
                  value={projectNameDraft}
                  onChange={(event) => setProjectNameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') saveProjectName();
                    if (event.key === 'Escape') setProjectNameDraft(project.name || '');
                  }}
                  aria-label="项目名称"
                  maxLength={80}
                />
                <button
                  type="button"
                  className="icon-button"
                  title="保存项目名称"
                  disabled={busy || !projectNameDraft.trim() || projectNameDraft.trim() === (project?.name || '').trim()}
                  onClick={saveProjectName}
                >
                  <Check size={18} />
                </button>
              </div>
            )}
            <span className={workflowBusy ? 'status busy' : 'status'}>{workflowMessage}</span>
          </div>
        </header>

        {tab === 'library' && (
          <MediaLibrary
            media={media}
            reload={refreshMedia}
            onError={setMessage}
            libraryTab={libraryTab}
            onLibraryTabChange={setLibraryTab}
            project={project}
            projectId={projectId}
            busy={busy}
            onProjectRefresh={() => refreshProject(projectId)}
          />
        )}
        {tab === 'projects' && (
          <section className="band">
            <ProjectManager
              projects={projects}
              activeProjectId={projectId}
              search={projectSearch}
              filter={projectFilter}
              onSearch={setProjectSearch}
              onFilter={setProjectFilter}
              onOpen={(id) => refreshAll(id)}
              onArchive={setProjectArchived}
              onDelete={deleteProjectItem}
              onDeleteMany={deleteProjects}
              busy={busy}
            />
          </section>
        )}
        {tab === 'export' && project?.workflow_type === 'video_library' && (
          <section className="band result">
            <h2>导出剪映草稿</h2>
            <p>草稿包含背景模板、随机匹配的视频素材、配音、每行最多 9 字的字幕、两行标题、背景音乐和封面。</p>
            {creationStatus.error && (
              <div className="video-error" role="alert">
                <strong>{creationStatus.error}</strong>
                <div className="video-error-actions">
                  {creationProjectIdRef.current && (
                    <button type="button" disabled={busy} onClick={retryCreation}>重试未完成步骤</button>
                  )}
                  <button
                    type="button"
                    onClick={() => setCreationStatus({ active: false, label: '', error: '', failedIndex: 0 })}
                  >
                    关闭提示
                  </button>
                </div>
              </div>
            )}
            <div className="video-summary">
              <span>配音时长 <b>{Number(project.audio_duration_sec || 0).toFixed(1)} 秒</b></span>
              <span>标题 <b>{project.title_line1 ? `${project.title_line1} / ${project.title_line2 || '—'}` : '未填写'}</b></span>
              <span>背景音乐 <b>{project.background_music_name || '未选择'}</b></span>
              <span>封面 <b>{project.cover_url ? '已生成' : '未生成'}</b></span>
              <span>带货画面 <b>{(() => {
                const placement = project.product_placement || {};
                const count = (placement.items || []).length;
                return count
                  ? `${count} 个 · 从 ${Number(placement.start_sec || 0).toFixed(1)} 秒开始`
                  : '未配置';
              })()}</b></span>
            </div>
            {project.audio_url && (
              <div className="export-voice-preview">
                <audio
                  key={`${project.audio_url}-${voiceAudioVersion}`}
                  ref={mainVoicePreviewRef}
                  controls
                  crossOrigin="anonymous"
                  preload="metadata"
                  src={`${API}${project.audio_url}?v=${voiceAudioVersion || encodeURIComponent(project.updated_at || '')}`}
                  onLoadedMetadata={(event) => { event.currentTarget.volume = Math.min(1, voiceVolume / 100); }}
                  onPlay={(event) => activateMainVoicePreview(event.currentTarget)}
                />
                <span>配音音量 {voiceVolume}%</span>
              </div>
            )}
            <section className="vlib-script-card">
              <div className="vlib-script-heading">
                <div>
                  <h3>项目文案</h3>
                  <small>{scriptCharacterCount(project.raw_script)} 字 · 创建于 {formatProjectTime(project.created_at)}</small>
                </div>
                <button type="button" onClick={copyProjectScript} disabled={!project.raw_script}>复制文案</button>
              </div>
              <pre className="vlib-script-text">{project.raw_script || '（没有保存文案）'}</pre>
              <div className="vlib-title-preview">
                <div><small>标题第一行</small><b>{project.title_line1 || '未填写'}</b></div>
                <div><small>标题第二行</small><b>{project.title_line2 || '未填写'}</b></div>
              </div>
            </section>
            <ProductPlacementPanel
              key={project.id}
              saveRef={productPlacementSaveRef}
              project={project}
              projectId={projectId}
              media={media}
              busy={busy}
              onProjectRefresh={() => refreshProject(projectId)}
              onError={setMessage}
            />
            <details
              className="vlib-export-settings"
              open={settingsOpen}
              onToggle={(event) => setSettingsOpen(event.currentTarget.open)}
            >
              <summary>修改设置</summary>
              <div className="vlib-settings-grid">
                {renderVoiceSettings('project')}
                {renderTitleSettings('project')}
                {renderMusicSettings('project')}
                {renderCoverWorkspace('project')}
              </div>
            </details>
            <small className="vlib-settings-hint">修改配音音量、标题、音乐或封面后，请先点击对应的保存/生成按钮，再导出。</small>
            <div className="export-actions">
              <button type="button" onClick={() => {
                setLibraryTab('product');
                setTab('library');
                refreshMedia().catch(error => setMessage(error.message));
              }}><ImagePlus size={18} /> 管理带货素材库</button>
              <button type="button" onClick={openExportFolder}><FolderOpen size={18} /> 打开导出文件夹</button>
              <button className="primary" onClick={exportPackage} disabled={busy || !project.audio_url}><Archive size={18} /> 导出剪映草稿</button>
            </div>
            {exportResult && (
              <div className="export-result-card">
                <div>
                  <strong>剪映草稿导出完成</strong>
                  <small>保存位置：{exportResult.export_folder}</small>
                  {exportResult.verification?.jianying?.draft_path && (
                    <small>剪映草稿：{exportResult.verification.jianying.draft_path}</small>
                  )}
                </div>
                <div className="export-result-actions">
                  {exportResult.verification?.jianying && (
                    <button onClick={openDraftFolder}><FolderOpen size={18} /> 打开剪映草稿</button>
                  )}
                  <button className="primary" onClick={openExportFolder}><FolderOpen size={18} /> 打开导出文件夹</button>
                </div>
              </div>
            )}
          </section>
        )}
        {tab === 'settings' && (
          <section className="band settings-workspace">
            <div className="settings-heading">
              <div>
                <h2>本地目录</h2>
                <p>目录设置保存在这台电脑上。</p>
              </div>
              <span className="settings-mode">本地模式</span>
            </div>
            <div className="directory-setting-row">
              <div className="directory-setting-icon"><FolderOpen size={20} /></div>
              <div className="directory-setting-copy">
                <strong>项目目录</strong>
                <span title={appSettings.project_directory}>{appSettings.project_directory || '未设置'}</span>
              </div>
              <button
                type="button"
                className="primary"
                disabled={busy || Boolean(selectingDirectory)}
                onClick={() => selectAppDirectory('project')}
              >
                <FolderOpen size={18} /> {selectingDirectory === 'project' ? '选择中…' : '选择目录'}
              </button>
            </div>
            <div className="directory-setting-row">
              <div className="directory-setting-icon"><Film size={20} /></div>
              <div className="directory-setting-copy">
                <strong>剪映草稿目录</strong>
                <span title={appSettings.jianying_drafts_directory}>{appSettings.jianying_drafts_directory || '使用剪映默认目录'}</span>
              </div>
              <button
                type="button"
                disabled={busy || Boolean(selectingDirectory)}
                onClick={() => selectAppDirectory('jianying')}
              >
                <FolderOpen size={18} /> {selectingDirectory === 'jianying' ? '选择中…' : '选择目录'}
              </button>
            </div>
          </section>
        )}

        {tab === 'create' && (
          <section className="band project-workspace">
            <form
              onSubmit={createProject}
              className="video-create-form"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA') event.preventDefault();
              }}
            >
              <div className="panel create-script-panel">
                <div className="create-heading">
                  <div>
                    <span className="studio-eyebrow">VIDEO STUDIO</span>
                    <h2>新建视频</h2>
                    <p>让好文案，成为好视频。配置画面与声音，一键生成剪映草稿。</p>
                  </div>
                  <span className="create-badge">9:16 · 1080 × 1920</span>
                </div>
                <div className={`create-fields ${creationStatus.active ? 'busy' : ''}`}>
                  {renderPresetPanel()}
                  <div className="create-flow">
                    <section className="create-card create-card-title">{renderTitleSettings('create')}</section>
                    <section className="create-card create-card-script">
                      <div className="create-card-head">
                        <h3>视频文案</h3>
                        <span className="script-character-count">{scriptCharacterCount(rawScriptDraft)} 字</span>
                      </div>
                      <div className="script-editor">
                        <div ref={scriptHighlightRef} className="script-editor-highlight" aria-hidden="true">
                          {renderScriptHighlight(rawScriptDraft)}
                        </div>
                        <textarea
                          ref={scriptTextareaRef}
                          className="create-script-textarea"
                          required
                          value={rawScriptDraft}
                          onChange={event => setRawScriptDraft(event.target.value)}
                          onScroll={event => {
                            if (scriptHighlightRef.current) {
                              scriptHighlightRef.current.scrollTop = event.currentTarget.scrollTop;
                            }
                          }}
                          placeholder="输入或粘贴视频文案，然后在下方配置配音、音乐和封面"
                          aria-label="视频文案"
                        />
                      </div>
                      <div className="script-marker-row">
                        <button type="button" onClick={insertProductMarker} disabled={rawScriptDraft.includes(PRODUCT_MARKER)}>
                          插入{PRODUCT_MARKER}
                        </button>
                        <small>
                          {rawScriptDraft.includes(PRODUCT_MARKER)
                            ? '已插入：从该位置开始叠加带货画中画；标记不会朗读，也不会出现在字幕中。'
                            : '把光标放到需要开始展示带货画面的位置，再点击插入。'}
                        </small>
                      </div>
                    </section>
                    <section className="create-card create-card-template">
                      <TemplatePicker templates={media.templates} value={backgroundTemplateId} onChange={setBackgroundTemplateId} reload={refreshMedia} onError={setMessage} />
                    </section>
                    <section className="create-card create-card-voice">{renderVoiceSettings('create')}</section>
                    <section className="create-card create-card-music">{renderMusicSettings('create')}</section>
                    <section className="create-card create-card-cover">{renderCoverWorkspace('create')}</section>
                  </div>
                </div>
                {creationStatus.active && <p className="video-progress" role="status">{creationStatus.label}</p>}
                {creationStatus.error && <p className="video-error" role="alert">{creationStatus.error}</p>}
                {!media.videos.length && <p className="studio-empty-hint"><Film size={18} /><span>先添加视频素材，开启你的第一条创作。</span><button type="button" onClick={() => setTab('library')}>上传视频素材 <ChevronRight size={14} /></button></p>}
                <div className="script-create-actions">
                  <button
                    className="primary"
                    disabled={busy || !rawScriptDraft.trim() || !media.videos.length || !media.templates.length || !titleLine1.trim() || !titleLine2.trim() || (audioSource === 'upload' && !audioFile)}
                  >
                    <Film size={18} /> {creationStatus.active ? creationStatus.label : '生成视频草稿'}
                  </button>
                </div>
              </div>
            </form>
          </section>
        )}

      </main>

    </div>
  );
}

function ProjectManager({ projects, activeProjectId, search, filter, onSearch, onFilter, onOpen, onArchive, onDelete, onDeleteMany, busy = false }) {
  const [selectedIds, setSelectedIds] = useState(new Set());
  const selectAllRef = useRef(null);
  const sorted = [...projects].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  const recent = sorted.filter((item) => !item.archived).slice(0, 6);
  const query = search.trim().toLowerCase();
  const filtered = sorted.filter((item) => {
    if (query && !String(item.name || '').toLowerCase().includes(query)) return false;
    if (filter === 'archived') return item.archived;
    if (item.archived) return false;
    const stage = projectStage(item);
    if (filter === 'active') return stage.key === 'active';
    if (filter === 'todo') return stage.key === 'todo';
    if (filter === 'done') return stage.key === 'done';
    return true;
  });
  const filteredIds = filtered.map((item) => item.id);
  const allSelected = filteredIds.length > 0 && filteredIds.every((id) => selectedIds.has(id));
  const someSelected = filteredIds.some((id) => selectedIds.has(id));

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected && !allSelected;
  }, [someSelected, allSelected]);

  useEffect(() => {
    const validIds = new Set(projects.map((item) => item.id));
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => validIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [projects]);

  function toggleSelection(id) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleFiltered() {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allSelected) filteredIds.forEach((id) => next.delete(id));
      else filteredIds.forEach((id) => next.add(id));
      return next;
    });
  }

  async function deleteSelected() {
    if (!selectedIds.size || !onDeleteMany) return;
    const deleted = await onDeleteMany([...selectedIds]);
    if (deleted) setSelectedIds(new Set());
  }

  return (
    <div className="project-manager panel">
      <div className="project-manager-heading">
        <div><h2>项目工作台</h2><small>优先继续最近项目，旧项目可归档后隐藏。</small></div>
        <span>{filtered.length} 个项目</span>
      </div>
      {recent.length > 0 && (
        <div className="recent-projects">
          <strong>最近项目</strong>
          <div>
            {recent.map((item) => (
              <button type="button" className={item.id === activeProjectId ? 'active' : ''} key={item.id} disabled={busy} onClick={() => onOpen(item.id)}>
                <span>{item.name}</span><small>{projectStage(item).label}</small>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="project-manager-toolbar">
        <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索项目名称" />
        <select value={filter} onChange={(event) => onFilter(event.target.value)}>
          <option value="current">全部未归档</option>
          <option value="active">进行中</option>
          <option value="todo">待处理</option>
          <option value="done">已完成</option>
          <option value="archived">已归档</option>
        </select>
        <button type="button" className="danger" disabled={busy || !selectedIds.size} onClick={deleteSelected}>
          批量删除（{selectedIds.size}）
        </button>
      </div>
      <div className="project-table-wrap">
        <table className="project-table">
          <thead><tr>
            <th className="project-select-cell">
              <input
                ref={selectAllRef}
                type="checkbox"
                checked={allSelected}
                onChange={toggleFiltered}
                disabled={busy}
                aria-label="全选当前列表"
                title="全选当前列表"
              />
            </th>
            <th>项目名称</th><th>当前阶段</th><th>修改时间</th><th>素材数</th><th>操作</th>
          </tr></thead>
          <tbody>
            {filtered.map((item) => {
              const stage = projectStage(item);
              return (
                <tr key={item.id} className={item.id === activeProjectId ? 'active' : ''}>
                  <td className="project-select-cell">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(item.id)}
                      onChange={() => toggleSelection(item.id)}
                      disabled={busy}
                      aria-label={`选择项目 ${item.name}`}
                    />
                  </td>
                  <td><button type="button" className="project-name-link" disabled={busy} onClick={() => onOpen(item.id)}>{item.name}</button></td>
                  <td><span className={`project-stage ${stage.key}`}>{stage.label}</span></td>
                  <td>{formatProjectTime(item.updated_at)}</td>
                  <td>{item.shot_count || 0}</td>
                  <td><div className="project-row-actions">
                    <button type="button" disabled={busy} onClick={() => onOpen(item.id)}>继续</button>
                    <button type="button" disabled={busy} onClick={() => onArchive(item, !item.archived)}>{item.archived ? '恢复' : '归档'}</button>
                    <button type="button" className="danger" disabled={busy} onClick={() => onDelete(item)}>删除</button>
                  </div></td>
                </tr>
              );
            })}
            {!filtered.length && <tr><td colSpan="6" className="empty-projects">没有符合条件的项目</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProjectSelect({ projects, projectId, open, onOpenChange, onSelect, disabled = false }) {
  const selected = projects.find((item) => item.id === projectId);
  return (
    <div className="custom-select">
      <button type="button" className={open ? 'custom-select-trigger open' : 'custom-select-trigger'} disabled={disabled} onClick={() => onOpenChange(!open)}>
        <span>{selected?.name || '选择项目'}</span>
      </button>
      {open && (
        <div className="custom-select-menu">
          <button type="button" className={!projectId ? 'selected' : ''} disabled={disabled} onClick={() => onSelect('')}>选择项目</button>
          {projects.map((item) => (
            <button type="button" key={item.id} className={item.id === projectId ? 'selected' : ''} disabled={disabled} onClick={() => onSelect(item.id)}>
              {item.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function VoiceSelect({ value, onChange }) {
  return (
    <label className="compact-control">
      音色
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {VOICE_OPTIONS.map((voice) => <option key={voice.value} value={voice.value}>{voice.label}</option>)}
      </select>
    </label>
  );
}

function SpeechRateSelect({ value, onChange }) {
  return (
    <label className="compact-control">
      语速
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        <option value={-50}>慢速</option>
        <option value={-25}>偏慢</option>
        <option value={0}>正常</option>
        <option value={25}>偏快</option>
        <option value={50}>快速</option>
      </select>
    </label>
  );
}

createRoot(document.getElementById('root')).render(<App />);
