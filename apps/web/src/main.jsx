import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Archive, Crop, Download, Eraser, Film, FolderOpen, ImagePlus, Library, Mic, Music, RefreshCw, Save, Scissors, Search, Tags, Trash2, Wand2 } from 'lucide-react';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const VOICE_OPTIONS = [
  { value: 'zh_male_m191_uranus_bigtts', label: '男声 · 沉稳叙事' },
  { value: 'zh_male_dongfanghaoran_uranus_bigtts', label: '男声 · 东方浩然' },
  { value: 'zh_female_vv_uranus_bigtts', label: '女声 · 清晰自然' },
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

function emptyTagForm() {
  return {
    object: '',
    scene: '',
    keywords: '',
    source_page: '',
    source_note: '用户上传',
    copyright_note: '自用素材',
  };
}

function assetImageUrl(asset) {
  if (!asset?.file_url) return '';
  const version = asset.updated_at || asset.created_at || '';
  return `${API}${asset.file_url}${version ? `?v=${encodeURIComponent(version)}` : ''}`;
}

function App() {
  const [tab, setTab] = useState('create');
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState(null);
  const [shots, setShots] = useState([]);
  const [assets, setAssets] = useState([]);
  const [generatedAssets, setGeneratedAssets] = useState([]);
  const [webImageDiagnostics, setWebImageDiagnostics] = useState([]);
  const [library, setLibrary] = useState(null);
  const [musicLibrary, setMusicLibrary] = useState([]);
  const [editingAsset, setEditingAsset] = useState(null);
  const [pendingUpload, setPendingUpload] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [rawScriptDraft, setRawScriptDraft] = useState('');
  const [aiScriptPerson, setAiScriptPerson] = useState('');
  const [aiScriptAngle, setAiScriptAngle] = useState('');
  const [processingImage, setProcessingImage] = useState('');
  const [generatingShotId, setGeneratingShotId] = useState('');
  const [imagePromptEditors, setImagePromptEditors] = useState({});
  const [materialSourceStrategy, setMaterialSourceStrategy] = useState('library_first');
  const [voiceType, setVoiceType] = useState(VOICE_OPTIONS[0].value);
  const [speechRate, setSpeechRate] = useState(0);
  const [titleLine1, setTitleLine1] = useState('');
  const [titleLine2, setTitleLine2] = useState('');
  const [titleConfirmed, setTitleConfirmed] = useState(false);
  const [coverImage, setCoverImage] = useState(null);
  const [backgroundMusicId, setBackgroundMusicId] = useState('');
  const [backgroundMusicStart, setBackgroundMusicStart] = useState(0);
  const [backgroundMusicVolume, setBackgroundMusicVolume] = useState(20);
  const [previewAsset, setPreviewAsset] = useState(null);
  const [libraryPickerShotId, setLibraryPickerShotId] = useState('');
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [exportResult, setExportResult] = useState(null);
  const [selectedAssetIds, setSelectedAssetIds] = useState(new Set());
  const folderFallbackRef = useRef(null);
  const uploadInputRef = useRef(null);
  const coverInputRef = useRef(null);
  const musicInputRef = useRef(null);
  const musicPreviewRef = useRef(null);
  const lastProjectStatusRef = useRef('');

  const activeLibrary = validLibrary(library) ? library : null;
  const selectedAssets = useMemo(() => {
    const map = new Map();
    assets.forEach((asset) => map.set(asset.id, asset));
    generatedAssets.forEach((asset) => map.set(asset.id, {
      ...asset,
      file_type: 'image',
      file_name: asset.file_name || `AI 占位图 ${asset.image_size || ''}`.trim(),
    }));
    return map;
  }, [assets, generatedAssets]);
  const selectableAssets = useMemo(() => [
    ...generatedAssets.map((asset) => ({
      ...asset,
      file_type: 'image',
      file_name: asset.file_name || `网络图片 ${asset.image_size || ''}`.trim(),
      asset_source: asset.asset_source || 'web_search',
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
        file_name: asset.file_name || `网络图片 ${asset.image_size || ''}`.trim(),
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
  const searchProgress = useMemo(() => {
    const total = shots.length || project?.search_total || 0;
    const success = shots.filter((shot) => ['web_downloaded', 'uploaded', 'ai_generated', 'matched'].includes(shot.status)).length;
    const failed = shots.filter((shot) => ['no_image', 'no_match', 'intent_failed'].includes(shot.status)).length;
    const completed = shots.length
      ? success + failed
      : Math.min(total, project?.search_completed || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    return { total, completed, success, failed, percent };
  }, [project, shots]);
  const diagnosticsByShot = useMemo(() => {
    const map = new Map();
    webImageDiagnostics.forEach((item) => {
      const list = map.get(item.shot_id) || [];
      list.push(item);
      map.set(item.shot_id, list);
    });
    map.forEach((list) => list.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))));
    return map;
  }, [webImageDiagnostics]);

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
    const [projectList, assetList, libraryData, musicData, projectData] = await Promise.all([
      request('/api/projects'),
      request('/api/assets'),
      request('/api/assets/library'),
      request('/api/assets/music'),
      id ? request(`/api/projects/${id}`) : Promise.resolve(null),
    ]);
    setProjects(projectList.projects);
    setAssets(assetList.assets);
    setLibrary(validLibrary(libraryData.library) ? libraryData.library : null);
    setMusicLibrary(musicData.music || []);
    if (projectData) {
      setProject(projectData.project);
      setShots(projectData.shots);
      setGeneratedAssets(projectData.generated_assets || []);
      setWebImageDiagnostics(projectData.web_image_diagnostics || []);
      setProjectId(id);
    } else {
      setProject(null);
      setShots([]);
      setGeneratedAssets([]);
      setWebImageDiagnostics([]);
      setProjectId('');
    }
  }

  async function refreshProject(id = projectId) {
    if (!id) return;
    const data = await request(`/api/projects/${id}`);
    setProject(data.project);
    setShots(data.shots);
    setGeneratedAssets(data.generated_assets || []);
    setWebImageDiagnostics(data.web_image_diagnostics || []);
  }


  useEffect(() => {
    refreshAll();
  }, []);

  useEffect(() => {
    if (!project) return;
    setTitleLine1(project.title_line1 || '');
    setTitleLine2(project.title_line2 || '');
    setTitleConfirmed(Boolean(project.title_line1 && project.title_line2) || Boolean(project.cover_url));
    setCoverImage(null);
    setBackgroundMusicId(project.background_music_id || '');
    setBackgroundMusicStart(Number(project.background_music_start_sec || 0));
    setBackgroundMusicVolume(Math.round(Number(project.background_music_volume ?? 0.2) * 100));
  }, [
    project?.id,
    project?.title_line1,
    project?.title_line2,
    project?.cover_url,
    project?.background_music_id,
    project?.background_music_start_sec,
    project?.background_music_volume,
  ]);

  useEffect(() => {
    const player = musicPreviewRef.current;
    if (!player) return;
    player.volume = Math.max(0, Math.min(1, backgroundMusicVolume / 100));
  }, [backgroundMusicVolume, backgroundMusicId]);

  useEffect(() => {
    const player = musicPreviewRef.current;
    if (!player || !Number.isFinite(player.duration)) return;
    player.currentTime = Math.min(
      backgroundMusicStart,
      Math.max(player.duration - 0.1, 0),
    );
  }, [backgroundMusicStart, backgroundMusicId]);

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
    const activeStatuses = ['pending_search', 'analyzing_intent', 'searching'];
    if (!projectId || !shots.some((shot) => activeStatuses.includes(shot.status)) && project?.status !== 'searching_images') return undefined;
    const timer = window.setInterval(() => {
      refreshProject(projectId);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [shots, projectId, project]);

  useEffect(() => {
    const previous = lastProjectStatusRef.current;
    const current = project?.status || '';
    if (previous === 'searching_images' && current === 'search_failed') {
      setMessage(project?.search_error ? `处理失败：${project.search_error}` : '分镜图片处理失败');
    } else if (previous === 'searching_images' && current === 'search_stopped') {
      setMessage('');
    } else if (previous === 'searching_images' && current && current !== 'searching_images') {
      if (searchProgress.total && searchProgress.completed < searchProgress.total) {
        setMessage(`分镜图片处理结束，仍有 ${searchProgress.total - searchProgress.completed} 个分镜未完成`);
      } else {
        setMessage('分镜图片处理完成');
      }
    }
    lastProjectStatusRef.current = current;
  }, [project?.status, project?.search_error, searchProgress.completed, searchProgress.total]);

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
      setAiScriptPerson('');
      setAiScriptAngle('');
      await refreshAll(data.project_id);
      setTab('script');
    }
  }

  async function generateAiRawScript() {
    const data = await run('AI 写文案', () => request('/api/projects/generate-guozhijiliang-script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        person_name: aiScriptPerson,
        event_angle: aiScriptAngle,
      }),
    }));
    if (data?.script) {
      setRawScriptDraft(data.script);
      setMessage(data.person ? `AI 文案已生成：${data.person}` : 'AI 文案已生成');
    }
  }

  async function rewrite() {
    const data = await run('二创文案', () => request(`/api/projects/${projectId}/rewrite`, { method: 'POST' }));
    if (data) await refreshAll(projectId);
  }

  async function saveScript() {
    await run('保存文案', () => request(`/api/projects/${projectId}/script`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rewritten_script: project.rewritten_script }),
    }));
    await refreshAll(projectId);
  }

  async function generateShots(imageSearchProvider = 'so') {
    setTab('storyboard');
    setBusy(true);
    setMessage('生成分镜中...');
    try {
      await request(`/api/projects/${projectId}/script`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rewritten_script: project.rewritten_script }),
      });
      await request(
        `/api/projects/${projectId}/shots?image_search_provider=${imageSearchProvider}&material_source_strategy=${materialSourceStrategy}`,
        { method: 'POST' },
      );
      await refreshAll(projectId);
      setMessage('分镜已生成，正在分析关键词和搜索图片...');
    } catch (err) {
      setMessage(`生成分镜失败：${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function skipToStoryboard(imageSearchProvider = 'so') {
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
        `/api/projects/${projectId}/shots?image_search_provider=${imageSearchProvider}&material_source_strategy=${materialSourceStrategy}`,
        { method: 'POST' },
      );
      await refreshAll(projectId);
      setMessage('分镜已生成，正在分析关键词和搜索图片...');
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
    const result = await run('上传素材', () => request('/api/assets/upload', { method: 'POST', body: data }));
    if (!result) return;
    closePendingUpload();
    setTab('assets');
    setMessage('素材已上传，正在识别标签');
    await refreshAll(projectId);
  }

  async function saveAssetTags(assetId, payload) {
    await run('保存标签', () => request(`/api/assets/${assetId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }));
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

  async function matchAssets() {
    await run('联网下载图片', () => request(`/api/projects/${projectId}/match-assets`, { method: 'POST' }));
    await refreshAll(projectId);
    setTab('match');
  }

  async function stopImageSearch() {
    if (!projectId) return;
    setMessage('正在停止图片搜索...');
    await request(`/api/projects/${projectId}/stop-image-search`, { method: 'POST' });
    await refreshAll(projectId);
  }

  async function retryFailedShots(imageSearchProvider = 'so') {
    const label = imageSearchProvider === 'tencent' ? '腾讯重新搜索失败分镜' : '重新搜索失败分镜';
    const result = await run(label, () => request(
      `/api/projects/${projectId}/retry-failed-shots?image_search_provider=${imageSearchProvider}`,
      { method: 'POST' },
    ));
    if (!result) return;
    if (result.retried_count === 0) {
      setMessage('没有失败的分镜需要重新搜索');
      return;
    }
    await refreshAll(projectId);
    setMessage(`正在重新搜索 ${result.retried_count} 个失败分镜...`);
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
    setGeneratingShotId(shotId);
    try {
      const result = await run('生成占位图', () => request(
        `/api/projects/${projectId}/shots/${shotId}/generate-image`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: String(prompt).trim() }),
        },
      ));
      if (!result) return;
      closeImagePromptEditor(shotId);
      await refreshAll(projectId);
    } finally {
      setGeneratingShotId('');
    }
  }

  async function retryImageSearch(shotId, imageSearchProvider = 'so') {
    const label = imageSearchProvider === 'tencent' ? '腾讯重新搜索图片' : '重新搜索图片';
    await run(label, () => request(
      `/api/projects/${projectId}/shots/${shotId}/retry-image-search?image_search_provider=${imageSearchProvider}`,
      { method: 'POST' },
    ));
    await refreshAll(projectId);
  }

  async function processGeneratedImage(assetId, operation) {
    const processingKey = `${assetId}:${operation}`;
    const label = operation === 'crop-square' ? '裁剪图片' : 'Seedream 去水印';
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
    setMessage(`素材入库：新增 ${result.created}，跳过重复 ${result.skipped_duplicates}，无图片 ${result.missing}`);
    await refreshAll(projectId);
  }

  async function cropSelectedImages() {
    const result = await run('一键裁剪', () => request(
      `/api/projects/${projectId}/crop-selected-images`,
      { method: 'POST' },
    ));
    if (!result) return;
    setMessage(`一键裁剪完成：成功 ${result.cropped}，跳过 ${result.skipped}，失败 ${result.failed.length}`);
    await refreshAll(projectId);
  }

  async function selectAsset(shotId, assetId, assetSource = 'web_search') {
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
    await refreshAll(projectId);
    setTab('cover');
    return true;
  }

  async function generateCover() {
    if (!coverImage) {
      setMessage('请先上传一张人物图片');
      return;
    }
    const data = new FormData();
    data.append('file', coverImage, coverImage.name);
    const result = await run('生成视频封面', () => request(`/api/projects/${projectId}/generate-cover`, {
      method: 'POST',
      body: data,
    }));
    if (!result) return;
    await refreshAll(projectId);
    setMessage('9:16 视频封面生成完成');
  }

  function downloadCover() {
    if (!project?.cover_url) return;
    const link = document.createElement('a');
    link.href = `${API}/api/projects/${projectId}/download-cover`;
    link.download = '视频封面.png';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setMessage('封面下载已开始');
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
    setTitleLine1(result.line1);
    setTitleLine2(result.line2);
    setTitleConfirmed(false);
    await refreshAll(projectId);
    setMessage('爆款标题生成完成，请确认或编辑');
  }

  async function confirmTitle() {
    if (!titleLine1.trim() || !titleLine2.trim()) {
      setMessage('请填写两行标题');
      return;
    }
    await run('保存标题', () => request(`/api/projects/${projectId}/script`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title_line1: titleLine1.trim(),
        title_line2: titleLine2.trim(),
      }),
    }));
    setTitleConfirmed(true);
    setMessage('标题已确认，可以生成封面');
  }

  async function exportPackage(output) {
    if (!project?.audio_url || !project?.voice_timeline_url) {
      const generated = await generateVoiceAndSubtitles();
      if (!generated) return;
    }
    const label = output === 'mp4' ? '导出 MP4' : '导出剪映草稿';
    const data = await run(label, () => request(
      `/api/projects/${projectId}/export/assets?output=${output}`,
      { method: 'POST' },
    ));
    if (!data) return;
    setExportResult(data);
    if (output === 'mp4') {
      setMessage(`MP4 导出完成：${data.verification?.mp4?.passed ? '验证通过' : '验证失败'}`);
    } else {
      setMessage(`剪映草稿导出完成：${data.verification?.jianying?.draft_name || ''}`);
    }
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

  const workflowBusy = busy || project?.status === 'searching_images';
  const workflowMessage = (() => {
    if (project?.status === 'search_failed') {
      return project.search_error ? `处理失败：${project.search_error}` : '分镜图片处理失败';
    }
    if (project?.status === 'search_stopped') return message || '就绪';
    if (project?.status !== 'searching_images') return message || '就绪';
    if (project.search_stage === 'stopping') return '正在停止图片搜索...';
    if (project.search_stage === 'analyzing_intent') {
      return project.current_search_keyword || '正在分析分镜关键词...';
    }
    if (project.search_stage === 'intent_ready') {
      return '关键词已生成，准备搜索图片...';
    }
    if (project.search_stage === 'downloading') {
      const total = project.search_total || shots.length || 0;
      return `正在搜索图片 ${project.search_completed || 0}/${total}`;
    }
    return '正在处理分镜图片...';
  })();

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand"><Film size={22} /> 草稿生成器</div>
        <nav>
          <button className={tab === 'create' ? 'active' : ''} onClick={() => setTab('create')}><Scissors size={18} /> 项目</button>
          <button className={tab === 'assets' ? 'active' : ''} onClick={() => setTab('assets')}><Library size={18} /> 素材库</button>
          <button className={tab === 'script' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('script')}><Wand2 size={18} /> 文案</button>
          <button className={tab === 'storyboard' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('storyboard')}><Archive size={18} /> 分镜</button>
          <button className={tab === 'match' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('match')}><Search size={18} /> 匹配</button>
          <button className={tab === 'cover' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('cover')}><Music size={18} /> 标题封面与配乐</button>
          <button className={tab === 'export' ? 'active' : ''} disabled={!projectId} onClick={() => setTab('export')}><Download size={18} /> 导出</button>
        </nav>
        <div className="project-picker">
          <ProjectSelect
            projects={projects}
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
            <span className={workflowBusy ? 'status busy' : 'status'}>{workflowMessage}</span>
          </div>
        </header>

        {tab === 'create' && (
          <section className="band two-col">
            <form onSubmit={createProject} className="panel">
              <h2>创建项目</h2>
              <label>项目名称<input name="name" placeholder="可留空，系统自动取标题" /></label>
              <div className="ai-script-options">
                <label>人物名称（可选）<input value={aiScriptPerson} onChange={(event) => setAiScriptPerson(event.target.value)} placeholder="留空则随机选择《国之脊梁》院士" /></label>
                <label>核心事件或角度（可选）<input value={aiScriptAngle} onChange={(event) => setAiScriptAngle(event.target.value)} placeholder="如：生命最后一天整理资料" /></label>
              </div>
              <label>
                原始文案
                <textarea
                  name="raw_script"
                  required
                  rows="12"
                  value={rawScriptDraft}
                  onChange={(event) => setRawScriptDraft(event.target.value)}
                  placeholder="粘贴历史人物/纪实解说文案，或点击 AI 写《国之脊梁》文案"
                />
              </label>
              <div className="script-create-actions">
                <button type="button" onClick={generateAiRawScript} disabled={busy}>
                  <Wand2 size={18} /> AI 写文案
                </button>
                <button className="primary" disabled={!rawScriptDraft.trim()}><Save size={18} /> 创建并进入文案</button>
              </div>
            </form>
            <div className="notes">
              <h2>V1 范围</h2>
              <p>本版本不抓取外网视频，不自动判断版权，不直接生成成片。重点解决“文案拆成镜头，并帮你从素材库里找到合适画面”。</p>
              <p>选择素材后会先让你填写标签，再上传并自动打标；手动标签会优先保留。</p>
            </div>
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
                    <button type="button" onClick={retryAssetAnalysis}><RefreshCw size={18} /> 重试标签识别</button>
                    <button className="primary" type="button" onClick={() => uploadInputRef.current?.click()}><ImagePlus size={18} /> 选择素材</button>
                  </div>
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
                  {assets.map((asset) => (
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
              </>
            )}
          </section>
        )}

        {tab === 'script' && project && (
          <section className="band two-col script-grid">
            <div className="panel">
              <h2>原始文案</h2>
              <textarea readOnly value={project.raw_script} rows="18" />
              <label className="source-strategy">
                素材来源策略
                <select value={materialSourceStrategy} onChange={(event) => setMaterialSourceStrategy(event.target.value)}>
                  <option value="library_first">优先素材库，缺失时联网搜索</option>
                  <option value="library_only">仅使用素材库</option>
                  <option value="web_only">仅联网搜索</option>
                </select>
              </label>
              <div className="actions raw-script-actions">
                <button className="primary" onClick={() => skipToStoryboard('so')}><Archive size={18} /> 直接生成分镜</button>
                <button
                  className="tencent-storyboard"
                  title="使用原始文案生成分镜，并使用腾讯云联网图像搜索"
                  onClick={() => skipToStoryboard('tencent')}
                >
                  <Search size={18} /> 直接生成分镜
                </button>
              </div>
              <small className="raw-script-hint">跳过二创，使用原始文案直接拆分镜头</small>
            </div>
            <div className="panel">
              <div className="row">
                <h2>二创口播稿</h2>
                <button onClick={rewrite}><RefreshCw size={18} /> 生成</button>
              </div>
              {project.rewrite_comparison && (
                <p>
                  总体差异度：{project.rewrite_comparison.overall_difference ?? project.rewrite_difference ?? '-'}%
                  {' · '}字符相似度：{project.rewrite_comparison.character_similarity ?? '-'}%
                  {' · '}语义相似度：{project.rewrite_comparison.semantic_similarity ?? '-'}%
                </p>
              )}
              <textarea value={project.rewritten_script || ''} rows="18" onChange={(e) => setProject({ ...project, rewritten_script: e.target.value })} />
              <label className="source-strategy">
                素材来源策略
                <select value={materialSourceStrategy} onChange={(event) => setMaterialSourceStrategy(event.target.value)}>
                  <option value="library_first">优先素材库，缺失时联网搜索</option>
                  <option value="library_only">仅使用素材库</option>
                  <option value="web_only">仅联网搜索</option>
                </select>
              </label>
              <div className="actions">
                <button onClick={saveScript}><Save size={18} /> 保存</button>
                <button className="primary" onClick={() => generateShots('so')}><Archive size={18} /> 生成分镜</button>
                <button
                  className="tencent-storyboard"
                  title="生成分镜并使用腾讯云联网图像搜索"
                  onClick={() => generateShots('tencent')}
                >
                  <Search size={18} /> 生成分镜
                </button>
              </div>
            </div>
          </section>
        )}

        {tab === 'storyboard' && (
          <section className="band">
            <div className="storyboard-actions">
              <div className="storyboard-action-buttons">
                <button
                  type="button"
                  disabled={busy || !shots.some((shot) => shot.selected_asset_id)}
                  onClick={cropSelectedImages}
                >
                  <Crop size={18} /> 一键裁剪选中图片
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
                <button
                  type="button"
                  disabled={busy || !shots.some((shot) => ['no_image', 'no_match', 'intent_failed', 'search_stopped'].includes(shot.status))}
                  onClick={() => retryFailedShots('so')}
                >
                  <RefreshCw size={18} /> 重新搜索失败分镜
                </button>
                <button
                  type="button"
                  className="tencent-storyboard"
                  disabled={busy || !shots.some((shot) => ['no_image', 'no_match', 'intent_failed', 'search_stopped'].includes(shot.status))}
                  title="使用腾讯云联网图像搜索重新搜索失败分镜"
                  onClick={() => retryFailedShots('tencent')}
                >
                  <Search size={18} /> 重新搜索失败分镜
                </button>
              </div>
              <small>优先保存已选图片；未选择时保存该镜头第一张，重复图片自动跳过。</small>
            </div>
            <SearchProgress progress={searchProgress} project={project} onStop={stopImageSearch} />
            <div className="shot-list">
              {shots.map((shot) => (
                <ShotCard
                  key={shot.id}
                  shot={shot}
                  assets={generatedAssetsByShot.get(shot.id) || []}
                  selectedAssetId={shot.selected_asset_id}
                  searchProgress={searchProgress}
                  project={project}
                  diagnostics={diagnosticsByShot.get(shot.id) || []}
                  onSelect={(assetId, assetSource) => selectAsset(shot.id, assetId, assetSource)}
                  onPreview={setPreviewAsset}
                  imagePrompt={imagePromptEditors[shot.id]}
                  onOpenImagePrompt={() => openImagePromptEditor(shot.id)}
                  onImagePromptChange={(prompt) => updateImagePrompt(shot.id, prompt)}
                  onCancelImagePrompt={() => closeImagePromptEditor(shot.id)}
                  onGenerate={(prompt) => generateImage(shot.id, prompt)}
                  onRetry={() => retryImageSearch(shot.id, 'so')}
                  onTencentRetry={() => retryImageSearch(shot.id, 'tencent')}
                  processingImage={processingImage}
                  generatingShotId={generatingShotId}
                  onCrop={(assetId) => processGeneratedImage(assetId, 'crop-square')}
                  onRemoveWatermark={(assetId) => processGeneratedImage(assetId, 'remove-watermark')}
                  onManualUpload={(file) => uploadManualShotImage(shot.id, file)}
                  onLibraryUpload={() => setLibraryPickerShotId(shot.id)}
                />
              ))}
            </div>
          </section>
        )}

        {tab === 'match' && (
          <section className="band">
            <div className="toolbar">
              <VoiceSelect value={voiceType} onChange={setVoiceType} />
              <SpeechRateSelect value={speechRate} onChange={setSpeechRate} />
              <button className="primary" onClick={generateVoiceAndSubtitles}><Mic size={18} /> 生成配音与字幕</button>
            </div>
            <div className="shot-list">
              {shots.map((shot) => (
                <div className="shot" key={shot.id}>
                  <ShotCard
                    shot={shot}
                    assets={generatedAssetsByShot.get(shot.id) || []}
                    selectedAssetId={shot.selected_asset_id}
                    searchProgress={searchProgress}
                    project={project}
                    diagnostics={diagnosticsByShot.get(shot.id) || []}
                    onSelect={(assetId, assetSource) => selectAsset(shot.id, assetId, assetSource)}
                    onPreview={setPreviewAsset}
                    imagePrompt={imagePromptEditors[shot.id]}
                    onOpenImagePrompt={() => openImagePromptEditor(shot.id)}
                    onImagePromptChange={(prompt) => updateImagePrompt(shot.id, prompt)}
                    onCancelImagePrompt={() => closeImagePromptEditor(shot.id)}
                    onGenerate={(prompt) => generateImage(shot.id, prompt)}
                    onRetry={() => retryImageSearch(shot.id, 'so')}
                    onTencentRetry={() => retryImageSearch(shot.id, 'tencent')}
                    processingImage={processingImage}
                    generatingShotId={generatingShotId}
                    onCrop={(assetId) => processGeneratedImage(assetId, 'crop-square')}
                    onRemoveWatermark={(assetId) => processGeneratedImage(assetId, 'remove-watermark')}
                    onManualUpload={(file) => uploadManualShotImage(shot.id, file)}
                    onLibraryUpload={() => setLibraryPickerShotId(shot.id)}
                  />
                  <select value={shot.selected_asset_id || ''} onChange={(e) => {
                    const options = selectableAssets.filter((item) => !item.shot_id || item.shot_id === shot.id);
                    const selected = options.find((asset) => asset.id === e.target.value);
                    selectAsset(shot.id, e.target.value, selected?.asset_source || 'web_search');
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
                <p>生成两行标题和视频封面，并从音乐库选择背景音乐。配乐设置会在 MP4 和剪映草稿导出时自动应用。</p>
              </div>

              <div className="title-section">
                <h3>第一步 · 生成标题</h3>
                <div className="title-inputs">
                  <label>
                    标题第一行
                    <input
                      value={titleLine1}
                      maxLength={9}
                      onChange={(event) => { setTitleLine1(event.target.value); setTitleConfirmed(false); }}
                      placeholder="5-9字，制造悬念"
                    />
                  </label>
                  <label>
                    标题第二行
                    <input
                      value={titleLine2}
                      maxLength={9}
                      onChange={(event) => { setTitleLine2(event.target.value); setTitleConfirmed(false); }}
                      placeholder="5-9字，揭示反差"
                    />
                  </label>
                </div>
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
                <p className="cover-layout-note">上传人物图片后，系统会将图片居中裁剪为 1:1 并添加圆角。图片顶部会预留安全间距，图片和两行标题会完整排在平台居中裁切的 3:4 区域内。</p>
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
                <div className="actions">
                  <button
                    className="primary"
                    disabled={busy || !titleConfirmed || !coverImage}
                    onClick={generateCover}
                  >
                    <ImagePlus size={18} /> {project.cover_url ? '重新合成封面' : '生成 9:16 封面'}
                  </button>
                </div>
              </div>

              <div className="music-section">
                <h3>第三步 · 设置背景音乐</h3>
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
              {project.cover_url ? (
                <>
                  <button
                    type="button"
                    className="cover-image-button"
                    onClick={() => setPreviewAsset({
                      file_url: project.cover_url,
                      updated_at: project.cover_updated_at,
                      file_name: '视频封面',
                    })}
                  >
                    <img
                      src={`${API}${project.cover_url}?v=${encodeURIComponent(project.cover_updated_at || '')}`}
                      alt="视频封面"
                    />
                  </button>
                  <button type="button" className="cover-download-button" onClick={downloadCover}>
                    <Download size={18} /> 下载封面
                  </button>
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
            <p>一次导出会同时生成 9:16 完整 MP4、按镜头编号的方形 PNG、配音字幕素材包和剪映草稿。方图居中显示，短字幕位于图片下方留白区。</p>
            {project.audio_url && <audio controls src={`${API}${project.audio_url}`} />}
            <div className="export-actions">
              <VoiceSelect value={voiceType} onChange={setVoiceType} />
              <SpeechRateSelect value={speechRate} onChange={setSpeechRate} />
              <button onClick={generateVoiceAndSubtitles}><Mic size={18} /> 重新生成配音字幕</button>
              <button onClick={() => setTab('cover')}><Music size={18} /> 标题封面与配乐</button>
              <button onClick={openExportFolder}><FolderOpen size={18} /> 打开导出文件夹</button>
              <button className="primary" onClick={() => exportPackage('mp4')}><Film size={18} /> 导出 MP4</button>
              <button className="primary" onClick={() => exportPackage('draft')}><Archive size={18} /> 导出剪映草稿</button>
            </div>
            {exportResult && (
              <div className="export-result-card">
                <div>
                  <strong>{exportResult.output === 'mp4' ? 'MP4 导出完成' : '剪映草稿导出完成'}</strong>
                  {exportResult.zip_file_name && <span>{exportResult.zip_file_name}</span>}
                  <small>保存位置：{exportResult.export_folder}</small>
                  {exportResult.verification?.jianying?.draft_path && (
                    <small>剪映草稿：{exportResult.verification.jianying.draft_path}</small>
                  )}
                </div>
                <div className="export-result-actions">
                  {exportResult.video_url && (
                    <button onClick={() => window.open(`${API}${exportResult.video_url}`, '_blank')}>
                      <Film size={18} /> 播放或下载 MP4
                    </button>
                  )}
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
      {previewAsset && <ImagePreview asset={previewAsset} onClose={() => setPreviewAsset(null)} />}
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
  function openPreview(event) {
    if (!canPreview) return;
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    onPreview();
  }
  return (
    <article className={`${asset.analysis_status === 'analyzing' ? 'asset-card analyzing' : 'asset-card'}${libraryCard ? ' library-asset-card' : ''}${selected ? ' selected' : ''}`}>
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
        {asset.file_type === 'image' ? <SafeImage src={src} alt={asset.file_name} /> : <video src={src} controls />}
        {imageTools}
      </div>
      <h3>{asset.file_name}</h3>
      {imageScore !== undefined && imageScore !== null && <p>图片评分：{imageScore}</p>}
      <p>{asset.analysis_status === 'analyzing' ? '识别中' : [...(asset.object || asset.people || []), ...(asset.scene || []), ...(asset.keywords || [])].slice(0, 6).join(' / ') || '待补充标签'}</p>
      <small>{asset.analysis_status === 'analyzing' ? '识别标签中，请稍候' : `${asset.analysis_provider || 'local_fallback'} · ${asset.copyright_note}`}</small>
      {imageReason && <small>{imageReason}</small>}
      {(onEdit || onDelete) && (
        <div className="asset-actions">
          {onEdit && <button disabled={asset.analysis_status === 'analyzing'} onClick={onEdit}><Tags size={16} /> 编辑标签</button>}
          {onDelete && <button className="danger" onClick={onDelete}><Trash2 size={16} /> 删除</button>}
        </div>
      )}
    </article>
  );
}

function SafeImage({ src, alt }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
  }, [src]);
  if (!src || broken) {
    return <div className="image-fallback">图片文件不可用</div>;
  }
  return <img src={src} alt={alt || ''} onError={() => setBroken(true)} />;
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

function diagnosticSummary(diagnostics) {
  const recent = (diagnostics || []).slice(0, 40);
  if (!recent.length) return '';
  const keywordRows = recent.filter((item) => item.type === 'keyword');
  const sourceRows = recent.filter((item) => item.type === 'source');
  const returned = sourceRows.reduce((sum, item) => sum + Number(item.returned || 0), 0);
  const attempts = keywordRows.reduce((sum, item) => sum + Number(item.attempted_downloads || 0), 0);
  const downloaded = keywordRows.reduce((sum, item) => sum + Number(item.downloaded || 0), 0);
  const rejected = {};
  keywordRows.forEach((row) => {
    Object.entries(row.rejected || {}).forEach(([reason, count]) => {
      rejected[reason] = (rejected[reason] || 0) + Number(count || 0);
    });
  });
  const rejectText = Object.entries(rejected)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([reason, count]) => `${reason}${count}`)
    .join(' / ');
  return `诊断：渠道返回 ${returned}，尝试下载 ${attempts}，成功 ${downloaded}${rejectText ? `，过滤 ${rejectText}` : ''}`;
}

function formatElapsed(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return minutes ? `${minutes}分${String(rest).padStart(2, '0')}秒` : `${rest}秒`;
}

function SearchProgress({ progress, project, onStop }) {
  const [tick, setTick] = useState(0);
  const stage = project?.search_stage || '';
  const searching = project?.status === 'searching_images';
  const stopping = searching && stage === 'stopping';
  const stopped = project?.status === 'search_stopped';
  const analyzingIntent = project?.status === 'searching_images' && stage === 'analyzing_intent';
  const startedAt = project?.intent_analysis_started_at ? new Date(project.intent_analysis_started_at).getTime() : 0;
  const elapsed = startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;
  const keywordEstimate = project?.intent_keyword_estimate || (progress.total ? `${progress.total}` : '');
  const batchInfo = project?.intent_batches_total
    ? `批次 ${project.intent_batches_completed || 0}/${project.intent_batches_total}`
    : `预计 ${keywordEstimate} 个关键词`;
  const statusText = analyzingIntent
    ? `${project?.current_search_keyword || `正在分析 ${progress.total || project?.search_total || '-'} 个分镜关键词`} · ${batchInfo} · 已运行 ${formatElapsed(elapsed)}`
    : `正在处理镜头 ${project?.current_shot_index || '-'} ${project?.current_search_keyword ? `· ${project.current_search_keyword}` : ''}`;
  const simpleProgressText = progress.total
    ? `已完成 ${progress.completed}/${progress.total} · ${stage === 'downloading' ? '正在下载候选图' : analyzingIntent ? '正在生成关键词' : '处理中'}`
    : '';
  const helperText = analyzingIntent
    ? 'GLM 正在按 10 个镜头一批生成关键词；每批完成后会立即保存，全部完成后自动进入逐镜头搜图。'
    : '';
  const label = progress.total
    ? `${progress.completed} / ${progress.total} 个分镜图片完成`
    : '等待生成分镜';

  useEffect(() => {
    if (!analyzingIntent) return undefined;
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [analyzingIntent]);

  return (
    <div className="progress-panel">
      <div className="progress-row">
        <strong>
          分镜图片进度
          <span className={project?.image_search_provider === 'tencent' ? 'provider-badge tencent' : 'provider-badge'}>
            {project?.image_search_provider === 'tencent' ? '腾讯云联网搜图' : '360 图片'}
          </span>
        </strong>
        <div className="progress-actions">
          <span>{label}</span>
          <span className="progress-count success">成功 {progress.success}</span>
          <span className="progress-count failed">失败 {progress.failed}</span>
          {searching && (
            <button type="button" className="danger compact-button" disabled={stopping} onClick={onStop}>
              <RefreshCw size={16} /> {stopping ? '停止中' : '停止搜索'}
            </button>
          )}
        </div>
      </div>
      <div className="progress-track">
        <div
          className={[
            'progress-fill',
            searching ? 'active' : '',
            analyzingIntent ? 'indeterminate' : '',
            stopped ? 'stopped' : '',
          ].filter(Boolean).join(' ')}
          style={{ width: `${analyzingIntent ? 18 + (tick % 5) * 3 : progress.percent}%` }}
        />
      </div>
      {project?.status === 'searching_images' && (
        <div className="progress-detail">
          <small>{statusText}</small>
          {simpleProgressText && <small>{simpleProgressText}</small>}
          {helperText && <small>{helperText}</small>}
        </div>
      )}
      {project?.status === 'search_failed' && (
        <div className="progress-detail error">
          <small>{project.current_search_keyword || '关键词分析失败'}</small>
          <small>{project.search_error || 'GLM 没有返回有效结果，请稍后重试。'}</small>
        </div>
      )}
      {project?.status === 'search_stopped' && (
        <div className="progress-detail">
          <small>图片搜索已停止</small>
          <small>已完成的候选图会保留，未处理镜头可稍后重新搜索。</small>
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
              <button type="button" className="primary" onClick={downloadPng}>
                <Download size={17} /> 下载 PNG
              </button>
            )}
            <button type="button" onClick={onClose}>关闭</button>
          </div>
        </div>
        <SafeImage src={src} alt={asset.file_name || 'preview'} />
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
        <label>主体标签<input value={form.object} onChange={(e) => update('object', e.target.value)} placeholder="钱学森，火车，纪念碑" /></label>
        <label>场景标签<input value={form.scene} onChange={(e) => update('scene', e.target.value)} placeholder="实验室，会议室，戈壁滩" /></label>
        <label>关键词<input value={form.keywords} onChange={(e) => update('keywords', e.target.value)} placeholder="归国科学家，留学回国" /></label>
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
        <TagFields form={form} update={update} />
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
    <div className="modal-backdrop">
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
  searchProgress,
  project,
  diagnostics = [],
  onSelect,
  onPreview,
  imagePrompt,
  onOpenImagePrompt,
  onImagePromptChange,
  onCancelImagePrompt,
  onGenerate,
  onRetry,
  onTencentRetry,
  processingImage,
  generatingShotId,
  onCrop,
  onRemoveWatermark,
  onManualUpload,
  onLibraryUpload,
}) {
  const manualUploadRef = useRef(null);
  const isGeneratingImage = generatingShotId === shot.id;
  const visibleAssets = [...assets]
    .sort((a, b) => (b.id === selectedAssetId ? 1 : 0) - (a.id === selectedAssetId ? 1 : 0))
    .slice(0, 2);
  const placeholders = Math.max(0, 2 - visibleAssets.length);
  const canRetry = !['pending_search', 'analyzing_intent', 'searching'].includes(shot.status);
  const isSearchingShot = shot.status === 'searching';
  const isWaitingShot = ['pending_search', 'analyzing_intent'].includes(shot.status);
  const progressText = searchProgress?.total
    ? `${searchProgress.completed}/${searchProgress.total}`
    : '';
  const placeholderText = isSearchingShot ? '搜索中' : isWaitingShot ? '等待中' : '暂无图片';
  const placeholderHint = isSearchingShot
    ? `${progressText ? `进度 ${progressText}` : '正在拉取候选图'}${shot.current_search_keyword ? ` · ${shot.current_search_keyword}` : ''}`
    : isWaitingShot
      ? '等待关键词或前序镜头'
      : '可重新搜索或生成占位图';
  const diagnosticText = diagnosticSummary(diagnostics);
  return (
    <article className="shot-card">
      <div className="shot-main">
        <span className={`pill ${shot.status}`}>镜头 {shot.shot_index} · {shot.status}</span>
        <h3>{shot.voice_text}</h3>
        <p>画面描述：{shot.visual_need || '暂无描述'}</p>
        <p>主体标签：{((shot.material_intent && shot.material_intent.objects) || []).join(' / ') || '—'}</p>
        <p>场景标签：{((shot.material_intent && shot.material_intent.scenes) || []).join(' / ') || '—'}</p>
        <p>关键词：{((shot.material_intent && shot.material_intent.keywords) || []).join(' / ') || '—'}</p>
        <p>搜索关键词：{(shot.search_keywords || []).join(' / ') || shot.current_search_keyword || '—'}</p>
        {diagnosticText && <p className="shot-diagnostic">{diagnosticText}</p>}
      </div>
      <div className="shot-side">
        <div className="shot-search-actions">
          <button onClick={onRetry} disabled={!canRetry}><RefreshCw size={18} /> 重新搜索</button>
          <button
            className="tencent-storyboard"
            title="使用腾讯云联网图像搜索重新搜索"
            onClick={onTencentRetry}
            disabled={!canRetry}
          >
            <Search size={18} /> 重新搜索
          </button>
        </div>
        <div className="shot-images">
          {imagePrompt !== undefined && !isGeneratingImage && (
            <div className="ai-prompt-editor">
              <strong>编辑 AI 图片提示词</strong>
              <textarea
                value={imagePrompt}
                onChange={(event) => onImagePromptChange?.(event.target.value)}
                placeholder="输入希望 Seedream 生成的画面描述"
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
              <small>Seedream 正在绘制 1:1 图片，请稍候</small>
            </div>
          )}
          {visibleAssets.map((item) => (
            <div className={item.id === selectedAssetId ? 'image-choice selected' : 'image-choice'} key={item.id}>
              <div
                className="image-choice-main"
                role="button"
                tabIndex={0}
                onClick={() => {
                  onSelect?.(item.id, item.asset_source || 'web_search');
                  onPreview?.(item);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect?.(item.id, item.asset_source || 'web_search');
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
                        title="居中裁剪为 1:1"
                        aria-label="裁剪为 1:1"
                        disabled={Boolean(processingImage)}
                        onClick={(event) => {
                          event.stopPropagation();
                          onCrop?.(item.id);
                        }}
                      >
                        <Crop size={16} />
                      </button>
                      <button
                        type="button"
                        title="使用 Seedream 去除水印"
                        aria-label="Seedream 去水印"
                        disabled={Boolean(processingImage)}
                        onClick={(event) => {
                          event.stopPropagation();
                          onRemoveWatermark?.(item.id);
                        }}
                      >
                        <Eraser size={16} />
                      </button>
                        </>
                      )}
                      <button
                        type="button"
                        className="baidu-image-button"
                        title={`打开百度图片搜索：${(shot.search_keywords || [shot.current_search_keyword]).filter(Boolean)[0] || ''}`}
                        aria-label="打开百度图片搜索"
                        onClick={(event) => {
                          event.stopPropagation();
                          const keyword = (shot.search_keywords || [shot.current_search_keyword]).filter(Boolean)[0] || '';
                          window.open(
                            `https://image.baidu.com/search/index?tn=baiduimage&word=${encodeURIComponent(keyword)}`,
                            '_blank',
                            'noopener,noreferrer',
                          );
                        }}
                      >
                        <span className="baidu-mark">百</span>
                      </button>
                    </div>
                  )}
                />
              </div>
            </div>
          ))}
          {Array.from({ length: placeholders }).map((_, index) => (
            <div className={isSearchingShot ? 'search-placeholder active' : 'search-placeholder'} key={`placeholder-${index}`}>
              <strong>{placeholderText}</strong>
              <small>{placeholderHint}</small>
              {!isSearchingShot && !isWaitingShot && (
                <button
                  type="button"
                  className="placeholder-baidu-button"
                  title="使用搜索关键词打开百度图片搜索"
                  onClick={() => {
                    const keyword = (shot.search_keywords || [shot.current_search_keyword]).filter(Boolean)[0] || '';
                    window.open(
                      `https://image.baidu.com/search/index?tn=baiduimage&word=${encodeURIComponent(keyword)}`,
                      '_blank',
                      'noopener,noreferrer',
                    );
                  }}
                >
                  <span className="baidu-mark">百</span> 百度搜图
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="shot-bottom-actions">
          <button
            className={isGeneratingImage ? 'ai-generate-button active' : 'ai-generate-button'}
            onClick={onOpenImagePrompt}
            disabled={isGeneratingImage}
          >
            <Wand2 size={18} /> {isGeneratingImage ? 'AI 生成中' : imagePrompt !== undefined ? '编辑提示词中' : 'AI 占位图'}
          </button>
          <button type="button" onClick={() => manualUploadRef.current?.click()}>
            <ImagePlus size={18} /> 上传下载图片
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
