import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Archive, Check, ChevronLeft, ChevronRight, Copy, Download, Eraser, Film, FolderOpen, ImagePlus, Library, MessageSquare, Mic, Music, RefreshCw, Save, Scissors, Send, Tags, Trash2, Wand2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './styles.css';

const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
const ASSET_PAGE_SIZE = 60;
const DEFAULT_PROMOTION_BOOK = '《国之脊梁》';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'videogen.sidebarCollapsed';
const ADD_PROMOTION_BOOK_OPTION = '__add_promotion_book__';
const HISTORY_MODEL_LABELS = {
  minimax: 'MiniMax',
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
};
const IMAGE_GENERATION_PROVIDER_LABELS = {
  seedream: 'Seedream',
  openai: 'OpenAI',
};
const MAX_VOICE_VOLUME_PERCENT = 200;
const DEFAULT_COVER_TITLE_POSITIONS = {
  line1: { x: 0.5, y: 0.18, font_size: 124 },
  line2: { x: 0.5, y: 0.25, font_size: 124 },
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

function validLibrary(library) {
  const name = String(library?.name || '').trim();
  return Boolean(name) && !/^\?+$/.test(name);
}

function listToText(value) {
  return Array.isArray(value) ? value.join('，') : '';
}

function textToList(value) {
  return String(value || '')
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function stripScriptParagraphNumbers(value) {
  return String(value || '').replace(/(^|\n)([ \t]*)\[\d+\][ \t]*/g, '$1$2').trim();
}

function copyScriptWithoutParagraphNumbers(event) {
  const textarea = event.currentTarget;
  const start = textarea.selectionStart ?? 0;
  const end = textarea.selectionEnd ?? start;
  if (end <= start) return;
  const selectedText = textarea.value.slice(start, end);
  const cleanText = selectedText.replace(/(^|\n)([ \t]*)\[\d+\][ \t]*/g, '$1$2');
  event.preventDefault();
  event.clipboardData.setData('text/plain', cleanText);
}

async function writeClipboardText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall back for browsers that expose the Clipboard API but deny it in
      // the current (for example, non-secure) context.
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand('copy');
  textarea.remove();
  if (!copied) throw new Error('浏览器未允许访问剪贴板');
}

function scriptCharacterCount(value) {
  return Array.from(stripScriptParagraphNumbers(value).replace(/\s+/g, '')).length;
}

function formatPromotionBookTitle(value) {
  const title = String(value || '').trim().replace(/^《|》$/g, '').trim();
  return title ? `《${title}》` : DEFAULT_PROMOTION_BOOK;
}

function MarkdownContent({ children, className = '' }) {
  return (
    <div className={`markdown-content ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener" />
          ),
        }}
      >
        {String(children || '')}
      </ReactMarkdown>
    </div>
  );
}

function formatBatchImagePrompts(prompts) {
  const normalized = prompts.map((prompt) => String(prompt || '').trim()).filter(Boolean);
  const visualMarker = '具体画面：';
  const parsed = normalized.map((prompt) => {
    const visualIndex = prompt.indexOf(visualMarker);
    if (visualIndex < 0) return null;
    return {
      sharedStyle: prompt.slice(0, visualIndex).trim(),
      shotPrompt: prompt.slice(visualIndex + visualMarker.length).trim(),
    };
  });
  const hasSharedPromptParts = parsed.length > 0
    && parsed.every(Boolean)
    && parsed.every((item) => item.sharedStyle === parsed[0].sharedStyle);
  if (!hasSharedPromptParts) {
    return normalized
      .map((prompt, index) => `${String(index + 1).padStart(2, '0')}. ${prompt}`)
      .join('\n\n');
  }
  const numberedShotPrompts = parsed
    .map((item, index) => (
      `${String(index + 1).padStart(2, '0')}. ${visualMarker}${item.shotPrompt}`
    ))
    .join('\n\n');
  return [
    '共同提示词：',
    parsed[0].sharedStyle,
    '',
    '各分镜独立提示词：',
    numberedShotPrompts,
  ].join('\n');
}

function numberScriptParagraphs(value) {
  const script = stripScriptParagraphNumbers(value).replace(/\r\n?/g, '\n');
  if (!script) return '';
  return script.split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph, index) => `[${index + 1}] ${paragraph}`)
    .join('\n\n');
}

function splitScriptParagraphs(value) {
  const script = stripScriptParagraphNumbers(value).replace(/\r\n?/g, '\n');
  if (!script) return [];
  return script.split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function projectStage(project) {
  const status = project?.status || 'created';
  const historyStep = Number(project?.history_workflow?.active_step || 0);
  const historyStatus = project?.history_workflow?.status || '';
  if (project?.has_export || project?.export_url || project?.draft_url || project?.last_export_at) return { key: 'done', label: '已完成' };
  if (['generating_shots', 'generating_images'].includes(status)) return { key: 'active', label: '处理中' };
  if (historyStatus === 'completed') return { key: 'todo', label: '历史文案已定稿' };
  if (historyStep) return { key: 'active', label: `Step ${historyStep} 待确认` };
  if (status === 'created') return { key: 'todo', label: '等待历史创作' };
  if (status === 'script_ready') return { key: 'todo', label: '文案已完成' };
  if (status === 'shots_ready') return { key: 'todo', label: project?.voice_url ? '等待标题封面' : '等待配音' };
  if (status === 'shot_generation_failed') return { key: 'todo', label: '分镜生成失败' };
  if (project?.cover_url) return { key: 'todo', label: '可以导出' };
  return { key: 'todo', label: '待继续' };
}

function formatProjectTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function emptyTagForm() {
  return {
    object: '',
    scene: '',
    keywords: '',
    source_page: '',
    source_note: '用户上传',
    copyright_note: '自用素材',
    keep_original: false,
    video_processing_mode: 'split',
  };
}

function assetImageUrl(asset) {
  if (!asset?.file_url) return '';
  const version = asset.updated_at || asset.created_at || '';
  return `${API}${asset.file_url}${version ? `?v=${encodeURIComponent(version)}` : ''}`;
}

function normalizeCoverTitlePositions(value) {
  const source = value && typeof value === 'object' ? value : {};
  return Object.fromEntries(Object.entries(DEFAULT_COVER_TITLE_POSITIONS).map(([key, fallback]) => {
    const line = source[key] && typeof source[key] === 'object' ? source[key] : {};
    return [key, {
      x: Math.max(0.03, Math.min(0.97, Number(line.x ?? fallback.x) || fallback.x)),
      y: Math.max(0.03, Math.min(0.97, Number(line.y ?? fallback.y) || fallback.y)),
      font_size: Math.max(32, Math.min(260, Number(line.font_size ?? fallback.font_size) || fallback.font_size)),
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
  const [tab, setTab] = useState('create');
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState(null);
  const [shots, setShots] = useState([]);
  const [assets, setAssets] = useState([]);
  const [generatedAssets, setGeneratedAssets] = useState([]);
  const [library, setLibrary] = useState(null);
  const [musicLibrary, setMusicLibrary] = useState([]);
  const [editingAsset, setEditingAsset] = useState(null);
  const [pendingUpload, setPendingUpload] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [rawScriptDraft, setRawScriptDraft] = useState('');
  const [rewrittenScriptEditor, setRewrittenScriptEditor] = useState('');
  const [editingParagraphIndex, setEditingParagraphIndex] = useState(-1);
  const [paragraphDraft, setParagraphDraft] = useState('');
  const [historyChatInput, setHistoryChatInput] = useState('');
  const [promotionBooks, setPromotionBooks] = useState([DEFAULT_PROMOTION_BOOK]);
  const [historyBookTitle, setHistoryBookTitle] = useState(DEFAULT_PROMOTION_BOOK);
  const [processingImage, setProcessingImage] = useState('');
  const [grayscaleProcessingIds, setGrayscaleProcessingIds] = useState(new Set());
  const [generatingShotIds, setGeneratingShotIds] = useState(new Set());
  const [recognizingShotIds, setRecognizingShotIds] = useState(new Set());
  const [imagePromptEditors, setImagePromptEditors] = useState({});
  const [materialSourceStrategy, setMaterialSourceStrategy] = useState('ai_only');
  const [storyboardModelProvider, setStoryboardModelProvider] = useState('deepseek');
  const [imageGenerationProvider, setImageGenerationProvider] = useState('seedream');
  const [voiceType, setVoiceType] = useState(VOICE_OPTIONS[0].value);
  const [speechRate, setSpeechRate] = useState(0);
  const [voicePreviewUrl, setVoicePreviewUrl] = useState('');
  const [previewingVoice, setPreviewingVoice] = useState(false);
  const [voiceVolume, setVoiceVolume] = useState(100);
  const [voiceAudioVersion, setVoiceAudioVersion] = useState(0);
  const [projectNameDraft, setProjectNameDraft] = useState('');
  const [titleLine1, setTitleLine1] = useState('');
  const [titleLine2, setTitleLine2] = useState('');
  const [titleCandidates, setTitleCandidates] = useState([]);
  const [titleConfirmed, setTitleConfirmed] = useState(false);
  const [publishShortTitle, setPublishShortTitle] = useState('');
  const [publishDescription, setPublishDescription] = useState('');
  const [coverImage, setCoverImage] = useState(null);
  const [coverImagePreviewUrl, setCoverImagePreviewUrl] = useState('');
  const [coverTitlePositions, setCoverTitlePositions] = useState(DEFAULT_COVER_TITLE_POSITIONS);
  const [coverMaskOpacity, setCoverMaskOpacity] = useState(35);
  const projectCoverTitlePositionsKey = JSON.stringify(project?.cover_title_positions ?? null);
  const [backgroundMusicId, setBackgroundMusicId] = useState('');
  const [backgroundMusicStart, setBackgroundMusicStart] = useState(0);
  const [backgroundMusicVolume, setBackgroundMusicVolume] = useState(20);
  const [previewAsset, setPreviewAsset] = useState(null);
  const [libraryPickerShotId, setLibraryPickerShotId] = useState('');
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [exportResult, setExportResult] = useState(null);
  const [selectedAssetIds, setSelectedAssetIds] = useState(new Set());
  const [visibleAssetCount, setVisibleAssetCount] = useState(ASSET_PAGE_SIZE);
  const [assetTypeFilter, setAssetTypeFilter] = useState('all');
  const [projectSearch, setProjectSearch] = useState('');
  const [projectFilter, setProjectFilter] = useState('current');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true',
  );
  const folderFallbackRef = useRef(null);
  const uploadInputRef = useRef(null);
  const coverInputRef = useRef(null);
  const musicInputRef = useRef(null);
  const musicVoiceInputRef = useRef(null);
  const musicPreviewRef = useRef(null);
  const voicePreviewRef = useRef(null);
  const generatedVoicePlayerRef = useRef(null);
  const mainVoicePreviewRef = useRef(null);
  const mainVoiceAudioGraphRef = useRef({ element: null, context: null, gain: null });
  const playVoicePreviewAfterLoadRef = useRef(false);
  const playGeneratedVoiceAfterLoadRef = useRef(false);
  const hydratedProjectSettingsRef = useRef('');
  const lastProjectStatusRef = useRef('');
  const rewrittenScriptDirtyRef = useRef(false);
  const paragraphSaveVersionRef = useRef(0);
  const paragraphSaveQueueRef = useRef(Promise.resolve());
  const deletingParagraphRef = useRef(false);

  const activeLibrary = validLibrary(library) ? library : null;
  const rewrittenParagraphs = useMemo(
    () => splitScriptParagraphs(rewrittenScriptEditor),
    [rewrittenScriptEditor],
  );
  const historyWorkflow = project?.history_workflow || {};
  const historyActiveStep = Number(historyWorkflow.active_step || 0);
  const historyOutput = String(historyWorkflow.outputs?.[String(historyActiveStep)] || '');
  const historyMessages = historyWorkflow.messages?.[String(historyActiveStep)] || [];
  const stepTwoComparison = historyWorkflow.step2_comparison || null;
  const historyModelProvider = project?.history_model_provider || 'minimax';
  const selectedAssets = useMemo(() => {
    const map = new Map();
    assets.forEach((asset) => map.set(asset.id, asset));
    generatedAssets.forEach((asset) => map.set(asset.id, {
      ...asset,
      file_type: 'image',
      file_name: asset.file_name || `AI 图片 ${asset.image_size || ''}`.trim(),
    }));
    return map;
  }, [assets, generatedAssets]);
  const selectableAssets = useMemo(() => [
    ...generatedAssets.map((asset) => ({
      ...asset,
      file_type: 'image',
      file_name: asset.file_name || `AI 图片 ${asset.image_size || ''}`.trim(),
      asset_source: asset.asset_source || 'ai_generated',
    })),
    ...assets.map((asset) => ({ ...asset, asset_source: 'local' })),
  ], [assets, generatedAssets]);
  const generatedAssetsByShot = useMemo(() => {
    const map = new Map();
    generatedAssets.forEach((asset) => {
      const list = map.get(asset.shot_id) || [];
      list.push({
        ...asset,
        file_type: 'image',
        file_name: asset.file_name || `AI 图片 ${asset.image_size || ''}`.trim(),
      });
      map.set(asset.shot_id, list);
    });
    shots.forEach((shot) => {
      const selected = assets.find((asset) => asset.id === shot.selected_asset_id);
      if (!selected) return;
      const list = map.get(shot.id) || [];
      if (!list.some((asset) => asset.id === selected.id)) {
        list.unshift({
          ...selected,
          shot_id: shot.id,
          asset_source: shot.asset_source === 'library_upload' ? 'library_upload' : 'local',
        });
      }
      map.set(shot.id, list);
    });
    map.forEach((list) => list.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))));
    return map;
  }, [assets, generatedAssets, shots]);
  const generationProgress = useMemo(() => {
    const total = shots.length || project?.generation_total || 0;
    const success = shots.filter((shot) => ['uploaded', 'ai_generated', 'matched', 'prompt_ready'].includes(shot.status)).length;
    const failed = shots.filter((shot) => shot.status === 'no_image').length;
    const completed = shots.length
      ? success + failed
      : Math.min(total, project?.generation_completed || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    return { total, completed, success, failed, percent };
  }, [project, shots]);
  const filteredAssets = useMemo(
    () => assets.filter((asset) => assetTypeFilter === 'all' || asset.file_type === assetTypeFilter),
    [assets, assetTypeFilter],
  );
  const visibleAssets = useMemo(
    () => filteredAssets.slice(0, visibleAssetCount),
    [filteredAssets, visibleAssetCount],
  );

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
    const [projectList, assetList, libraryData, musicData, promotionBookData, projectData] = await Promise.all([
      request('/api/projects'),
      request('/api/assets'),
      request('/api/assets/library'),
      request('/api/assets/music'),
      request('/api/projects/promotion-books'),
      id ? request(`/api/projects/${id}`) : Promise.resolve(null),
    ]);
    setProjects(projectList.projects);
    setAssets(assetList.assets);
    setLibrary(validLibrary(libraryData.library) ? libraryData.library : null);
    setMusicLibrary(musicData.music || []);
    setPromotionBooks(promotionBookData.books?.length ? promotionBookData.books : [DEFAULT_PROMOTION_BOOK]);
    if (projectData) {
      setProject(projectData.project);
      setShots(projectData.shots);
      setGeneratedAssets(projectData.generated_assets || []);
      setProjectId(id);
      setHistoryBookTitle(formatPromotionBookTitle(projectData.project.promotion_book_title));
      if (hydratedProjectSettingsRef.current !== id) {
        hydratedProjectSettingsRef.current = id;
        setStoryboardModelProvider(projectData.project.storyboard_model_provider || 'deepseek');
        setImageGenerationProvider(projectData.project.image_generation_provider || 'seedream');
      }
    } else {
      setProject(null);
      setShots([]);
      setGeneratedAssets([]);
      setProjectId('');
      hydratedProjectSettingsRef.current = '';
    }
  }

  async function refreshProject(id = projectId) {
    if (!id) return;
    const data = await request(`/api/projects/${id}`);
    setProject(data.project);
    setShots(data.shots);
    setGeneratedAssets(data.generated_assets || []);
  }


  useEffect(() => {
    refreshAll();
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
    setCoverImagePreviewUrl(project?.cover_source_url ? `${API}${project.cover_source_url}` : '');
    return undefined;
  }, [coverImage, project?.cover_source_url]);

  useEffect(() => {
    const hasProcessingVideo = assets.some((asset) => (
      asset.file_type === 'video' && ['analyzing', 'probing', 'splitting'].includes(asset.analysis_status)
    ));
    // 上传视频后会在 message 里挂一句"素材已上传，正在识别标签"，
    // 但没有任何路径去覆盖它。这里在所有视频都离开处理中状态时把
    // 这条提示清掉，让徽标回落到默认的"就绪"。
    if (!hasProcessingVideo && message === '素材已上传，正在识别标签') {
      setMessage('');
    }
    if (!hasProcessingVideo) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const data = await request('/api/assets');
        setAssets(data.assets || []);
      } catch (_) {
        // The next interval will retry without interrupting the rest of the UI.
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [assets, message]);

  useEffect(() => {
    if (!project) return;
    setProjectNameDraft(project.name || '');
    setTitleLine1(project.title_line1 || '');
    setTitleLine2(project.title_line2 || '');
    setTitleCandidates(project.title_candidates || []);
    setTitleConfirmed(Boolean(project.title_line1 && project.title_line2) || Boolean(project.cover_url));
    setPublishShortTitle(project.publish_short_title || '');
    setPublishDescription(project.publish_description || '');
    setCoverImage(null);
    setCoverTitlePositions(normalizeCoverTitlePositions(project.cover_title_positions));
    setCoverMaskOpacity(Math.round(Number(project.cover_mask_opacity ?? 0.35) * 100));
    setBackgroundMusicId(project.background_music_id || '');
    setBackgroundMusicStart(Number(project.background_music_start_sec || 0));
    setBackgroundMusicVolume(Math.round(Number(project.background_music_volume ?? 0.2) * 100));
    setVoiceVolume(Math.round(Number(project.voice_volume ?? 1) * 100));
    setHistoryBookTitle(formatPromotionBookTitle(project.promotion_book_title));
  }, [
    project?.id,
    project?.name,
    project?.append_book_promotion,
    project?.promotion_book_title,
    project?.title_line1,
    project?.title_line2,
    project?.publish_short_title,
    project?.publish_description,
    project?.cover_url,
    project?.cover_source_url,
    projectCoverTitlePositionsKey,
    project?.cover_mask_opacity,
    project?.background_music_id,
    project?.background_music_start_sec,
    project?.background_music_volume,
    project?.voice_volume,
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

  useEffect(() => {
    const player = generatedVoicePlayerRef.current;
    if (player) player.volume = Math.max(0, Math.min(1, voiceVolume / 100));
  }, [voiceVolume, project?.audio_url, voiceAudioVersion, tab]);

  useEffect(() => () => {
    const context = mainVoiceAudioGraphRef.current.context;
    if (context && context.state !== 'closed') context.close().catch(() => {});
  }, []);

  useEffect(() => {
    if (rewrittenScriptDirtyRef.current) return;
    setRewrittenScriptEditor(numberScriptParagraphs(project?.rewritten_script || ''));
    setEditingParagraphIndex(-1);
    setParagraphDraft('');
  }, [project?.id, project?.rewritten_script]);

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

  useEffect(() => {
    if (!assets.some((asset) => asset.analysis_status === 'analyzing')) return undefined;
    const timer = window.setInterval(() => {
      Promise.all([
        request('/api/assets').then((data) => setAssets(data.assets)),
        projectId ? refreshProject(projectId) : Promise.resolve(),
      ]);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [assets, projectId]);

  useEffect(() => {
    if (tab === 'assets') {
      setVisibleAssetCount(ASSET_PAGE_SIZE);
    }
  }, [tab, activeLibrary?.id]);

  useEffect(() => {
    if (!projectId || !['generating_shots', 'generating_images'].includes(project?.status)) return undefined;
    const timer = window.setInterval(() => {
      refreshProject(projectId);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [shots, projectId, project]);

  useEffect(() => {
    const previous = lastProjectStatusRef.current;
    const current = project?.status || '';
    if (['generating_shots', 'generating_images'].includes(previous) && current === 'shot_generation_failed') {
      setMessage(project?.generation_error ? `处理失败：${project.generation_error}` : '分镜生成失败');
    } else if (['generating_shots', 'generating_images'].includes(previous) && current && !['generating_shots', 'generating_images'].includes(current)) {
      if (generationProgress.total && generationProgress.completed < generationProgress.total) {
        setMessage(`分镜处理结束，仍有 ${generationProgress.total - generationProgress.completed} 个分镜未完成`);
      } else {
        setMessage(project?.material_source_strategy === 'prompt_only' ? '分镜提示词生成完成' : '分镜图片处理完成');
      }
    }
    lastProjectStatusRef.current = current;
  }, [project?.status, project?.generation_error, generationProgress.completed, generationProgress.total]);

  async function chooseLibraryFolder() {
    if ('showDirectoryPicker' in window) {
      const dir = await window.showDirectoryPicker({ mode: 'read' });
      await run('设置素材库', () => request('/api/assets/library', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: dir.name, path_hint: dir.name }),
      }));
      await refreshAll(projectId);
      return;
    }
    folderFallbackRef.current?.click();
  }

  async function chooseLibraryFallback(ev) {
    const file = ev.target.files?.[0];
    if (!file) return;
    const path = file.webkitRelativePath || file.name;
    const name = path.split('/')[0] || '素材库';
    await run('设置素材库', () => request('/api/assets/library', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, path_hint: name }),
    }));
    ev.target.value = '';
    await refreshAll(projectId);
  }

  async function createProject(ev) {
    ev.preventDefault();
    const form = new FormData(ev.currentTarget);
    const payload = Object.fromEntries(form.entries());
    payload.raw_script = rawScriptDraft;
    const data = await run('创建项目', () => request('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }));
    if (data?.project_id) {
      setRawScriptDraft('');
      await refreshAll(data.project_id);
      setTab('script');
    }
  }

  async function rewrite() {
    if (historyActiveStep && !window.confirm('从 Step 1 重新生成会清空当前三步创作进度，确认继续吗？')) return;
    setHistoryChatInput('');
    const data = await run('历史创作 Step 1', () => request(`/api/projects/${projectId}/history-workflow/steps/1`, {
      method: 'POST',
    }));
    if (data) {
      await refreshAll(projectId);
      setMessage('Step 1 策略分析已生成，可在右侧聊天修改，满意后再确认进入 Step 2');
    }
  }

  async function selectHistoryBook(nextTitle, { skipConfirmation = false } = {}) {
    const formatted = formatPromotionBookTitle(nextTitle);
    if (formatted === historyBookTitle) return true;
    if (
      historyActiveStep
      && !skipConfirmation
      && !window.confirm('切换带书会清空当前三步创作进度，确认继续吗？')
    ) return false;
    const data = await run('切换带书', () => request(`/api/projects/${projectId}/history-workflow/book`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: formatted }),
    }));
    if (!data) return false;
    setHistoryBookTitle(data.title || formatted);
    setPromotionBooks(data.books?.length ? data.books : promotionBooks);
    setHistoryChatInput('');
    await refreshAll(projectId);
    setMessage(`已选择 ${data.title || formatted}，请从 Step 1 重新开始`);
    return true;
  }

  async function selectHistoryModel(provider) {
    const data = await run('切换创作模型', () => request(
      `/api/projects/${projectId}/history-workflow/model`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      },
    ));
    if (!data) return;
    await refreshProject(projectId);
    setMessage(`已切换为 ${HISTORY_MODEL_LABELS[provider] || provider}，后续生成和问答将使用该模型`);
  }

  async function addPromotionBookFromSelect() {
    const title = window.prompt('请输入要添加的书名：')?.trim();
    if (!title) return;
    if (historyActiveStep && !window.confirm('添加并选择新书会清空当前三步创作进度，确认继续吗？')) return;
    const data = await run('添加书籍', async () => {
      const created = await request('/api/projects/promotion-books', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      const selected = await request(`/api/projects/${projectId}/history-workflow/book`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: created.title }),
      });
      return { ...selected, books: created.books };
    });
    if (!data) return;
    setPromotionBooks(data.books || []);
    setHistoryBookTitle(data.title);
    setHistoryChatInput('');
    await refreshAll(projectId);
    setMessage(`已添加并选择 ${data.title}，请执行 Step 1`);
  }

  function handleHistoryBookSelect(value) {
    if (value === ADD_PROMOTION_BOOK_OPTION) {
      addPromotionBookFromSelect();
      return;
    }
    selectHistoryBook(value);
  }

  async function deletePromotionBook() {
    if (promotionBooks.length <= 1) {
      setMessage('至少需要保留一本带货书籍');
      return;
    }
    if (!window.confirm(`确认从下拉列表删除 ${historyBookTitle} 吗？`)) return;
    const bareTitle = historyBookTitle.replace(/^《|》$/g, '');
    const data = await run('删除书籍', async () => {
      const deleted = await request(`/api/projects/promotion-books/${encodeURIComponent(bareTitle)}`, {
        method: 'DELETE',
      });
      const fallback = deleted.books[0];
      const selected = await request(`/api/projects/${projectId}/history-workflow/book`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: fallback }),
      });
      return { ...selected, books: deleted.books };
    });
    if (!data) return;
    setPromotionBooks(data.books);
    setHistoryBookTitle(data.title);
    setHistoryChatInput('');
    await refreshAll(projectId);
    setMessage(`已删除书籍，当前改为 ${data.title}，请重新执行 Step 1`);
  }

  async function runNextHistoryStep() {
    if (![1, 2].includes(historyActiveStep)) return;
    const nextStep = historyActiveStep + 1;
    setHistoryChatInput('');
    const data = await run(`历史创作 Step ${nextStep}`, () => request(
      `/api/projects/${projectId}/history-workflow/steps/${nextStep}`,
      { method: 'POST' },
    ));
    if (data) {
      await refreshAll(projectId);
      setMessage(
        nextStep === 2
          ? 'Step 2 正文已生成，可继续聊天修改，满意后再确认进入 Step 3'
          : 'Step 3 终审定稿已生成，可继续聊天修改，满意后确认定稿',
      );
    }
  }

  async function regenerateHistoryStep(step) {
    const downstreamSteps = [1, 2, 3].filter((item) => (
      item > step && Boolean(historyWorkflow.outputs?.[String(item)])
    ));
    const consequence = downstreamSteps.length
      ? `，并清除 Step ${downstreamSteps.join('、Step ')} 的旧结果`
      : '';
    if (!window.confirm(`确认重新生成 Step ${step}${consequence}吗？`)) return;
    setHistoryChatInput('');
    const data = await run(`重新生成 Step ${step}`, () => request(
      `/api/projects/${projectId}/history-workflow/steps/${step}`,
      { method: 'POST' },
    ));
    if (!data) return;
    await refreshAll(projectId);
    setMessage(`Step ${step} 已重新生成${downstreamSteps.length ? '，后续步骤已清除' : ''}`);
  }

  async function sendHistoryChat(event) {
    event?.preventDefault();
    const chatMessage = historyChatInput.trim();
    if (!chatMessage || !historyActiveStep) return;
    const data = await run('AI 修改', () => request(`/api/projects/${projectId}/history-workflow/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: chatMessage }),
    }));
    if (data) {
      setHistoryChatInput('');
      await refreshAll(projectId);
      setMessage(`Step ${historyActiveStep} 的回答已记录，进入下一步时会作为创作要求`);
    }
  }

  async function finalizeHistoryWorkflow() {
    const data = await run('确认定稿', () => request(
      `/api/projects/${projectId}/history-workflow/finalize`,
      { method: 'POST' },
    ));
    if (data) {
      await refreshAll(projectId);
      setMessage('历史口播稿已定稿，可以保存或生成分镜');
    }
  }

  async function copyHistoryOutput() {
    if (!historyOutput.trim()) return;
    try {
      await writeClipboardText(historyOutput);
      setMessage(`当前阶段结果已复制，共 ${scriptCharacterCount(historyOutput)} 字`);
    } catch (err) {
      setMessage(`复制失败：${err.message}`);
    }
  }

  async function saveScript() {
    const paragraphs = [...rewrittenParagraphs];
    if (editingParagraphIndex >= 0 && paragraphs[editingParagraphIndex] !== undefined && paragraphDraft.trim()) {
      paragraphs[editingParagraphIndex] = paragraphDraft.trim();
    }
    setEditingParagraphIndex(-1);
    setParagraphDraft('');
    rewrittenScriptDirtyRef.current = false;
    await persistParagraphs(paragraphs, '文案已保存');
  }

  async function copyAllImagePrompts() {
    const prompts = [...shots]
      .sort((a, b) => Number(a.shot_index || 0) - Number(b.shot_index || 0))
      .map((shot) => String(shot.image_prompt || '').trim())
      .filter(Boolean);
    if (!prompts.length) {
      setMessage('暂无分镜提示词可复制');
      return;
    }
    const numberedPrompts = formatBatchImagePrompts(prompts);
    try {
      await writeClipboardText(numberedPrompts);
      setMessage(`已复制 ${prompts.length} 条分镜提示词`);
    } catch (err) {
      setMessage(`复制分镜提示词失败：${err.message}`);
    }
  }

  async function persistParagraphs(paragraphs, successMessage = '段落已自动保存') {
    const cleanedParagraphs = paragraphs.map((paragraph) => paragraph.trim()).filter(Boolean);
    if (!cleanedParagraphs.length) {
      setMessage('文案至少需要保留一个段落');
      return false;
    }
    const rewrittenScript = cleanedParagraphs.join('\n\n');
    const saveVersion = paragraphSaveVersionRef.current + 1;
    paragraphSaveVersionRef.current = saveVersion;
    rewrittenScriptDirtyRef.current = false;
    setRewrittenScriptEditor(numberScriptParagraphs(rewrittenScript));
    setMessage('自动保存中...');
    try {
      const saveRequest = paragraphSaveQueueRef.current
        .catch(() => undefined)
        .then(() => request(`/api/projects/${projectId}/script`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rewritten_script: rewrittenScript }),
        }));
      paragraphSaveQueueRef.current = saveRequest;
      const data = await saveRequest;
      if (saveVersion !== paragraphSaveVersionRef.current) return true;
      setProject((current) => current ? {
        ...current,
        rewritten_script: rewrittenScript,
        rewrite_comparison: data.rewrite_comparison || current.rewrite_comparison,
        rewrite_difference: data.rewrite_difference ?? current.rewrite_difference,
      } : current);
      setMessage(successMessage);
      return true;
    } catch (err) {
      if (saveVersion === paragraphSaveVersionRef.current) {
        rewrittenScriptDirtyRef.current = true;
        setMessage(`自动保存失败：${err.message}`);
      }
      return false;
    }
  }

  function beginParagraphEdit(index) {
    setEditingParagraphIndex(index);
    setParagraphDraft(rewrittenParagraphs[index] || '');
  }

  async function finishParagraphEdit(index) {
    if (deletingParagraphRef.current || editingParagraphIndex !== index) return;
    const value = paragraphDraft.trim();
    setEditingParagraphIndex(-1);
    setParagraphDraft('');
    rewrittenScriptDirtyRef.current = false;
    if (!value) {
      setMessage('段落内容不能为空；如需移除请点击删除');
      return;
    }
    if (value === rewrittenParagraphs[index]) return;
    const paragraphs = [...rewrittenParagraphs];
    paragraphs[index] = value;
    await persistParagraphs(paragraphs);
  }

  async function deleteScriptParagraph(event, index) {
    event.stopPropagation();
    if (!window.confirm(`确认删除第 ${index + 1} 段吗？删除后序号会自动重排。`)) return;
    deletingParagraphRef.current = true;
    setEditingParagraphIndex(-1);
    setParagraphDraft('');
    const paragraphs = rewrittenParagraphs.filter((_, paragraphIndex) => paragraphIndex !== index);
    const savePromise = persistParagraphs(paragraphs, '段落已删除并自动重新编号');
    window.setTimeout(() => { deletingParagraphRef.current = false; }, 0);
    await savePromise;
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

  async function generateShots() {
    setTab('storyboard');
    setBusy(true);
    setMessage('生成分镜中...');
    try {
      const paragraphs = [...rewrittenParagraphs];
      if (editingParagraphIndex >= 0 && paragraphs[editingParagraphIndex] !== undefined && paragraphDraft.trim()) {
        paragraphs[editingParagraphIndex] = paragraphDraft.trim();
      }
      await request(`/api/projects/${projectId}/script`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rewritten_script: paragraphs.join('\n\n') }),
      });
      await request(
        `/api/projects/${projectId}/shots?material_source_strategy=${materialSourceStrategy}&storyboard_model_provider=${storyboardModelProvider}&image_generation_provider=${imageGenerationProvider}`,
        { method: 'POST' },
      );
      await refreshAll(projectId);
      setMessage('旧分镜已清空，正在后台重新生成分镜...');
    } catch (err) {
      setMessage(`生成分镜失败：${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function skipToStoryboard() {
    setTab('storyboard');
    setBusy(true);
    setMessage('使用原始文案生成分镜...');
    try {
      await request(`/api/projects/${projectId}/script`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rewritten_script: project.raw_script }),
      });
      await request(
        `/api/projects/${projectId}/shots?material_source_strategy=${materialSourceStrategy}&storyboard_model_provider=${storyboardModelProvider}&image_generation_provider=${imageGenerationProvider}`,
        { method: 'POST' },
      );
      await refreshAll(projectId);
      setMessage('旧分镜已清空，正在后台重新生成分镜...');
    } catch (err) {
      setMessage(`生成分镜失败：${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  function handleUploadPick(ev) {
    const files = Array.from(ev.target.files || []);
    if (!files.length) return;
    setPendingUpload({ files, form: emptyTagForm() });
  }

  function closePendingUpload() {
    setPendingUpload(null);
    if (uploadInputRef.current) uploadInputRef.current.value = '';
  }

  async function confirmUpload(form) {
    if (!pendingUpload?.files?.length) return;
    const data = new FormData();
    pendingUpload.files.forEach((file) => data.append('files', file, file.name));
    data.append('source_page', form.source_page || '');
    data.append('source_note', form.source_note || '用户上传');
    data.append('copyright_note', form.copyright_note || '自用素材');
    data.append('manual_tags', JSON.stringify({
      object: textToList(form.object),
      scene: textToList(form.scene),
      keywords: textToList(form.keywords),
    }));
    data.append('keep_original', String(Boolean(form.keep_original)));
    data.append('video_processing_mode', form.video_processing_mode || 'split');
    const result = await run('上传素材', () => request('/api/assets/upload', { method: 'POST', body: data }));
    if (!result) return;
    closePendingUpload();
    setTab('assets');
    setMessage('素材已上传，正在识别标签');
    await refreshAll(projectId);
  }

  async function saveAssetTags(assetId, payload) {
    const result = await run('保存标签', () => request(`/api/assets/${assetId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }));
    if (payload.sync_remote) {
      setMessage(result?.remote_synced ? '标签已保存并同步远程素材库' : '标签已保存，远程素材库未同步');
    }
    setEditingAsset(null);
    await refreshAll(projectId);
  }

  async function deleteAsset(asset) {
    if (!window.confirm(`确认删除素材「${asset.file_name}」吗？`)) return;
    await run('删除素材', () => request(`/api/assets/${asset.id}`, { method: 'DELETE' }));
    if (editingAsset?.id === asset.id) setEditingAsset(null);
    setSelectedAssetIds((prev) => { const next = new Set(prev); next.delete(asset.id); return next; });
    await refreshAll(projectId);
  }

  async function batchDeleteAssets() {
    const ids = [...selectedAssetIds];
    if (!ids.length) return;
    if (!window.confirm(`确认批量删除 ${ids.length} 个素材吗？`)) return;
    const result = await run('批量删除', () => request('/api/assets/batch-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_ids: ids }),
    }));
    if (!result) return;
    setSelectedAssetIds(new Set());
    if (editingAsset && ids.includes(editingAsset.id)) setEditingAsset(null);
    await refreshAll(projectId);
    setMessage(`已删除 ${result.deleted_count} 个素材（${result.deleted_files} 个文件）`);
  }

  async function retryAssetAnalysis() {
    const result = await run('重试标签识别', () => request('/api/assets/retry-analysis', {
      method: 'POST',
    }));
    if (!result) return;
    setMessage(result.queued ? `已重新提交 ${result.queued} 张图片进行标签识别` : '没有需要重试的图片');
    await refreshAll(projectId);
  }

  async function syncRemoteAssets() {
    const result = await run('拉取远程素材', () => request('/api/assets/sync-remote', { method: 'POST' }));
    if (!result) return;
    await refreshAll(projectId);
    setMessage(`远程同步完成：新增 ${result.added} 条，更新 ${result.updated} 条，共发现 ${result.remote_count} 条素材`);
  }

  function toggleAssetSelect(assetId) {
    setSelectedAssetIds((prev) => {
      const next = new Set(prev);
      if (next.has(assetId)) next.delete(assetId);
      else next.add(assetId);
      return next;
    });
  }

  function toggleSelectAllAssets() {
    if (selectedAssetIds.size === assets.length) {
      setSelectedAssetIds(new Set());
    } else {
      setSelectedAssetIds(new Set(assets.map((a) => a.id)));
    }
  }

  async function deleteProject() {
    if (!projectId || !project) return;
    if (!window.confirm(`确认删除项目「${project.name}」吗？项目文案、分镜、生成文件和导出包都会删除。`)) return;
    const deletedId = projectId;
    const result = await run('删除项目', () => request(`/api/projects/${deletedId}`, { method: 'DELETE' }));
    if (!result) return;
    setTab('create');
    await refreshAll('');
  }

  async function openImagePromptEditor(shotId) {
    if (imagePromptEditors[shotId] !== undefined) return;
    const result = await run('读取图片提示词', () => request(
      `/api/projects/${projectId}/shots/${shotId}/image-prompt`,
    ));
    if (!result) return;
    setImagePromptEditors((current) => ({ ...current, [shotId]: result.prompt || '' }));
  }

  function updateImagePrompt(shotId, prompt) {
    setImagePromptEditors((current) => ({ ...current, [shotId]: prompt }));
  }

  function closeImagePromptEditor(shotId) {
    setImagePromptEditors((current) => {
      const next = { ...current };
      delete next[shotId];
      return next;
    });
  }

  async function generateImage(shotId, prompt) {
    if (!String(prompt || '').trim()) {
      setMessage('图片提示词不能为空');
      return;
    }
    setGeneratingShotIds((current) => new Set(current).add(shotId));
    setMessage('AI 图片生成中，可继续生成其他分镜或重新识别提示词');
    try {
      const result = await request(
        `/api/projects/${projectId}/shots/${shotId}/generate-image`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: String(prompt).trim(),
            provider: imageGenerationProvider,
          }),
        },
      );
      closeImagePromptEditor(shotId);
      await refreshAll(projectId);
      setMessage(`镜头 ${result.shot_index || ''} AI 图片生成完成`.replace('镜头  AI', 'AI'));
    } catch (err) {
      setMessage(`AI 图片生成失败：${err.message}`);
    } finally {
      setGeneratingShotIds((current) => {
        const next = new Set(current);
        next.delete(shotId);
        return next;
      });
    }
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
    if (!window.confirm(`确认删除项目「${item.name}」吗？项目文案、分镜、生成文件和导出包都会删除。`)) return;
    const result = await run('删除项目', () => request(`/api/projects/${item.id}`, { method: 'DELETE' }));
    if (!result) return;
    if (item.id === projectId) {
      setTab('create');
      await refreshAll('');
    } else {
      await refreshAll(projectId);
    }
  }

  async function regenerateShotImagePrompt(shotId) {
    setRecognizingShotIds((current) => new Set(current).add(shotId));
    setMessage('正在重新生成图片提示词，可继续处理其他分镜');
    try {
      const result = await request(
        `/api/projects/${projectId}/shots/${shotId}/regenerate-image-prompt?storyboard_model_provider=${storyboardModelProvider}`,
        { method: 'POST' },
      );
      closeImagePromptEditor(shotId);
      setShots((current) => current.map((shot) => (shot.id === shotId ? result.shot : shot)));
      setMessage(`镜头 ${result.shot.shot_index} 的 AI 图片提示词已重新生成`);
    } catch (err) {
      setMessage(`重新生成提示词失败：${err.message}`);
    } finally {
      setRecognizingShotIds((current) => {
        const next = new Set(current);
        next.delete(shotId);
        return next;
      });
    }
  }

  async function updateShotVoiceText(shotId, voiceText) {
    const text = String(voiceText || '').trim();
    if (!text) {
      setMessage('镜头文案不能为空');
      return false;
    }
    const result = await run('保存镜头文案', () => request(`/api/projects/${projectId}/shots/${shotId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_text: text }),
    }));
    if (!result) return false;
    setShots((current) => current.map((shot) => (shot.id === shotId ? result.shot : shot)));
    setMessage(`镜头 ${result.shot.shot_index} 文案已保存；如画面内容变化，请重新生成图片提示词`);
    return true;
  }

  async function processGeneratedImage(assetId, operation, { silent = false } = {}) {
    const processingKey = `${assetId}:${operation}`;
    const label = {
      grayscale: '转为黑白照片',
      'remove-watermark': 'Seedream 去水印',
    }[operation] || '处理图片';

    if (operation === 'grayscale') {
      setGrayscaleProcessingIds((current) => new Set(current).add(assetId));
      if (!silent) setMessage('黑白转换中，可继续转换其他分镜图片');
      const startedAt = Date.now();
      try {
        const result = await request(
          `/api/projects/${projectId}/generated-assets/${assetId}/${operation}`,
          { method: 'POST' },
        );
        const remaining = 700 - (Date.now() - startedAt);
        if (remaining > 0) await new Promise((resolve) => window.setTimeout(resolve, remaining));
        setGeneratedAssets((current) => current.map((asset) => (
          asset.id === assetId ? result.asset : asset
        )));
        if (!silent) setMessage('黑白照片转换完成');
        return true;
      } catch (err) {
        if (!silent) setMessage(`黑白照片转换失败：${err.message}`);
        return false;
      } finally {
        setGrayscaleProcessingIds((current) => {
          const next = new Set(current);
          next.delete(assetId);
          return next;
        });
      }
    }

    setProcessingImage(processingKey);
    try {
      await run(label, () => request(
        `/api/projects/${projectId}/generated-assets/${assetId}/${operation}`,
        { method: 'POST' },
      ));
      await refreshAll(projectId);
    } finally {
      setProcessingImage('');
    }
  }

  async function grayscaleAllStoryboardImages() {
    const pendingAssets = generatedAssets.filter((asset) => (
      asset.project_id === projectId
      && !asset.is_grayscale
      && !grayscaleProcessingIds.has(asset.id)
    ));
    if (!pendingAssets.length) {
      setMessage('所有分镜图片都已是黑白照片');
      return;
    }

    setMessage(`正在同时转换 ${pendingAssets.length} 张分镜图片为黑白照片`);
    const results = await Promise.all(pendingAssets.map((asset) => (
      processGeneratedImage(asset.id, 'grayscale', { silent: true })
    )));
    const succeeded = results.filter(Boolean).length;
    const failed = results.length - succeeded;
    setMessage(failed
      ? `一键黑白完成：成功 ${succeeded} 张，失败 ${failed} 张`
      : `一键黑白完成：${succeeded} 张分镜图片已转为黑白照片`);
  }

  async function uploadManualShotImage(shotId, file) {
    const data = new FormData();
    data.append('file', file, file.name);
    await run('上传镜头图片', () => request(
      `/api/projects/${projectId}/shots/${shotId}/manual-image`,
      { method: 'POST', body: data },
    ));
    await refreshAll(projectId);
  }

  async function archiveSelectedImages() {
    const result = await run('批量存入素材库', () => request(
      `/api/projects/${projectId}/archive-selected-images`,
      { method: 'POST' },
    ));
    if (!result) return;
    setMessage(`素材入库：新增 ${result.created}，二次识别 ${result.analyzing || 0}，跳过重复 ${result.skipped_duplicates}，无图片 ${result.missing}`);
    await refreshAll(projectId);
  }

  async function selectAsset(shotId, assetId, assetSource = 'ai_generated') {
    const result = await run('指定素材', () => request(`/api/projects/${projectId}/shots/${shotId}/asset`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: assetId, asset_source: assetSource }),
    }));
    if (!result) return false;
    await refreshAll(projectId);
    return true;
  }

  async function uploadShotImageFromLibrary(assetId) {
    if (!libraryPickerShotId) return;
    const selected = await selectAsset(libraryPickerShotId, assetId, 'library_upload');
    if (selected) setLibraryPickerShotId('');
  }

  async function generateVoiceAndSubtitles() {
    const voiceResult = await run('生成配音', () => request(`/api/projects/${projectId}/generate-voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_type: voiceType, speech_rate: speechRate }),
    }));
    if (!voiceResult) return false;
    const subtitleResult = await run('生成字幕', () => request(`/api/projects/${projectId}/generate-subtitles`, { method: 'POST' }));
    if (!subtitleResult) return false;
    playGeneratedVoiceAfterLoadRef.current = true;
    await refreshAll(projectId);
    setVoiceAudioVersion(Date.now());
    setMessage('配音与字幕已生成，可直接试听；不满意可以重新生成');
    return true;
  }

  async function previewVoice() {
    playVoicePreviewAfterLoadRef.current = true;
    setPreviewingVoice(true);
    setMessage('正在生成音色试听...');
    try {
      const response = await fetch(`${API}/api/projects/${projectId}/voice-preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_type: voiceType, speech_rate: speechRate }),
      });
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

  async function generateMusicVoiceAndSubtitles(musicId = backgroundMusicId, startSec = backgroundMusicStart) {
    if (!musicId) {
      setMessage('请先上传或选择一首音乐');
      return false;
    }
    const musicResult = await run('生成音乐歌词字幕', () => request(`/api/projects/${projectId}/generate-music-voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ music_id: musicId, start_sec: Number(startSec) || 0 }),
    }));
    if (!musicResult) return false;
    const subtitleResult = await run('生成字幕', () => request(`/api/projects/${projectId}/generate-subtitles`, { method: 'POST' }));
    if (!subtitleResult) return false;
    await refreshAll(projectId);
    setMessage(musicResult.warning
      ? `音乐字幕已生成，但 AI 对齐失败，已按时长临时匹配：${musicResult.warning}`
      : `音乐已作为主音轨，识别 ${musicResult.line_count || 0} 行歌词并生成字幕`);
    setTab('cover');
    return true;
  }

  async function uploadMusicVoice(file) {
    if (!file) return;
    const data = new FormData();
    data.append('file', file, file.name);
    const result = await run('上传音乐', () => request('/api/assets/music', {
      method: 'POST',
      body: data,
    }));
    if (!result?.music) return;
    const musicData = await request('/api/assets/music');
    setMusicLibrary(musicData.music || []);
    await generateMusicVoiceAndSubtitles(result.music.id, 0);
  }

  async function generateCover(options = {}) {
    if (!coverImage && !project?.cover_source_url) {
      setMessage('请先上传一张人物图片');
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
    data.append('title_positions', JSON.stringify(coverTitlePositions));
    data.append('mask_opacity', String(coverMaskOpacity / 100));
    const result = await run('生成视频封面', () => request(`/api/projects/${projectId}/generate-cover`, {
      method: 'POST',
      body: data,
    }));
    if (!result) return;
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

  async function uploadBackgroundMusic(file) {
    if (!file) return;
    const data = new FormData();
    data.append('file', file, file.name);
    const result = await run('上传背景音乐', () => request('/api/assets/music', {
      method: 'POST',
      body: data,
    }));
    if (!result?.music) return;
    const musicData = await request('/api/assets/music');
    setMusicLibrary(musicData.music || []);
    setBackgroundMusicId(result.music.id);
    setBackgroundMusicStart(0);
    await saveMusicSettings({
      musicId: result.music.id,
      startSec: 0,
      volumePercent: backgroundMusicVolume,
    });
  }

  async function deleteBackgroundMusic() {
    if (!backgroundMusicId) return;
    const selectedMusic = musicLibrary.find((music) => music.id === backgroundMusicId);
    if (!selectedMusic) return;
    if (!window.confirm(`确认删除音乐「${selectedMusic.name}」吗？正在使用它的项目会清空配乐设置。`)) return;
    const result = await run('删除背景音乐', () => request(`/api/assets/music/${backgroundMusicId}`, {
      method: 'DELETE',
    }));
    if (!result) return;
    const musicData = await request('/api/assets/music');
    setMusicLibrary(musicData.music || []);
    setBackgroundMusicId('');
    setBackgroundMusicStart(0);
    await refreshProject(projectId);
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
    if (!options.silent) setMessage('标题已确认，可以生成封面');
    return true;
  }

  async function generatePublishAssistant() {
    const result = await run('生成发布助手', () => request(`/api/projects/${projectId}/generate-publish-assistant`, {
      method: 'POST',
    }));
    if (!result) return;
    setPublishShortTitle(result.short_title || '');
    setPublishDescription(result.description || '');
    await refreshAll(projectId);
    setMessage('发布助手已生成，可以直接复制发布');
  }

  async function exportPackage() {
    if (!project?.audio_url || !project?.voice_timeline_url) {
      const generated = await generateVoiceAndSubtitles();
      if (!generated) return;
    }
    const data = await run('导出剪映草稿', () => request(
      `/api/projects/${projectId}/export/assets`,
      { method: 'POST' },
    ));
    if (!data) return;
    setExportResult(data);
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

  const workflowBusy = busy || ['generating_shots', 'generating_images'].includes(project?.status);
  const workflowMessage = (() => {
    if (project?.status === 'shot_generation_failed') {
      return project.generation_error ? `处理失败：${project.generation_error}` : '分镜生成失败';
    }
    if (!['generating_shots', 'generating_images'].includes(project?.status)) return message || '就绪';
    return project.current_generation_message
      || (project.status === 'generating_shots' ? '正在生成分镜提示词...' : '正在使用 AI 生成分镜图片...');
  })();

  function toggleSidebar() {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next));
      if (next) setProjectMenuOpen(false);
      return next;
    });
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
        <div className="brand"><Film size={22} /><span className="brand-label">草稿生成器</span></div>
        <nav>
          <button title="项目" className={tab === 'create' ? 'active' : ''} onClick={() => setTab('create')}><Scissors size={18} /><span>项目</span></button>
          <button title="素材库" className={tab === 'assets' ? 'active' : ''} onClick={() => setTab('assets')}><Library size={18} /><span>素材库</span></button>
          <button title="文案" className={tab === 'script' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('script')}><Wand2 size={18} /><span>文案</span></button>
          <button title="分镜" className={tab === 'storyboard' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('storyboard')}><Archive size={18} /><span>分镜</span></button>
          <button title="配音" className={tab === 'match' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('match')}><Mic size={18} /><span>配音</span></button>
          <button title="标题" className={tab === 'cover' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('cover')}><Music size={18} /><span>标题</span></button>
          <button title="导出" className={tab === 'export' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('export')}><Download size={18} /><span>导出</span></button>
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
          />
          <button className="danger icon-only" disabled={!projectId} title="删除项目" onClick={deleteProject}><Trash2 size={18} /></button>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <h1>{project?.name || '真实素材优先的短视频草稿工作台'}</h1>
            <p>{activeLibrary ? `当前素材库：${activeLibrary.name}` : '进入素材库前，需要先选择一个文件夹作为素材库。'}</p>
          </div>
          <div className="top-actions">
            {project && (
              <div className="project-name-editor">
                <input
                  value={projectNameDraft}
                  onChange={(event) => setProjectNameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') saveProjectName();
                    if (event.key === 'Escape') setProjectNameDraft(project.name || '');
                  }}
                  aria-label="项目名称"
                  maxLength="80"
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

        {tab === 'create' && (
          <section className="band project-workspace">
            <form onSubmit={createProject} className="create-project-layout">
              <div className="panel create-options-panel">
                <h2>创建历史向视频</h2>
                <label>项目名称<input name="name" placeholder="可留空，系统自动取标题" /></label>
                <div className="history-create-guide">
                  <strong>三步历史创作流程</strong>
                  <div><span>1</span><p><b>策略分析</b>提炼爆款逻辑、切入视角和开场方案</p></div>
                  <div><span>2</span><p><b>正文创作</b>按确认后的策略生成历史口播正文</p></div>
                  <div><span>3</span><p><b>终审定稿</b>校验史实、原创度和口语流畅度</p></div>
                  <small>每一步都可以与 AI 聊天修改，确认后才会进入下一步。</small>
                </div>
              </div>

              <div className="panel create-script-panel">
                <div className="script-title script-panel-heading">
                  <h2>历史参考文案</h2>
                  <span className="script-character-count">{scriptCharacterCount(rawScriptDraft)} 字</span>
                </div>
                <textarea
                  className="create-script-textarea"
                  name="raw_script"
                  required
                  value={rawScriptDraft}
                  onChange={(event) => setRawScriptDraft(event.target.value)}
                  placeholder="粘贴需要重构的历史人物、历史事件或纪实解说参考文案"
                  aria-label="历史参考文案"
                />
                <div className="script-create-actions">
                  <button className="primary" disabled={!rawScriptDraft.trim()}><Save size={18} /> 创建并进入三步创作</button>
                </div>
              </div>
            </form>
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
            />
          </section>
        )}

        {tab === 'assets' && (
          <section className="band">
            {!activeLibrary ? (
              <div className="library-gate">
                <div className="library-icon"><FolderOpen size={42} /></div>
                <h2>选择素材库文件夹</h2>
                <p>选择后，系统会把这个文件夹登记为当前素材库。后续上传入口只保留一个，上传后的图片会自动打标签并放入素材库。</p>
                <button className="primary" onClick={chooseLibraryFolder}><FolderOpen size={18} /> 选择文件夹</button>
                <input ref={folderFallbackRef} className="hidden-input" type="file" webkitdirectory="" directory="" onChange={chooseLibraryFallback} />
              </div>
            ) : (
              <>
                <div className="library-bar">
                  <div>
                    <strong>{activeLibrary.name}</strong>
                    <span>{activeLibrary.path_hint}</span>
                  </div>
                  <button onClick={chooseLibraryFolder}><FolderOpen size={18} /> 更换素材库</button>
                  <input ref={folderFallbackRef} className="hidden-input" type="file" webkitdirectory="" directory="" onChange={chooseLibraryFallback} />
                </div>
                <div className="toolbar single-upload">
                  <input ref={uploadInputRef} name="files" type="file" accept=".jpg,.jpeg,.png,.webp,.mp4,.mov" multiple onChange={handleUploadPick} />
                  <span>{pendingUpload ? `已选择 ${pendingUpload.files.length} 个文件` : '选择图片或视频后先设置标签'}</span>
                  <div className="asset-upload-actions">
                    <button type="button" onClick={syncRemoteAssets}><Download size={18} /> 拉取远程素材</button>
                    <button type="button" onClick={retryAssetAnalysis}><RefreshCw size={18} /> 重试标签识别</button>
                    <button className="primary" type="button" onClick={() => uploadInputRef.current?.click()}><ImagePlus size={18} /> 选择素材</button>
                  </div>
                </div>
                <div className="asset-type-filter">
                  <button className={assetTypeFilter === 'all' ? 'active' : ''} onClick={() => { setAssetTypeFilter('all'); setVisibleAssetCount(ASSET_PAGE_SIZE); }}>全部</button>
                  <button className={assetTypeFilter === 'image' ? 'active' : ''} onClick={() => { setAssetTypeFilter('image'); setVisibleAssetCount(ASSET_PAGE_SIZE); }}>图片</button>
                  <button className={assetTypeFilter === 'video' ? 'active' : ''} onClick={() => { setAssetTypeFilter('video'); setVisibleAssetCount(ASSET_PAGE_SIZE); }}>视频</button>
                </div>
                {assets.length > 0 && (
                  <div className="batch-bar">
                    <label className="check-row compact">
                      <input type="checkbox" checked={selectedAssetIds.size === assets.length && assets.length > 0} onChange={toggleSelectAllAssets} />
                      全选 ({assets.length})
                    </label>
                    <div className={selectedAssetIds.size > 0 ? 'batch-selection-actions visible' : 'batch-selection-actions'}>
                        <span className="batch-count">已选 {selectedAssetIds.size} 项</span>
                        <button className="danger compact-button" onClick={batchDeleteAssets}><Trash2 size={16} /> 批量删除</button>
                        <button className="compact-button" onClick={() => setSelectedAssetIds(new Set())}>取消选择</button>
                    </div>
                  </div>
                )}
                <div className="asset-grid">
                  {visibleAssets.map((asset) => (
                    <AssetCard
                      key={asset.id}
                      asset={asset}
                      libraryCard
                      selected={selectedAssetIds.has(asset.id)}
                      onSelect={() => toggleAssetSelect(asset.id)}
                      onPreview={() => setPreviewAsset(asset)}
                      onEdit={() => setEditingAsset(asset)}
                      onDelete={() => deleteAsset(asset)}
                    />
                  ))}
                </div>
                {visibleAssetCount < filteredAssets.length && (
                  <div className="load-more-row">
                    <button
                      type="button"
                      onClick={() => setVisibleAssetCount((count) => Math.min(count + ASSET_PAGE_SIZE, filteredAssets.length))}
                    >
                      加载更多（{Math.min(ASSET_PAGE_SIZE, filteredAssets.length - visibleAssetCount)} / 剩余 {filteredAssets.length - visibleAssetCount}）
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        )}

        {tab === 'script' && project && (
          <section className="band history-script-grid">
            <div className="panel">
              <div className="script-title script-panel-heading">
                <h2>历史参考文案</h2>
                <span className="script-character-count">{scriptCharacterCount(project.raw_script)} 字</span>
              </div>
              <textarea
                className="script-editor-surface raw-script-textarea"
                readOnly
                value={project.raw_script}
                rows="18"
              />
              <div className="script-panel-footer">
              <label className="source-strategy">
                分镜生成方式
                <select value={materialSourceStrategy} onChange={(event) => setMaterialSourceStrategy(event.target.value)}>
                  <option value="ai_only">仅使用 AI 生成</option>
                  <option value="prompt_only">仅生成 AI 图片提示词</option>
                </select>
              </label>
              <label className="source-strategy">
                提示词模型
                <select value={storyboardModelProvider} onChange={(event) => setStoryboardModelProvider(event.target.value)}>
                  <option value="deepseek">DeepSeek</option>
                  <option value="minimax">MiniMax</option>
                  <option value="openai">OpenAI</option>
                </select>
              </label>
              {materialSourceStrategy === 'ai_only' && (
                <label className="source-strategy">
                  出图模型
                  <select value={imageGenerationProvider} onChange={(event) => setImageGenerationProvider(event.target.value)}>
                    <option value="seedream">Seedream</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </label>
              )}
              <div className="actions raw-script-actions">
                <button
                  className="primary"
                  title={materialSourceStrategy === 'prompt_only'
                    ? '使用原始文案生成分镜和图片提示词，不生成画面'
                    : '使用原始文案生成分镜，并为每个镜头生成 AI 图片'}
                  onClick={skipToStoryboard}
                >
                  <Wand2 size={18} /> {materialSourceStrategy === 'prompt_only' ? '生成分镜提示词' : 'AI 生成分镜'}
                </button>
              </div>
              <small className="raw-script-hint">也可以跳过历史创作，直接使用参考文案生成分镜</small>
              </div>
            </div>
            <div className="panel history-output-panel">
              <div className="history-workflow-heading">
                <div>
                  <h2>历史创作工作台</h2>
                  <small>{historyActiveStep ? `当前 Step ${historyActiveStep}` : '点击“改写”启动 Step 1'}</small>
                </div>
                <button type="button" onClick={copyHistoryOutput} disabled={!historyOutput}>
                  <Copy size={15} /> 复制
                </button>
              </div>
              <div className="history-book-control">
                <label>
                  本次视频带书
                  <div className="history-book-select-row">
                    <select
                      value={historyBookTitle}
                      onChange={(event) => handleHistoryBookSelect(event.target.value)}
                      disabled={busy}
                      aria-label="选择本次视频推广书籍"
                    >
                      {promotionBooks.map((title) => (
                        <option key={title} value={title}>{title}</option>
                      ))}
                      <option value={ADD_PROMOTION_BOOK_OPTION}>＋ 新增图书…</option>
                    </select>
                    <button
                      type="button"
                      className="danger icon-only"
                      onClick={deletePromotionBook}
                      disabled={busy || promotionBooks.length <= 1}
                      title="删除当前书籍"
                      aria-label={`删除 ${historyBookTitle}`}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </label>
                <label>
                  创作模型
                  <select
                    value={historyModelProvider}
                    onChange={(event) => selectHistoryModel(event.target.value)}
                    disabled={busy}
                    aria-label="选择历史文案创作模型"
                  >
                    <option value="minimax">MiniMax</option>
                    <option value="deepseek">DeepSeek</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </label>
                <small>
                  Step 1、Step 2、Step 3 和阶段问答都会使用所选模型；切换书籍会重置进度。
                </small>
              </div>
              <div className="history-stepper" aria-label="历史创作进度">
                {[
                  [1, '策略分析'],
                  [2, '正文创作'],
                  [3, '终审定稿'],
                ].map(([step, label]) => (
                  <div
                    key={step}
                    className={`${historyActiveStep === step ? 'active' : ''} ${historyActiveStep > step || historyWorkflow.status === 'completed' ? 'done' : ''}`}
                  >
                    <span>{historyActiveStep > step || historyWorkflow.status === 'completed' ? <Check size={14} /> : step}</span>
                    <strong>{label}</strong>
                    {historyWorkflow.outputs?.[String(step)] && (
                      <button
                        type="button"
                        className="history-step-regenerate"
                        onClick={() => regenerateHistoryStep(step)}
                        disabled={busy}
                        title={`重新生成 Step ${step}`}
                        aria-label={`重新生成 Step ${step} ${label}`}
                      >
                        <RefreshCw size={13} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              <div className="history-stage-output" aria-live="polite">
                {historyOutput ? (
                  <MarkdownContent>{historyOutput}</MarkdownContent>
                ) : (
                  <div className="history-empty-state">
                    <Wand2 size={30} />
                    <strong>从历史策略分析开始</strong>
                    <span>系统会严格停在每一步，只有你确认后才会继续。</span>
                  </div>
                )}
              </div>
              {stepTwoComparison && (
                <details className="history-draft-comparison" open={historyActiveStep === 2}>
                  <summary>Step 2 首稿 / 优化稿对照</summary>
                  <div className="history-draft-verdict">
                    <strong>评审选择：{stepTwoComparison.winner === 'draft_2' ? '优化稿' : '首稿'}</strong>
                    <span>{stepTwoComparison.reason || '未提供选择原因'}</span>
                    <small>
                      {stepTwoComparison.local_edit_applied
                        ? `已执行 ${stepTwoComparison.required_edits?.length || 0} 项局部修改`
                        : stepTwoComparison.local_edit_rejected
                          ? '局部修改改动过大，已自动回退胜出稿'
                          : '胜出稿未再修改'}
                    </small>
                  </div>
                  <div className="history-draft-grid">
                    {[
                      ['首稿', stepTwoComparison.draft_1, stepTwoComparison.draft_1_metrics],
                      ['优化稿', stepTwoComparison.draft_2, stepTwoComparison.draft_2_metrics],
                    ].map(([label, draft, metrics]) => (
                      <section key={label}>
                        <h3>{label}</h3>
                        <div className="history-draft-metrics">
                          <span>字符数 <b>{metrics?.character_count ?? '—'}</b></span>
                          <span>重复句比例 <b>{metrics ? `${(Number(metrics.repeated_sentence_ratio || 0) * 100).toFixed(1)}%` : '—'}</b></span>
                          <span>书籍介绍 <b>{metrics?.book_introduction_length ?? '—'} 字</b></span>
                        </div>
                        <div className="history-draft-text"><MarkdownContent>{String(draft || '')}</MarkdownContent></div>
                      </section>
                    ))}
                  </div>
                  {!!stepTwoComparison.required_edits?.length && (
                    <div className="history-required-edits">
                      <strong>必要局部修改</strong>
                      <ul>{stepTwoComparison.required_edits.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  )}
                </details>
              )}
              <div className="history-workflow-actions">
                {!historyActiveStep ? (
                  <button type="button" onClick={rewrite} disabled={busy}>
                    <Wand2 size={16} /> 改写
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={rewrite}
                    disabled={busy}
                    title="清空当前三步创作进度并从 Step 1 重新生成"
                    aria-label="从头重新生成二创文案"
                  >
                    <RefreshCw size={16} /> 重新生成
                  </button>
                )}
                {historyActiveStep > 0 && historyActiveStep < 3 && (
                  <button type="button" className="primary" onClick={runNextHistoryStep} disabled={busy}>
                    <Check size={16} /> 确认回答并进入 Step {historyActiveStep + 1}
                  </button>
                )}
                {historyActiveStep === 3 && historyWorkflow.status !== 'completed' && (
                  <button type="button" className="primary" onClick={finalizeHistoryWorkflow} disabled={busy}>
                    <Check size={16} /> 确认定稿
                  </button>
                )}
                {historyWorkflow.status === 'completed' && (
                  <span className="history-completed"><Check size={15} /> 已定稿</span>
                )}
              </div>
            </div>
            <div className="panel history-chat-panel">
              <div className="history-chat-heading">
                <MessageSquare size={19} />
                <div>
                  <h2>AI 阶段问答助手</h2>
                  <small>{historyActiveStep ? `回答 Step ${historyActiveStep} 末尾的问题，意见将在下一步落实` : '生成任一步结果后即可回答'}</small>
                </div>
              </div>
              <div className="history-chat-messages">
                {historyMessages.length ? historyMessages.map((item, index) => (
                  <div className={`history-chat-message ${item.role}`} key={`${index}-${item.role}`}>
                    <span>{item.role === 'user' ? '你' : 'AI'}</span>
                    <MarkdownContent className="history-chat-markdown">{item.content}</MarkdownContent>
                  </div>
                )) : (
                  <div className="history-chat-placeholder">
                    请回答左侧当前阶段最后提出的问题。例如：选择第二个开场；节奏满意，但希望设问更深入。
                  </div>
                )}
              </div>
              <form className="history-chat-form" onSubmit={sendHistoryChat}>
                <textarea
                  value={historyChatInput}
                  disabled={!historyActiveStep || busy}
                  onChange={(event) => setHistoryChatInput(event.target.value)}
                  placeholder={historyActiveStep ? '输入你对当前阶段问题的回答…' : '请先点击“改写”执行 Step 1'}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      sendHistoryChat(event);
                    }
                  }}
                />
                <button type="submit" className="primary" disabled={!historyChatInput.trim() || !historyActiveStep || busy}>
                  <Send size={17} /> 发送回答
                </button>
              </form>
              {historyWorkflow.status === 'completed' && (
                <div className="history-final-actions">
                  <label className="source-strategy">
                    分镜生成方式
                    <select value={materialSourceStrategy} onChange={(event) => setMaterialSourceStrategy(event.target.value)}>
                      <option value="ai_only">仅使用 AI 生成</option>
                      <option value="prompt_only">仅生成 AI 图片提示词</option>
                    </select>
                  </label>
                  <label className="source-strategy">
                    提示词模型
                    <select value={storyboardModelProvider} onChange={(event) => setStoryboardModelProvider(event.target.value)}>
                      <option value="deepseek">DeepSeek</option>
                      <option value="minimax">MiniMax</option>
                      <option value="openai">OpenAI</option>
                    </select>
                  </label>
                  {materialSourceStrategy === 'ai_only' && (
                    <label className="source-strategy">
                      出图模型
                      <select value={imageGenerationProvider} onChange={(event) => setImageGenerationProvider(event.target.value)}>
                        <option value="seedream">Seedream</option>
                        <option value="openai">OpenAI</option>
                      </select>
                    </label>
                  )}
                  <button type="button" className="primary" onClick={() => generateShots()}>
                    <Archive size={17} /> 生成分镜
                  </button>
                </div>
              )}
            </div>
          </section>
        )}

        {tab === 'storyboard' && (
          <section className="band">
            <div className="storyboard-actions">
              <div className="storyboard-action-buttons">
                <label className="storyboard-model-picker">
                  出图模型
                  <select value={imageGenerationProvider} onChange={(event) => setImageGenerationProvider(event.target.value)}>
                    <option value="seedream">Seedream</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </label>
                <label className="storyboard-model-picker">
                  提示词模型
                  <select value={storyboardModelProvider} onChange={(event) => setStoryboardModelProvider(event.target.value)}>
                    <option value="deepseek">DeepSeek</option>
                    <option value="minimax">MiniMax</option>
                    <option value="openai">OpenAI</option>
                  </select>
                </label>
                <button
                  type="button"
                  disabled={!shots.some((shot) => String(shot.image_prompt || '').trim())}
                  onClick={copyAllImagePrompts}
                >
                  <Copy size={18} /> 一键复制全部提示词
                </button>
                <button
                  type="button"
                  disabled={Boolean(processingImage) || !generatedAssets.some((asset) => (
                    asset.project_id === projectId
                    && !asset.is_grayscale
                    && !grayscaleProcessingIds.has(asset.id)
                  ))}
                  onClick={grayscaleAllStoryboardImages}
                >
                  <Film size={18} /> 一键黑白
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={!activeLibrary}
                  title={activeLibrary ? '保存当前分镜选图到素材库' : '请先在素材库页面选择文件夹'}
                  onClick={archiveSelectedImages}
                >
                  <Archive size={18} /> 批量存入素材库并打标签
                </button>
              </div>
              <small>优先保存已选图片；未选择时保存该镜头第一张，重复图片自动跳过。</small>
            </div>
            <StoryboardProgress
              progress={generationProgress}
              project={project}
              imageGenerationProvider={imageGenerationProvider}
            />
            <div className="shot-list">
              {shots.map((shot) => (
                <ShotCard
                  key={shot.id}
                  shot={shot}
                  assets={generatedAssetsByShot.get(shot.id) || []}
                  selectedAssetId={shot.selected_asset_id}
                  project={project}
                  imageGenerationProvider={imageGenerationProvider}
                  onSelect={(assetId, assetSource) => selectAsset(shot.id, assetId, assetSource)}
                  onPreview={setPreviewAsset}
                  imagePrompt={imagePromptEditors[shot.id]}
                  savedImagePrompt={shot.image_prompt}
                  onOpenImagePrompt={() => openImagePromptEditor(shot.id)}
                  onImagePromptChange={(prompt) => updateImagePrompt(shot.id, prompt)}
                  onCancelImagePrompt={() => closeImagePromptEditor(shot.id)}
                  onGenerate={(prompt) => generateImage(shot.id, prompt)}
                  onReanalyzeImage={() => regenerateShotImagePrompt(shot.id)}
                  isRecognizingImage={recognizingShotIds.has(shot.id)}
                  onUpdateVoiceText={(text) => updateShotVoiceText(shot.id, text)}
                  processingImage={processingImage}
                  grayscaleProcessingIds={grayscaleProcessingIds}
                  generatingShotIds={generatingShotIds}
                  onRemoveWatermark={(assetId) => processGeneratedImage(assetId, 'remove-watermark')}
                  onGrayscale={(assetId) => processGeneratedImage(assetId, 'grayscale')}
                  onManualUpload={(file) => uploadManualShotImage(shot.id, file)}
                  onLibraryUpload={() => setLibraryPickerShotId(shot.id)}
                />
              ))}
            </div>
          </section>
        )}

        {tab === 'match' && (
          <section className="band">
            <div className="toolbar voice-toolbar">
              <input
                ref={musicVoiceInputRef}
                className="hidden-input"
                type="file"
                accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,audio/*"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) uploadMusicVoice(file);
                  event.target.value = '';
                }}
              />
              <VoiceSelect value={voiceType} onChange={setVoiceType} />
              <SpeechRateSelect value={speechRate} onChange={setSpeechRate} />
              <button type="button" onClick={previewVoice} disabled={previewingVoice}>
                <Mic size={18} /> {previewingVoice ? '试听生成中…' : '试听音色'}
              </button>
              {voicePreviewUrl && <audio ref={voicePreviewRef} className="voice-preview-player" controls src={voicePreviewUrl} />}
              <button type="button" onClick={() => musicVoiceInputRef.current?.click()}>
                <Music size={18} /> 上传音乐生成歌词字幕
              </button>
              <button
                type="button"
                disabled={!backgroundMusicId || busy}
                onClick={() => generateMusicVoiceAndSubtitles()}
              >
                <Music size={18} /> 用音乐库生成歌词字幕
              </button>
              <button className="primary" onClick={generateVoiceAndSubtitles} disabled={busy}>
                <Mic size={18} /> {project?.audio_url ? '重新生成配音与字幕' : '生成配音与字幕'}
              </button>
            </div>
            {project?.audio_url && (
              <div className="generated-voice-preview">
                <div>
                  <strong>完整配音试听</strong>
                  <small>不满意可调整音色或语速后重新生成。</small>
                </div>
                <audio
                  key={`${project.audio_url}-${voiceAudioVersion}`}
                  ref={generatedVoicePlayerRef}
                  controls
                  preload="metadata"
                  src={`${API}${project.audio_url}?v=${voiceAudioVersion || encodeURIComponent(project.updated_at || '')}`}
                  onLoadedMetadata={(event) => {
                    event.currentTarget.volume = Math.min(1, voiceVolume / 100);
                  }}
                  onCanPlay={(event) => {
                    if (!playGeneratedVoiceAfterLoadRef.current) return;
                    playGeneratedVoiceAfterLoadRef.current = false;
                    event.currentTarget.play().catch(() => {
                      setMessage('配音已生成，请点击播放器开始试听');
                    });
                  }}
                />
              </div>
            )}
            <div className="shot-list">
              {shots.map((shot) => (
                <div className="shot" key={shot.id}>
                  <ShotCard
                    shot={shot}
                    assets={generatedAssetsByShot.get(shot.id) || []}
                    selectedAssetId={shot.selected_asset_id}
                    project={project}
                    imageGenerationProvider={imageGenerationProvider}
                    onSelect={(assetId, assetSource) => selectAsset(shot.id, assetId, assetSource)}
                    onPreview={setPreviewAsset}
                    imagePrompt={imagePromptEditors[shot.id]}
                    savedImagePrompt={shot.image_prompt}
                    onOpenImagePrompt={() => openImagePromptEditor(shot.id)}
                    onImagePromptChange={(prompt) => updateImagePrompt(shot.id, prompt)}
                    onCancelImagePrompt={() => closeImagePromptEditor(shot.id)}
                    onGenerate={(prompt) => generateImage(shot.id, prompt)}
                    onReanalyzeImage={() => regenerateShotImagePrompt(shot.id)}
                    isRecognizingImage={recognizingShotIds.has(shot.id)}
                    onUpdateVoiceText={(text) => updateShotVoiceText(shot.id, text)}
                    processingImage={processingImage}
                    grayscaleProcessingIds={grayscaleProcessingIds}
                    generatingShotIds={generatingShotIds}
                    onRemoveWatermark={(assetId) => processGeneratedImage(assetId, 'remove-watermark')}
                    onGrayscale={(assetId) => processGeneratedImage(assetId, 'grayscale')}
                    onManualUpload={(file) => uploadManualShotImage(shot.id, file)}
                    onLibraryUpload={() => setLibraryPickerShotId(shot.id)}
                  />
                  <select value={shot.selected_asset_id || ''} onChange={(e) => {
                    const options = selectableAssets.filter((item) => !item.shot_id || item.shot_id === shot.id);
                    const selected = options.find((asset) => asset.id === e.target.value);
                    selectAsset(shot.id, e.target.value, selected?.asset_source || 'ai_generated');
                  }}>
                    <option value="">手动选择素材</option>
                    {selectableAssets.filter((item) => !item.shot_id || item.shot_id === shot.id).map((asset) => <option key={asset.id} value={asset.id}>{asset.file_name}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </section>
        )}

        {tab === 'cover' && project && (
          <section className="band cover-workspace">
            <div className="panel cover-editor">
              {/* Step 1: Title Generation */}
              <div>
                <h2>标题封面与配乐</h2>
                <p>生成两行标题和视频封面，并从音乐库选择背景音乐。配乐设置会在剪映草稿导出时自动应用。</p>
              </div>

              <div className="title-section">
                <h3>第一步 · 生成标题</h3>
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
                        const selected = (
                          titleLine1 === candidate.line1
                          && titleLine2 === candidate.line2
                        );
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
                              <small className="title-candidate-evidence">
                                依据：{candidate.evidence_quote}
                              </small>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div className="actions">
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={generateTitle}
                  >
                    <Wand2 size={18} /> {titleLine1 ? '重新生成标题' : 'AI 生成爆款标题'}
                  </button>
                  <button
                    disabled={busy || !titleLine1.trim() || !titleLine2.trim()}
                    onClick={confirmTitle}
                  >
                    <Save size={18} /> {titleConfirmed ? '标题已确认 ✓' : '确认标题'}
                  </button>
                </div>
              </div>

              {/* Step 2: Cover Generation (only after title is confirmed) */}
              <div className="cover-section">
                <h3>第二步 · 生成封面</h3>
                <p className="cover-layout-note">封面保持完整的 9:16 竖屏画面，不做裁剪；图片上方会叠加黑色遮罩，标题显示在遮罩上层。上传图片后，可在右侧分别拖动两行标题，拖动选框右下角可调整大小。</p>
                <input
                  ref={coverInputRef}
                  className="hidden-input"
                  type="file"
                  accept="image/*"
                  onChange={(event) => setCoverImage(event.target.files?.[0] || null)}
                />
                <button type="button" onClick={() => {
                  if (!coverInputRef.current) return;
                  coverInputRef.current.value = '';
                  coverInputRef.current.click();
                }}>
                  <ImagePlus size={18} /> {coverImage ? '更换人物图片' : '上传人物图片'}
                </button>
                {coverImage && <small className="cover-file-name">{coverImage.name}</small>}
                <label className="cover-mask-control">
                  遮罩不透明度：{coverMaskOpacity}%
                  <input
                    type="range"
                    min="0"
                    max="80"
                    step="1"
                    value={coverMaskOpacity}
                    onChange={(event) => setCoverMaskOpacity(Number(event.target.value))}
                  />
                  <small>数值越高，背景越暗，标题越醒目。</small>
                </label>
                <div className="actions">
                  <button
                    className="primary"
                    disabled={busy || !titleLine1.trim() || !titleLine2.trim() || (!coverImage && !project.cover_source_url)}
                    onClick={() => generateCover()}
                  >
                    <ImagePlus size={18} /> {project.cover_url ? '重新合成封面' : '生成 9:16 封面'}
                  </button>
                </div>
              </div>

              {project.audio_url && (
                <div className="voice-volume-section">
                  <h3>第三步 · 调整配音音量</h3>
                  <audio
                    ref={mainVoicePreviewRef}
                    controls
                    crossOrigin="anonymous"
                    src={`${API}${project.audio_url}`}
                    onLoadedMetadata={(event) => {
                      event.currentTarget.volume = Math.min(1, voiceVolume / 100);
                    }}
                    onPlay={(event) => {
                      activateMainVoicePreview(event.currentTarget);
                    }}
                  />
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
                  <button type="button" className="primary" disabled={busy} onClick={saveVoiceVolume}>
                    <Save size={18} /> 保存配音音量
                  </button>
                  <small>播放后拖动滑块可实时试听 0%–200% 的音量变化；剪映草稿会使用相同增益。</small>
                </div>
              )}

              <div className="music-section">
                <h3>{project.audio_url ? '第四步' : '第三步'} · 设置背景音乐</h3>
                <input
                  ref={musicInputRef}
                  className="hidden-input"
                  type="file"
                  accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,audio/*"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) uploadBackgroundMusic(file);
                    event.target.value = '';
                  }}
                />
                <label>
                  音乐库
                  <div className="music-library-row">
                    <select
                      value={backgroundMusicId}
                      onChange={(event) => {
                        setBackgroundMusicId(event.target.value);
                        setBackgroundMusicStart(0);
                      }}
                    >
                      <option value="">不添加背景音乐</option>
                      {musicLibrary.map((music) => (
                        <option key={music.id} value={music.id}>
                          {music.name} · {Math.round(Number(music.duration_sec || 0))}秒
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="danger icon-button"
                      title="删除当前选中的音乐"
                      aria-label="删除当前选中的音乐"
                      disabled={!backgroundMusicId || busy}
                      onClick={deleteBackgroundMusic}
                    >
                      <Trash2 size={18} />
                    </button>
                  </div>
                </label>
                <button type="button" onClick={() => musicInputRef.current?.click()}>
                  <Music size={18} /> 上传音乐并加入音乐库
                </button>
                {backgroundMusicId && (() => {
                  const selectedMusic = musicLibrary.find((music) => music.id === backgroundMusicId);
                  if (!selectedMusic) return null;
                  const maxStart = Math.max(0, Number(selectedMusic.duration_sec || 0) - 0.1);
                  return (
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
                  );
                })()}
                <button type="button" className="primary" disabled={busy} onClick={() => saveMusicSettings()}>
                  <Save size={18} /> 保存配乐设置
                </button>
                <small>音乐不足视频时长时会自动循环，相邻循环片段使用 1 秒淡入淡出衔接。</small>
                <button type="button" disabled={!project.cover_url} onClick={() => setTab('export')}>
                  <Download size={18} /> 下一步：导出
                </button>
              </div>

              {project.cover_model && <small>封面处理：{project.cover_model}</small>}
            </div>
            <div className="cover-preview">
              {coverImagePreviewUrl ? (
                <>
                  <CoverTitleEditor
                    imageUrl={coverImagePreviewUrl}
                    line1={titleLine1}
                    line2={titleLine2}
                    positions={coverTitlePositions}
                    maskOpacity={coverMaskOpacity}
                    onChange={updateCoverTitlePosition}
                  />
                  <small className="cover-editor-hint">拖动标题框调整位置，拖动右下角圆点调整字号</small>
                  {project.cover_url && (
                    <button type="button" className="cover-download-button" disabled={busy} onClick={downloadCover}>
                      <Download size={18} /> 应用调整并下载封面
                    </button>
                  )}
                </>
              ) : (
                <div className="cover-placeholder">
                  <ImagePlus size={42} />
                  <strong>尚未生成封面</strong>
                  <span>确认标题并上传人物图片后，在这里生成竖版封面</span>
                </div>
              )}
            </div>
          </section>
        )}

        {tab === 'export' && (
          <section className="band result">
            <h2>结果导出</h2>
            <p>导出内容包括按镜头编号的图片、配音字幕素材包和剪映草稿。</p>
            {project.audio_url && (
              <div className="export-voice-preview">
                <audio
                  ref={mainVoicePreviewRef}
                  controls
                  crossOrigin="anonymous"
                  src={`${API}${project.audio_url}`}
                  onLoadedMetadata={(event) => { event.currentTarget.volume = Math.min(1, voiceVolume / 100); }}
                  onPlay={(event) => activateMainVoicePreview(event.currentTarget)}
                />
                <span>配音音量 {voiceVolume}%</span>
              </div>
            )}
            <div className="export-actions">
              <VoiceSelect value={voiceType} onChange={setVoiceType} />
              <SpeechRateSelect value={speechRate} onChange={setSpeechRate} />
              <button type="button" onClick={previewVoice} disabled={previewingVoice}>
                <Mic size={18} /> {previewingVoice ? '试听生成中…' : '试听音色'}
              </button>
              {voicePreviewUrl && <audio ref={voicePreviewRef} className="voice-preview-player" controls src={voicePreviewUrl} />}
              <button onClick={generateVoiceAndSubtitles}><Mic size={18} /> 重新生成配音字幕</button>
              <button onClick={() => setTab('cover')}><Music size={18} /> 标题封面与配乐</button>
              <button onClick={openExportFolder}><FolderOpen size={18} /> 打开导出文件夹</button>
              <button className="primary" onClick={exportPackage}><Archive size={18} /> 导出剪映草稿</button>
            </div>
            <div className="publish-assistant">
              <div className="publish-assistant-head">
                <div>
                  <h3>发布助手</h3>
                  <p>生成平台发布时用的视频描述和一句话短标题。</p>
                </div>
                <button className="primary" onClick={generatePublishAssistant} disabled={busy}>
                  <Wand2 size={18} /> {publishShortTitle ? '重新生成' : '生成发布文案'}
                </button>
              </div>
              <div className="publish-fields">
                <label>
                  一句话短标题
                  <input
                    value={publishShortTitle}
                    onChange={(e) => setPublishShortTitle(e.target.value)}
                    placeholder="一句完整的短标题"
                  />
                </label>
                <label>
                  视频描述
                  <textarea
                    rows="5"
                    value={publishDescription}
                    onChange={(e) => setPublishDescription(e.target.value)}
                    placeholder="吸引人的视频描述"
                  />
                </label>
              </div>
            </div>
            {exportResult && (
              <div className="export-result-card">
                <div>
                  <strong>剪映草稿导出完成</strong>
                  {exportResult.zip_file_name && <span>{exportResult.zip_file_name}</span>}
                  <small>保存位置：{exportResult.export_folder}</small>
                  {exportResult.verification?.jianying?.draft_path && (
                    <small>剪映草稿：{exportResult.verification.jianying.draft_path}</small>
                  )}
                </div>
                <div className="export-result-actions">
                  {exportResult.download_url && (
                    <button onClick={() => window.open(`${API}${exportResult.download_url}`, '_blank')}>
                      <Download size={18} /> 下载草稿 ZIP
                    </button>
                  )}
                  {exportResult.verification?.jianying && (
                    <button onClick={openDraftFolder}>
                      <FolderOpen size={18} /> 打开剪映草稿
                    </button>
                  )}
                  <button className="primary" onClick={openExportFolder}>
                    <FolderOpen size={18} /> 打开导出文件夹
                  </button>
                </div>
              </div>
            )}
          </section>
        )}
      </main>

      {pendingUpload && (
        <UploadTagDialog
          files={pendingUpload.files}
          initialForm={pendingUpload.form}
          onClose={closePendingUpload}
          onUpload={confirmUpload}
        />
      )}
      {editingAsset && <AssetEditor asset={editingAsset} onClose={() => setEditingAsset(null)} onSave={saveAssetTags} onDelete={deleteAsset} />}
      {previewAsset && (
        <ImagePreview
          asset={previewAsset}
          onClose={() => setPreviewAsset(null)}
        />
      )}
      {libraryPickerShotId && (
        <LibraryImagePicker
          assets={assets}
          onClose={() => setLibraryPickerShotId('')}
          onSelect={uploadShotImageFromLibrary}
        />
      )}
    </div>
  );
}

function ProjectManager({ projects, activeProjectId, search, filter, onSearch, onFilter, onOpen, onArchive, onDelete }) {
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
              <button type="button" className={item.id === activeProjectId ? 'active' : ''} key={item.id} onClick={() => onOpen(item.id)}>
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
      </div>
      <div className="project-table-wrap">
        <table className="project-table">
          <thead><tr><th>项目名称</th><th>当前阶段</th><th>修改时间</th><th>分镜数</th><th>操作</th></tr></thead>
          <tbody>
            {filtered.map((item) => {
              const stage = projectStage(item);
              return (
                <tr key={item.id} className={item.id === activeProjectId ? 'active' : ''}>
                  <td><button type="button" className="project-name-link" onClick={() => onOpen(item.id)}>{item.name}</button></td>
                  <td><span className={`project-stage ${stage.key}`}>{stage.label}</span></td>
                  <td>{formatProjectTime(item.updated_at)}</td>
                  <td>{item.shot_count || 0}</td>
                  <td><div className="project-row-actions">
                    <button type="button" onClick={() => onOpen(item.id)}>继续</button>
                    <button type="button" onClick={() => onArchive(item, !item.archived)}>{item.archived ? '恢复' : '归档'}</button>
                    <button type="button" className="danger" onClick={() => onDelete(item)}>删除</button>
                  </div></td>
                </tr>
              );
            })}
            {!filtered.length && <tr><td colSpan="5" className="empty-projects">没有符合条件的项目</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProjectSelect({ projects, projectId, open, onOpenChange, onSelect }) {
  const selected = projects.find((item) => item.id === projectId);
  return (
    <div className="custom-select">
      <button type="button" className={open ? 'custom-select-trigger open' : 'custom-select-trigger'} onClick={() => onOpenChange(!open)}>
        <span>{selected?.name || '选择项目'}</span>
      </button>
      {open && (
        <div className="custom-select-menu">
          <button type="button" className={!projectId ? 'selected' : ''} onClick={() => onSelect('')}>选择项目</button>
          {projects.map((item) => (
            <button type="button" key={item.id} className={item.id === projectId ? 'selected' : ''} onClick={() => onSelect(item.id)}>
              {item.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AssetCard({ asset, selected, onSelect, onPreview, onEdit, onDelete, imageTools, libraryCard = false }) {
  const imageScore = asset.score_result?.score ?? asset.match_score;
  const imageReason = asset.score_result?.reason;
  const src = assetImageUrl(asset);
  const canPreview = Boolean(onPreview && asset.file_type === 'image');
  // 视频处理有三阶段：probing → splitting → analyzing，单看 'analyzing' 会漏掉前两步。
  const isAnalyzing = ['analyzing', 'probing', 'splitting'].includes(asset.analysis_status);
  function openPreview(event) {
    if (!canPreview) return;
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    onPreview();
  }
  return (
    <article className={`${isAnalyzing ? 'asset-card analyzing' : 'asset-card'}${libraryCard ? ' library-asset-card' : ''}${selected ? ' selected' : ''}`}>
      {onSelect && (
        <label className="asset-checkbox" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={!!selected} onChange={onSelect} />
        </label>
      )}
      <div
        className={`preview${canPreview ? ' preview-clickable' : ''}`}
        role={canPreview ? 'button' : undefined}
        tabIndex={canPreview ? 0 : undefined}
        title={canPreview ? '点击放大预览' : undefined}
        onClick={openPreview}
        onKeyDown={openPreview}
      >
        {asset.file_type === 'image' ? <SafeImage src={src} alt={asset.file_name} /> : (
          asset.file_url
            ? <video src={src} poster={asset.thumbnail_url ? `${API}${asset.thumbnail_url}` : undefined} controls preload="metadata" />
            : <div className="video-processing-placeholder"><Film size={32} />{asset.processing_stage === 'failed' ? '处理失败' : '原视频已转为片段'}</div>
        )}
        {imageTools}
      </div>
      <h3>{asset.file_name}</h3>
      {asset.file_type === 'video' && asset.processing_stage && asset.processing_stage !== 'ready' && (
        <div className="asset-processing-progress"><span style={{ width: `${asset.processing_progress || 0}%` }} /></div>
      )}
      {asset.file_type === 'video' && asset.duration_ms && <small>{(asset.duration_ms / 1000).toFixed(1)} 秒 · 代理片段</small>}
      {asset.file_type === 'video' && asset.clip_count !== undefined && <small>已生成 {asset.clip_count} 个片段</small>}
      {imageScore !== undefined && imageScore !== null && <p>图片评分：{imageScore}</p>}
      <p>{isAnalyzing ? '识别中' : [...(asset.object || asset.people || []), ...(asset.scene || []), ...(asset.keywords || [])].slice(0, 6).join(' / ') || '待补充标签'}</p>
      <small>{isAnalyzing ? '识别标签中，请稍候' : `${asset.analysis_provider || 'local_fallback'} · ${asset.copyright_note}`}</small>
      {imageReason && <small>{imageReason}</small>}
      {(onEdit || onDelete) && (
        <div className="asset-actions">
          {onEdit && <button disabled={isAnalyzing} onClick={onEdit}><Tags size={16} /> 编辑标签</button>}
          {onDelete && <button className="danger" onClick={onDelete}><Trash2 size={16} /> 删除</button>}
        </div>
      )}
    </article>
  );
}

function SafeImage({ src, alt, style, onLoad }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
  }, [src]);
  if (!src || broken) {
    return <div className="image-fallback">图片文件不可用</div>;
  }
  return <img src={src} alt={alt || ''} style={style} loading="lazy" decoding="async" onLoad={onLoad} onError={() => setBroken(true)} />;
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

function StoryboardProgress({ progress, project, imageGenerationProvider = 'seedream' }) {
  const generating = ['generating_shots', 'generating_images'].includes(project?.status);
  const promptOnly = project?.material_source_strategy === 'prompt_only';
  const label = progress.total
    ? `${progress.completed} / ${progress.total} 个分镜${promptOnly ? '提示词' : '图片'}完成`
    : '等待生成分镜';

  return (
    <div className="progress-panel">
      <div className="progress-row">
        <strong>
          分镜{promptOnly ? '提示词' : '图片'}进度
          <span className="provider-badge">
            {promptOnly ? 'AI 提示词' : `${IMAGE_GENERATION_PROVIDER_LABELS[imageGenerationProvider]} AI 出图`}
          </span>
        </strong>
        <div className="progress-actions">
          <span>{label}</span>
          <span className="progress-count success">成功 {progress.success}</span>
          <span className="progress-count failed">失败 {progress.failed}</span>
        </div>
      </div>
      <div className="progress-track">
        <div
          className={[
            'progress-fill',
            generating ? 'active' : '',
          ].filter(Boolean).join(' ')}
          style={{ width: `${progress.percent}%` }}
        />
      </div>
      {generating && (
        <div className="progress-detail">
          <small>{project?.current_generation_message || '正在生成分镜内容...'}</small>
          {progress.total > 0 && <small>已完成 {progress.completed}/{progress.total}</small>}
        </div>
      )}
      {project?.status === 'shot_generation_failed' && (
        <div className="progress-detail error">
          <small>{project.generation_error || '分镜生成失败，请稍后重试。'}</small>
        </div>
      )}
    </div>
  );
}

function ImagePreview({ asset, onClose }) {
  const src = assetImageUrl(asset);
  const canDownloadPng = Boolean(asset.project_id && asset.id && asset.file_type !== 'video');
  function downloadPng() {
    const link = document.createElement('a');
    link.href = `${API}/api/projects/${asset.project_id}/generated-assets/${asset.id}/download-png`;
    link.download = `${(asset.file_name || 'image').replace(/\.[^.]+$/, '')}.png`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }
  return (
    <div className="image-preview-backdrop" onClick={onClose}>
      <div className="image-preview" onClick={(e) => e.stopPropagation()}>
        <div className="image-preview-head">
          <strong>{asset.file_name || '图片预览'}</strong>
          <div className="image-preview-actions">
            {canDownloadPng && (
              <button type="button" onClick={downloadPng}>
                <Download size={17} /> 下载 PNG
              </button>
            )}
            <button type="button" onClick={onClose}>关闭</button>
          </div>
        </div>
        <div className="image-preview-stage">
          <img src={src} alt={asset.file_name || 'preview'} />
        </div>
        {(asset.source_page || asset.remote_url) && (
          <a href={asset.source_page || asset.remote_url} target="_blank" rel="noreferrer">查看来源</a>
        )}
      </div>
    </div>
  );
}

function TagFields({ form, update }) {
  return (
    <>
      <div className="grid">
        <label>主体标签<input value={form.object} onChange={(e) => update('object', e.target.value)} placeholder="武将，战马，城门" /></label>
        <label>场景标签<input value={form.scene} onChange={(e) => update('scene', e.target.value)} placeholder="实验室，会议室，戈壁滩" /></label>
        <label>关键词<input value={form.keywords} onChange={(e) => update('keywords', e.target.value)} placeholder="归国科学家，留学回国" /></label>
      </div>
      <SourceFields form={form} update={update} />
    </>
  );
}

function SourceFields({ form, update }) {
  return (
    <>
      <div className="grid">
        <label>来源链接<input value={form.source_page || ''} onChange={(e) => update('source_page', e.target.value)} placeholder="https://..." /></label>
      </div>
      <label>来源备注<input value={form.source_note} onChange={(e) => update('source_note', e.target.value)} /></label>
      <label>版权备注<input value={form.copyright_note} onChange={(e) => update('copyright_note', e.target.value)} /></label>
    </>
  );
}

function UploadTagDialog({ files, initialForm, onClose, onUpload }) {
  const [form, setForm] = useState(initialForm);
  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }
  function submit(ev) {
    ev.preventDefault();
    onUpload(form);
  }
  return (
    <div className="modal-backdrop">
      <form className="asset-editor" onSubmit={submit}>
        <div className="row">
          <h2>上传前设置标签</h2>
          <button type="button" onClick={onClose}>关闭</button>
        </div>
        <div className="selected-files">
          {files.slice(0, 6).map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}
          {files.length > 6 && <span>还有 {files.length - 6} 个文件</span>}
        </div>
        <SourceFields form={form} update={update} />
        {files.some((file) => /\.(mp4|mov|webm)$/i.test(file.name)) && (
          <div className="video-upload-options">
            <strong>视频处理方式</strong>
            <label className="check-row">
              <input type="radio" name="video-processing-mode" value="split" checked={form.video_processing_mode === 'split'} onChange={() => update('video_processing_mode', 'split')} />
              智能切割分段（按场景生成多个 3～15 秒素材）
            </label>
            <label className="check-row">
              <input type="radio" name="video-processing-mode" value="full" checked={form.video_processing_mode === 'full'} onChange={() => update('video_processing_mode', 'full')} />
              保留完整素材（压缩转码，但不改变视频时长）
            </label>
            <label className="check-row">
              <input type="checkbox" checked={Boolean(form.keep_original)} onChange={(e) => update('keep_original', e.target.checked)} />
              另外长期保存未经压缩的原始文件
            </label>
          </div>
        )}
        <div className="actions"><button type="button" onClick={onClose}>取消</button><button className="primary"><ImagePlus size={18} /> 上传并自动打标</button></div>
      </form>
    </div>
  );
}

function AssetEditor({ asset, onClose, onSave, onDelete }) {
  const [form, setForm] = useState({
    object: listToText(asset.object || asset.people),
    scene: listToText(asset.scene),
    keywords: listToText(asset.keywords),
    source_page: asset.source_page || asset.remote_url || '',
    source_note: asset.source_note || '',
    copyright_note: asset.copyright_note || '',
    is_available: asset.is_available !== false,
    sync_remote: asset.storage_provider === 'cloudflare_r2',
  });

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit(ev) {
    ev.preventDefault();
    onSave(asset.id, {
      object: textToList(form.object),
      scene: textToList(form.scene),
      keywords: textToList(form.keywords),
      source_page: form.source_page,
      source_note: form.source_note,
      copyright_note: form.copyright_note,
      is_available: form.is_available,
      sync_remote: form.sync_remote,
    });
  }

  return (
    <div className="modal-backdrop">
      <form className="asset-editor" onSubmit={submit}>
        <div className="row">
          <h2>编辑标签</h2>
          <button type="button" onClick={onClose}>关闭</button>
        </div>
        <div className="editor-preview">
          {asset.file_type === 'image' ? <SafeImage src={`${API}${asset.file_url}`} alt={asset.file_name} /> : <video src={`${API}${asset.file_url}`} controls />}
          <strong>{asset.file_name}</strong>
        </div>
        <TagFields form={form} update={update} />
        <label className="check-row"><input type="checkbox" checked={form.is_available} onChange={(e) => update('is_available', e.target.checked)} /> 可用于项目匹配</label>
        <label className="check-row">
          <input type="checkbox" checked={form.sync_remote} onChange={(e) => update('sync_remote', e.target.checked)} />
          同步远程素材库
        </label>
        <div className="actions">
          <button type="button" className="danger" onClick={() => onDelete(asset)}><Trash2 size={18} /> 删除素材</button>
          <button type="button" onClick={onClose}>取消</button>
          <button className="primary"><Save size={18} /> 保存标签</button>
        </div>
      </form>
    </div>
  );
}

function LibraryImagePicker({ assets, onClose, onSelect }) {
  const images = assets.filter((asset) => asset.file_type === 'image' && asset.is_available !== false);
  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="asset-editor library-image-picker">
        <div className="row">
          <div>
            <h2>从素材库选择图片</h2>
            <small>选择后会作为用户上传图片应用到当前分镜。</small>
          </div>
          <button type="button" onClick={onClose}>关闭</button>
        </div>
        {images.length ? (
          <div className="library-picker-grid">
            {images.map((asset) => (
              <button
                type="button"
                className="library-picker-item"
                key={asset.id}
                onClick={() => onSelect(asset.id)}
              >
                <SafeImage src={assetImageUrl(asset)} alt={asset.file_name} />
                <span>{asset.file_name}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty-state">素材库中暂无可用图片，请先到素材库上传图片。</div>
        )}
      </div>
    </div>
  );
}

function ShotCard({
  shot,
  assets = [],
  selectedAssetId,
  project,
  imageGenerationProvider = 'seedream',
  onSelect,
  onPreview,
  imagePrompt,
  savedImagePrompt,
  onOpenImagePrompt,
  onImagePromptChange,
  onCancelImagePrompt,
  onGenerate,
  onReanalyzeImage,
  isRecognizingImage,
  onUpdateVoiceText,
  processingImage,
  grayscaleProcessingIds,
  generatingShotIds,
  onRemoveWatermark,
  onGrayscale,
  onManualUpload,
  onLibraryUpload,
}) {
  const manualUploadRef = useRef(null);
  const [editingVoiceText, setEditingVoiceText] = useState(false);
  const [voiceTextDraft, setVoiceTextDraft] = useState(shot.voice_text || '');
  useEffect(() => {
    if (!editingVoiceText) setVoiceTextDraft(shot.voice_text || '');
  }, [shot.voice_text, editingVoiceText]);
  const isGeneratingImage = generatingShotIds.has(shot.id);
  const visibleAssets = [...assets]
    .sort((a, b) => (b.id === selectedAssetId ? 1 : 0) - (a.id === selectedAssetId ? 1 : 0))
    .slice(0, 2);
  const placeholders = Math.max(0, 2 - visibleAssets.length);
  const canReanalyze = !isRecognizingImage && project?.status !== 'generating_shots';
  const imageProviderLabel = IMAGE_GENERATION_PROVIDER_LABELS[imageGenerationProvider] || imageGenerationProvider;
  return (
    <article className={isRecognizingImage ? 'shot-card recognizing-image' : 'shot-card'}>
      <div className="shot-main">
        <span className={`pill ${shot.status}`}>镜头 {shot.shot_index} · {shot.status}</span>
        {editingVoiceText ? (
          <div className="shot-voice-editor">
            <textarea
              value={voiceTextDraft}
              onChange={(event) => setVoiceTextDraft(event.target.value)}
              rows="4"
              autoFocus
            />
            <div className="actions">
              <button type="button" onClick={() => { setVoiceTextDraft(shot.voice_text || ''); setEditingVoiceText(false); }}>取消</button>
              <button
                type="button"
                className="primary"
                disabled={!voiceTextDraft.trim()}
                onClick={async () => {
                  if (await onUpdateVoiceText?.(voiceTextDraft)) setEditingVoiceText(false);
                }}
              >
                <Save size={16} /> 保存镜头文案
              </button>
            </div>
          </div>
        ) : (
          <div className="shot-voice-display">
            <h3>{shot.voice_text}</h3>
            <button type="button" onClick={() => setEditingVoiceText(true)}>编辑镜头文案</button>
          </div>
        )}
        <p className="shot-visual-description">画面描述：{shot.visual_need || '暂无描述'}</p>
        {savedImagePrompt && (
          <div className="saved-image-prompt">
            <div>
              <strong>AI 图片提示词</strong>
              <button type="button" onClick={() => writeClipboardText(savedImagePrompt)}><Copy size={15} /> 复制</button>
            </div>
            <p>{savedImagePrompt}</p>
          </div>
        )}
        <button className="reanalyze-image-button" type="button" onClick={onReanalyzeImage} disabled={!canReanalyze}>
          <RefreshCw size={18} /> {isRecognizingImage ? '生成中…' : '重新生成提示词'}
        </button>
      </div>
      <div className="shot-side">
        <div className="shot-images">
          {imagePrompt !== undefined && !isGeneratingImage && (
            <div className="ai-prompt-editor">
              <strong>编辑 AI 图片提示词</strong>
              <textarea
                value={imagePrompt}
                onChange={(event) => onImagePromptChange?.(event.target.value)}
                placeholder={`输入希望 ${imageProviderLabel} 生成的画面描述`}
                autoFocus
              />
              <div className="ai-prompt-actions">
                <button type="button" onClick={onCancelImagePrompt}>取消</button>
                <button
                  type="button"
                  className="primary"
                  disabled={!String(imagePrompt || '').trim()}
                  onClick={() => onGenerate?.(imagePrompt)}
                >
                  <Wand2 size={16} /> 确认生成
                </button>
              </div>
            </div>
          )}
          {isGeneratingImage && (
            <div className="ai-generating-overlay">
              <Wand2 size={26} />
              <strong>AI 图片生成中</strong>
              <small>{imageProviderLabel} 正在绘制 9:16 竖屏图片，请稍候</small>
            </div>
          )}
          {visibleAssets.map((item) => (
            <div
              className={`${item.id === selectedAssetId ? 'image-choice selected' : 'image-choice'} ${grayscaleProcessingIds.has(item.id) ? 'converting-grayscale' : ''}`.trim()}
              key={item.id}
            >
              <div
                className="image-choice-main"
                role="button"
                tabIndex={0}
                onClick={(event) => {
                  onSelect?.(item.id, item.asset_source || 'ai_generated');
                  onPreview?.(item);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect?.(item.id, item.asset_source || 'ai_generated');
                    onPreview?.(item);
                  }
                }}
                title="选择并预览这张图"
              >
                <AssetCard
                  asset={item}
                  imageTools={(
                    <div className="image-tools">
                      {item.asset_source !== 'local' && (
                        <>
                      <button
                        type="button"
                        title="一键转为黑白照片"
                        aria-label="转为黑白照片"
                        disabled={Boolean(processingImage) || grayscaleProcessingIds.has(item.id) || item.is_grayscale}
                        onClick={(event) => {
                          event.stopPropagation();
                          onGrayscale?.(item.id);
                        }}
                      >
                        <Film size={16} />
                      </button>
                      <button
                        type="button"
                        title="使用 Seedream 去除水印"
                        aria-label="Seedream 去水印"
                        disabled={Boolean(processingImage) || grayscaleProcessingIds.has(item.id)}
                        onClick={(event) => {
                          event.stopPropagation();
                          onRemoveWatermark?.(item.id);
                        }}
                      >
                        <Eraser size={16} />
                      </button>
                        </>
                      )}
                    </div>
                  )}
                />
              </div>
              {grayscaleProcessingIds.has(item.id) && (
                <div className="grayscale-processing-overlay">
                  <Film size={22} />
                  <strong>黑白转换中</strong>
                </div>
              )}
            </div>
          ))}
          {Array.from({ length: placeholders }).map((_, index) => (
            <div className="search-placeholder" key={`placeholder-${index}`}>
              <strong>暂无 AI 图片</strong>
              <small>可编辑提示词后调用 {imageProviderLabel} 生成</small>
            </div>
          ))}
        </div>
        <div className="shot-bottom-actions">
          <button
            className={isGeneratingImage ? 'ai-generate-button active' : 'ai-generate-button'}
            onClick={onOpenImagePrompt}
            disabled={isGeneratingImage}
          >
            <Wand2 size={18} /> {isGeneratingImage ? 'AI 生成中' : imagePrompt !== undefined ? '编辑提示词中' : 'AI 出图'}
          </button>
          <button type="button" onClick={() => manualUploadRef.current?.click()}>
            <ImagePlus size={18} /> 手动上传图片
          </button>
          <button type="button" onClick={onLibraryUpload}>
            <Library size={18} /> 从素材库选择
          </button>
          <input
            ref={manualUploadRef}
            className="hidden-input"
            type="file"
            accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onManualUpload?.(file);
              event.target.value = '';
            }}
          />
        </div>
      </div>
    </article>
  );
}

createRoot(document.getElementById('root')).render(<App />);
