import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Archive, Download, Film, FolderOpen, ImagePlus, Library, Mic, RefreshCw, Save, Scissors, Search, Tags, Terminal, Trash2, Wand2 } from 'lucide-react';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const VOICE_OPTIONS = [
  { value: 'zh_male_m191_uranus_bigtts', label: '男声 · 沉稳叙事' },
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
    source_note: '用户上传',
    copyright_note: '自用素材',
  };
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
  const [editingAsset, setEditingAsset] = useState(null);
  const [pendingUpload, setPendingUpload] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [voiceType, setVoiceType] = useState(VOICE_OPTIONS[0].value);
  const [previewAsset, setPreviewAsset] = useState(null);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [consoleStream, setConsoleStream] = useState('image_search');
  const [consoleLog, setConsoleLog] = useState('');
  const [consoleMeta, setConsoleMeta] = useState(null);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const folderFallbackRef = useRef(null);
  const uploadInputRef = useRef(null);
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
    map.forEach((list) => list.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || ''))));
    return map;
  }, [generatedAssets]);
  const searchProgress = useMemo(() => {
    const total = shots.length || project?.search_total || 0;
    const completedByShots = shots.filter((shot) => ['web_downloaded', 'no_image', 'ai_generated'].includes(shot.status)).length;
    const completed = Math.min(total || project?.search_total || 0, Math.max(completedByShots, project?.search_completed || 0));
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    return { total, completed, percent };
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
    const [projectList, assetList, libraryData] = await Promise.all([
      request('/api/projects'),
      request('/api/assets'),
      request('/api/assets/library'),
    ]);
    setProjects(projectList.projects);
    setAssets(assetList.assets);
    setLibrary(validLibrary(libraryData.library) ? libraryData.library : null);
    if (id) {
      const data = await request(`/api/projects/${id}`);
      setProject(data.project);
      setShots(data.shots);
      setGeneratedAssets(data.generated_assets || []);
      setWebImageDiagnostics(data.web_image_diagnostics || []);
      setProjectId(id);
    } else {
      setProject(null);
      setShots([]);
      setGeneratedAssets([]);
      setWebImageDiagnostics([]);
      setProjectId('');
    }
  }

  async function refreshConsoleLog(stream = consoleStream) {
    const data = await request(`/api/system/logs?stream=${stream}&max_chars=20000`);
    setConsoleLog(data.content || '');
    setConsoleMeta(data);
  }

  useEffect(() => {
    if (!consoleOpen) return undefined;
    refreshConsoleLog(consoleStream).catch((err) => setConsoleLog(String(err.message || err)));
    const timer = window.setInterval(() => {
      refreshConsoleLog(consoleStream).catch((err) => setConsoleLog(String(err.message || err)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [consoleOpen, consoleStream]);

  useEffect(() => {
    refreshAll();
  }, []);

  useEffect(() => {
    if (!assets.some((asset) => asset.analysis_status === 'analyzing')) return undefined;
    const timer = window.setInterval(() => {
      refreshAll(projectId);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [assets, projectId]);

  useEffect(() => {
    const activeStatuses = ['pending_search', 'analyzing_intent', 'searching'];
    if (!projectId || !shots.some((shot) => activeStatuses.includes(shot.status)) && project?.status !== 'searching_images') return undefined;
    const timer = window.setInterval(() => {
      refreshAll(projectId);
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
      setMessage('分镜图片处理完成');
    }
    lastProjectStatusRef.current = current;
  }, [project?.status, project?.search_error]);

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
    const data = await run('创建项目', () => request('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }));
    if (data?.project_id) {
      await refreshAll(data.project_id);
      setTab('script');
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
      await request(`/api/projects/${projectId}/shots?image_search_provider=${imageSearchProvider}`, { method: 'POST' });
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
    await refreshAll(projectId);
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

  async function generateImage(shotId) {
    await run('生成占位图', () => request(`/api/projects/${projectId}/shots/${shotId}/generate-image`, { method: 'POST' }));
    await refreshAll(projectId);
  }

  async function retryImageSearch(shotId) {
    await run('重新搜索图片', () => request(`/api/projects/${projectId}/shots/${shotId}/retry-image-search`, { method: 'POST' }));
    await refreshAll(projectId);
  }

  async function selectAsset(shotId, assetId, assetSource = 'web_search') {
    await run('指定素材', () => request(`/api/projects/${projectId}/shots/${shotId}/asset`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: assetId, asset_source: assetSource }),
    }));
    await refreshAll(projectId);
  }

  async function generateVoiceAndSubtitles() {
    const voiceResult = await run('生成配音', () => request(`/api/projects/${projectId}/generate-voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_type: voiceType }),
    }));
    if (!voiceResult) return false;
    const subtitleResult = await run('生成字幕', () => request(`/api/projects/${projectId}/generate-subtitles`, { method: 'POST' }));
    if (!subtitleResult) return false;
    await refreshAll(projectId);
    setTab('export');
    return true;
  }

  async function exportPackage() {
    if (!project?.audio_url) {
      const generated = await generateVoiceAndSubtitles();
      if (!generated) return;
    }
    const data = await run('导出素材包', () => request(`/api/projects/${projectId}/export/assets`, { method: 'POST' }));
    if (data?.download_url) window.open(`${API}${data.download_url}`, '_blank');
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
            <button type="button" onClick={() => setConsoleOpen(true)}><Terminal size={18} /> 后端控制台</button>
            <span className={workflowBusy ? 'status busy' : 'status'}>{workflowMessage}</span>
          </div>
        </header>

        {tab === 'create' && (
          <section className="band two-col">
            <form onSubmit={createProject} className="panel">
              <h2>创建项目</h2>
              <label>项目名称<input name="name" placeholder="可留空，系统自动取标题" /></label>
              <label>原始文案<textarea name="raw_script" required rows="12" placeholder="粘贴历史人物/纪实解说文案" /></label>
              <button className="primary"><Save size={18} /> 创建并进入文案</button>
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
                  <button className="primary" type="button" onClick={() => uploadInputRef.current?.click()}><ImagePlus size={18} /> 选择素材</button>
                </div>
                <div className="asset-grid">
                  {assets.map((asset) => (
                    <AssetCard
                      key={asset.id}
                      asset={asset}
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
              <textarea readOnly value={project.raw_script} rows="22" />
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
              <textarea value={project.rewritten_script || ''} rows="22" onChange={(e) => setProject({ ...project, rewritten_script: e.target.value })} />
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
                  onSelect={(assetId) => selectAsset(shot.id, assetId, 'web_search')}
                  onPreview={setPreviewAsset}
                  onGenerate={() => generateImage(shot.id)}
                  onRetry={() => retryImageSearch(shot.id)}
                />
              ))}
            </div>
          </section>
        )}

        {tab === 'match' && (
          <section className="band">
            <div className="toolbar">
              <VoiceSelect value={voiceType} onChange={setVoiceType} />
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
                    onSelect={(assetId) => selectAsset(shot.id, assetId, 'web_search')}
                    onPreview={setPreviewAsset}
                    onGenerate={() => generateImage(shot.id)}
                    onRetry={() => retryImageSearch(shot.id)}
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

        {tab === 'export' && (
          <section className="band result">
            <h2>结果导出</h2>
            <p>素材包包含 raw_script、rewritten_script、storyboard.json/csv、subtitles.srt、timeline.json、asset_match_report.json、已选真实素材、AI 占位图和 main_voice.mp3。</p>
            {project.audio_url && <audio controls src={`${API}${project.audio_url}`} />}
            <div className="actions">
              <VoiceSelect value={voiceType} onChange={setVoiceType} />
              <button onClick={generateVoiceAndSubtitles}><Mic size={18} /> 重新生成音频字幕</button>
              <button className="primary" onClick={exportPackage}><Download size={18} /> 导出 ZIP</button>
            </div>
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
      {consoleOpen && (
        <BackendConsole
          stream={consoleStream}
          content={consoleLog}
          meta={consoleMeta}
          onStreamChange={setConsoleStream}
          onRefresh={() => refreshConsoleLog(consoleStream)}
          onClose={() => setConsoleOpen(false)}
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

function AssetCard({ asset, onEdit, onDelete }) {
  const imageScore = asset.score_result?.score ?? asset.match_score;
  const imageReason = asset.score_result?.reason;
  const src = asset.file_url ? `${API}${asset.file_url}` : '';
  return (
    <article className={asset.analysis_status === 'analyzing' ? 'asset-card analyzing' : 'asset-card'}>
      <div className="preview">{asset.file_type === 'image' ? <SafeImage src={src} alt={asset.file_name} /> : <video src={src} controls />}</div>
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

function BackendConsole({ stream, content, meta, onStreamChange, onRefresh, onClose }) {
  const logRef = useRef(null);
  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [content]);
  return (
    <div className="console-panel">
      <div className="console-head">
        <strong><Terminal size={18} /> 后端控制台</strong>
        <div className="console-tools">
          <select value={stream} onChange={(e) => onStreamChange(e.target.value)}>
            <option value="image_search">图片搜索日志</option>
            <option value="runtime">服务运行日志</option>
            <option value="errors">错误输出</option>
          </select>
          <button type="button" onClick={onRefresh}><RefreshCw size={16} /> 刷新</button>
          <button type="button" onClick={onClose}>关闭</button>
        </div>
      </div>
      <small>
        <span className={meta?.exists ? 'console-status online' : 'console-status'} />
        {meta?.exists ? '每 2 秒自动刷新' : '日志文件还没有生成'}
      </small>
      <pre ref={logRef}>{content || '暂无日志输出'}</pre>
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
  const src = asset.file_url ? `${API}${asset.file_url}` : '';
  return (
    <div className="image-preview-backdrop" onClick={onClose}>
      <div className="image-preview" onClick={(e) => e.stopPropagation()}>
        <div className="image-preview-head">
          <strong>{asset.file_name || '图片预览'}</strong>
          <button type="button" onClick={onClose}>关闭</button>
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
        <label>主体标签<input value={form.object} onChange={(e) => update('object', e.target.value)} placeholder="钱学森，火车，纪念碑，动物" /></label>
        <label>场景标签<input value={form.scene} onChange={(e) => update('scene', e.target.value)} placeholder="实验室，会议，老照片" /></label>
        <label>关键词<input value={form.keywords} onChange={(e) => update('keywords', e.target.value)} placeholder="中国科学家，历史照片" /></label>
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

function ShotCard({ shot, assets = [], selectedAssetId, searchProgress, project, diagnostics = [], onSelect, onPreview, onGenerate, onRetry }) {
  const visibleAssets = [...assets]
    .sort((a, b) => (b.id === selectedAssetId ? 1 : 0) - (a.id === selectedAssetId ? 1 : 0))
    .slice(0, 2);
  const placeholders = Math.max(0, 2 - visibleAssets.length);
  const canRetry = (shot.search_attempts || 0) < 2 && !['pending_search', 'analyzing_intent', 'searching'].includes(shot.status);
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
        <p>核心关键词：{(shot.search_keywords || []).join(' / ') || shot.current_search_keyword || '生成中'}</p>
        {diagnosticText && <p className="shot-diagnostic">{diagnosticText}</p>}
      </div>
      <div className="shot-side">
        <button onClick={onRetry} disabled={!canRetry}><RefreshCw size={18} /> 重新搜索</button>
        <div className="shot-images">
          {visibleAssets.map((item) => (
            <button
              type="button"
              className={item.id === selectedAssetId ? 'image-choice selected' : 'image-choice'}
              key={item.id}
              onClick={() => {
                onSelect?.(item.id);
                onPreview?.(item);
              }}
              title="选择并预览这张图"
            >
              <AssetCard asset={item} />
            </button>
          ))}
          {Array.from({ length: placeholders }).map((_, index) => (
            <div className={isSearchingShot ? 'search-placeholder active' : 'search-placeholder'} key={`placeholder-${index}`}>
              <strong>{placeholderText}</strong>
              <small>{placeholderHint}</small>
            </div>
          ))}
        </div>
        <button onClick={onGenerate}><Wand2 size={18} /> AI 占位图</button>
      </div>
    </article>
  );
}

createRoot(document.getElementById('root')).render(<App />);
