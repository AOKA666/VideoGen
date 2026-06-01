import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Archive, Download, Film, FolderOpen, ImagePlus, Library, Mic, RefreshCw, Save, Scissors, Search, Tags, Trash2, Wand2 } from 'lucide-react';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

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
  const [library, setLibrary] = useState(null);
  const [editingAsset, setEditingAsset] = useState(null);
  const [pendingUpload, setPendingUpload] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const folderFallbackRef = useRef(null);
  const uploadInputRef = useRef(null);

  const activeLibrary = validLibrary(library) ? library : null;
  const selectedAssets = useMemo(() => {
    const map = new Map();
    assets.forEach((asset) => map.set(asset.id, asset));
    return map;
  }, [assets]);

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
      setProjectId(id);
    }
  }

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

  async function generateShots() {
    await saveScript();
    await run('生成分镜', () => request(`/api/projects/${projectId}/shots`, { method: 'POST' }));
    await refreshAll(projectId);
    setTab('storyboard');
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

  async function matchAssets() {
    await run('匹配真实素材', () => request(`/api/projects/${projectId}/match-assets`, { method: 'POST' }));
    await refreshAll(projectId);
    setTab('match');
  }

  async function generateImage(shotId) {
    await run('生成占位图', () => request(`/api/projects/${projectId}/shots/${shotId}/generate-image`, { method: 'POST' }));
    await refreshAll(projectId);
  }

  async function selectAsset(shotId, assetId) {
    await run('指定素材', () => request(`/api/projects/${projectId}/shots/${shotId}/asset`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: assetId, asset_source: 'local' }),
    }));
    await refreshAll(projectId);
  }

  async function generateVoiceAndSubtitles() {
    await run('生成配音', () => request(`/api/projects/${projectId}/generate-voice`, { method: 'POST' }));
    await run('生成字幕', () => request(`/api/projects/${projectId}/generate-subtitles`, { method: 'POST' }));
    setTab('export');
  }

  async function exportPackage() {
    const data = await run('导出素材包', () => request(`/api/projects/${projectId}/export/assets`, { method: 'POST' }));
    if (data?.download_url) window.open(`${API}${data.download_url}`, '_blank');
  }

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
        <select value={projectId} onChange={(e) => refreshAll(e.target.value)}>
          <option value="">选择项目</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <h1>{project?.name || '真实素材优先的短视频草稿工作台'}</h1>
            <p>{activeLibrary ? `当前素材库：${activeLibrary.name}` : '进入素材库前，需要先选择一个文件夹作为素材库。'}</p>
          </div>
          <span className={busy ? 'status busy' : 'status'}>{message || '就绪'}</span>
        </header>

        {tab === 'create' && (
          <section className="band two-col">
            <form onSubmit={createProject} className="panel">
              <h2>创建项目</h2>
              <label>项目名称<input name="name" placeholder="可留空，系统自动取标题" /></label>
              <label>原始文案<textarea name="raw_script" required rows="12" placeholder="粘贴历史人物/纪实解说文案" /></label>
              <div className="grid">
                <label>内容类型<select name="content_type" defaultValue="国之脊梁"><option>国之脊梁</option><option>历史人物</option><option>科学家故事</option><option>两弹一星</option><option>纪实解说</option></select></label>
                <label>改写强度<select name="rewrite_level" defaultValue="medium"><option value="light">轻度</option><option value="medium">中度</option><option value="strong">强度</option></select></label>
                <label>文案风格<select name="script_style" defaultValue="纪实故事型"><option>纪实故事型</option><option>爆款悬念型</option><option>情绪感染型</option><option>视频号中老年风格</option></select></label>
                <label>配音风格<select name="voice_style" defaultValue="沉稳男声"><option>沉稳男声</option><option>温和女声</option><option>纪录片旁白</option></select></label>
              </div>
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
              <textarea value={project.rewritten_script || ''} rows="22" onChange={(e) => setProject({ ...project, rewritten_script: e.target.value })} />
              <div className="actions"><button onClick={saveScript}><Save size={18} /> 保存</button><button className="primary" onClick={generateShots}><Archive size={18} /> 生成分镜</button></div>
            </div>
          </section>
        )}

        {tab === 'storyboard' && (
          <section className="band">
            <div className="toolbar"><button className="primary" onClick={matchAssets}><Search size={18} /> 匹配素材库</button></div>
            <div className="shot-list">
              {shots.map((shot) => <ShotCard key={shot.id} shot={shot} asset={selectedAssets.get(shot.selected_asset_id)} onGenerate={() => generateImage(shot.id)} />)}
            </div>
          </section>
        )}

        {tab === 'match' && (
          <section className="band">
            <div className="toolbar"><button onClick={matchAssets}><RefreshCw size={18} /> 重新匹配</button><button className="primary" onClick={generateVoiceAndSubtitles}><Mic size={18} /> 生成配音与字幕</button></div>
            <div className="shot-list">
              {shots.map((shot) => (
                <div className="shot" key={shot.id}>
                  <ShotCard shot={shot} asset={selectedAssets.get(shot.selected_asset_id)} onGenerate={() => generateImage(shot.id)} />
                  <select value={shot.selected_asset_id || ''} onChange={(e) => selectAsset(shot.id, e.target.value)}>
                    <option value="">手动选择素材</option>
                    {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.file_name}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </section>
        )}

        {tab === 'export' && (
          <section className="band result">
            <h2>结果导出</h2>
            <p>素材包包含 raw_script、rewritten_script、storyboard.json/csv、subtitles.srt、timeline.json、asset_match_report.json、已选真实素材、AI 占位图和 main_voice.wav。</p>
            <div className="actions"><button onClick={generateVoiceAndSubtitles}><Mic size={18} /> 重新生成音频字幕</button><button className="primary" onClick={exportPackage}><Download size={18} /> 导出 ZIP</button></div>
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
    </div>
  );
}

function AssetCard({ asset, onEdit, onDelete }) {
  return (
    <article className={asset.analysis_status === 'analyzing' ? 'asset-card analyzing' : 'asset-card'}>
      <div className="preview">{asset.file_type === 'image' ? <img src={`${API}${asset.file_url}`} /> : <video src={`${API}${asset.file_url}`} controls />}</div>
      <h3>{asset.file_name}</h3>
      <p>{asset.analysis_status === 'analyzing' ? '识别中' : [...(asset.object || asset.people || []), ...(asset.scene || []), ...(asset.keywords || [])].slice(0, 6).join(' / ') || '待补充标签'}</p>
      <div className="asset-meta">
        <span>方向：{asset.orientation || 'unknown'}</span>
        <span>质量：{asset.quality_score ?? 75}</span>
      </div>
      <small>{asset.analysis_status === 'analyzing' ? '识别标签中，请稍候' : `${asset.analysis_provider || 'local_fallback'} · ${asset.copyright_note}`}</small>
      {(onEdit || onDelete) && (
        <div className="asset-actions">
          {onEdit && <button disabled={asset.analysis_status === 'analyzing'} onClick={onEdit}><Tags size={16} /> 编辑标签</button>}
          {onDelete && <button className="danger" onClick={onDelete}><Trash2 size={16} /> 删除</button>}
        </div>
      )}
    </article>
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
          {asset.file_type === 'image' ? <img src={`${API}${asset.file_url}`} /> : <video src={`${API}${asset.file_url}`} controls />}
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

function ShotCard({ shot, asset, onGenerate }) {
  return (
    <article className="shot-card">
      <div className="shot-main">
        <span className={`pill ${shot.status}`}>镜头 {shot.shot_index} · {shot.status}</span>
        <h3>{shot.voice_text}</h3>
        <p>画面需求：{shot.visual_need}</p>
        <p>关键词：{[...(shot.exact_keywords || []), ...(shot.alternative_keywords || []), ...(shot.atmosphere_keywords || [])].join(' / ')}</p>
      </div>
      <div className="shot-side">
        {asset ? <AssetCard asset={asset} /> : <div className="empty">暂无真实素材</div>}
        <button onClick={onGenerate}><Wand2 size={18} /> AI 占位图</button>
      </div>
    </article>
  );
}

createRoot(document.getElementById('root')).render(<App />);
