# 《真实素材优先的 AI 短视频草稿生成器》需求文档 V1

## 1. 产品定位

### 1.1 产品名称，暂定

**国之脊梁视频草稿生成器**

备选名称：

* 历史人物短视频素材匹配助手
* AI 历史口播视频草稿助手
* 真实素材优先的短视频生成器
* 文案转剪映草稿助手

### 1.2 一句话定位

用户输入一段历史人物/国之脊梁类原始文案，系统自动完成二创、分镜，并优先从本地真实素材库中匹配图片或视频素材；当素材库中没有合适画面时，再使用 AI 生图补位，最后生成配音、字幕、素材包，并支持后续导出剪映草稿。

### 1.3 核心差异化

市面上很多工具是：

```text
文案 → AI 生图 → 配音 → 成片
```

本工具的核心差异是：

```text
文案 → 二创 → 分镜 → 优先匹配真实素材 → 匹配不到才 AI 生图 → 配音字幕 → 草稿导出
```

也就是说，本工具不是简单的 AI 图片生成器，而是一个面向历史解说类短视频的 **真实素材匹配 + AI 兜底成片工作流工具**。

---

## 2. 目标用户

### 2.1 第一阶段目标用户

优先服务以下用户：

```text
1. 做视频号历史人物内容的人
2. 做“国之脊梁”类短视频的人
3. 做钱学森、邓稼先、于敏、黄旭华、两弹一星等人物故事的人
4. 做纪实解说、人物故事、爱国题材短视频的人
5. 有一批本地素材，但剪辑效率低的内容创作者
```

### 2.2 暂不服务的人群

V1 暂时不重点服务：

```text
1. 泛娱乐短视频创作者
2. 带货视频创作者
3. 剧情短剧创作者
4. 自动混剪影视片段的人
5. 需要全网自动抓取素材的人
```

---

## 3. 核心使用场景

用户有一段原始文案：

```text
钱学森到底有多重要？
上世纪60年代，中央曾专门为他设下一项连十大元帅都不曾拥有的特殊保护待遇……
```

用户希望系统自动完成：

```text
1. 改写成适合短视频口播的版本
2. 拆成一个个视频分镜
3. 每个分镜自动生成素材搜索关键词
4. 优先从本地真实素材库匹配画面
5. 如果真实素材不合适，再用 AI 生成补位图
6. 生成 AI 配音
7. 生成字幕文件
8. 导出素材包
9. 后续支持导出剪映草稿
```

---

## 4. V1 产品边界

### 4.1 V1 必须实现

| 模块      | 是否必须 | 说明                |
| ------- | ---: | ----------------- |
| 原始文案输入  |   必须 | 用户粘贴文案            |
| AI 文案二创 |   必须 | 改写成短视频口播稿         |
| 文案人工编辑  |   必须 | 用户可修改 AI 输出       |
| 自动分镜    |   必须 | 将口播稿拆成镜头          |
| 分镜人工编辑  |   必须 | 用户可编辑每个镜头         |
| 本地素材库上传 |   必须 | 用户上传图片/视频素材       |
| 素材自动打标签 |   必须 | 根据素材内容生成标签        |
| 素材检索与匹配 |   必须 | 根据分镜匹配真实素材        |
| AI 生图兜底 |   必须 | 匹配不到素材时生成图片       |
| AI 配音   |   必须 | 根据最终文案生成旁白        |
| 字幕生成    |   必须 | 生成 SRT 字幕         |
| 素材包导出   |   必须 | 导出图片/视频/音频/字幕/分镜表 |
| 剪映草稿导出  | V1.1 | V1 可预留接口，优先素材包导出  |

### 4.2 V1 暂不做

| 功能            | 暂不做原因         |
| ------------- | ------------- |
| 自动抓取抖音/B站视频   | 平台反爬、版权和风控风险高 |
| 自动下载纪录片素材     | 版权风险高         |
| 自动全网搜索并直接使用素材 | 版权不可控         |
| 自动识别并剪切长视频片段  | 技术复杂度高        |
| 自动 BGM 卡点     | 非核心需求         |
| 多角色配音         | V1 不必要        |
| 复杂转场/花字/特效    | 剪映草稿兼容复杂      |
| 一键发布到视频号/抖音   | 平台接口和审核复杂     |
| 移动端剪映草稿       | V1 优先桌面端      |

---

## 5. 产品主流程

### 5.1 总流程

```text
用户输入原始文案
        ↓
AI 二创文案
        ↓
用户确认/编辑二创文案
        ↓
AI 自动分镜
        ↓
AI 为每个分镜生成素材搜索意图
        ↓
系统从本地素材库匹配真实素材
        ↓
用户确认素材匹配结果
        ↓
匹配不到的镜头使用 AI 生图补位
        ↓
生成 AI 配音
        ↓
生成字幕
        ↓
生成时间轴
        ↓
导出素材包
        ↓
后续导出剪映草稿
```

### 5.2 核心原则

```text
1. 优先使用真实素材
2. 真实素材不足时再用 AI 图兜底
3. AI 图不直接替代真实素材，而是作为补位方案
4. 历史人物内容尽量少生成真实人物正脸
5. 用户必须能人工确认和修改分镜、素材、字幕
```

---

## 6. 页面结构

## 6.1 页面一：项目创建页

### 页面目标

让用户输入原始文案，并选择生成配置。

### 页面字段

| 字段       | 类型       | 是否必填 | 默认值    | 说明          |
| -------- | -------- | ---: | ------ | ----------- |
| 项目名称     | input    |    否 | 自动取标题  | 用于素材包和草稿命名  |
| 原始文案     | textarea |    是 | 空      | 用户粘贴文案      |
| 内容类型     | select   |    是 | 国之脊梁   | 控制二创和分镜风格   |
| 文案改写强度   | radio    |    是 | 中度改写   | 轻度/中度/强度    |
| 文案风格     | select   |    是 | 纪实故事型  | 控制口播表达      |
| 素材匹配优先级  | radio    |    是 | 真实素材优先 | V1 固定真实素材优先 |
| AI 兜底方式  | select   |    是 | AI 生图  | 匹配不到时使用     |
| 配音风格     | select   |    是 | 沉稳男声   | 控制 TTS      |
| 视频比例     | radio    |    是 | 9:16   | 默认竖屏        |
| 是否生成字幕   | switch   |    是 | 开启     | 默认生成 SRT    |
| 是否生成剪映草稿 | switch   |    否 | 关闭     | V1.1 再开放    |

### 内容类型选项

```text
国之脊梁
历史人物
科学家故事
两弹一星
纪实解说
人物传记
```

### 文案风格选项

```text
纪实故事型
爆款悬念型
情绪感染型
视频号中老年风格
抖音强钩子风格
```

### AI 兜底方式选项

V1 只做：

```text
AI 生图补位
```

后续可扩展：

```text
通用图库素材补位
历史氛围图补位
```

---

## 6.2 页面二：本地素材库管理页

### 页面目标

让用户上传和管理自己的真实素材。

### 素材类型

V1 支持：

```text
图片：jpg / jpeg / png / webp
视频：mp4 / mov
```

### 素材上传方式

```text
1. 单个文件上传
2. 批量文件上传
3. 文件夹上传，后续版本
```

### 素材库字段

| 字段      | 说明              |
| ------- | --------------- |
| 素材 ID   | 系统生成            |
| 素材文件    | 图片或视频           |
| 素材类型    | image/video     |
| 文件名     | 原始文件名           |
| 缩略图     | 用于前端预览          |
| 人物标签    | 如钱学森、邓稼先        |
| 场景标签    | 如实验室、会议、火箭发射    |
| 年代标签    | 如1950年代、1960年代  |
| 情绪标签    | 如庄重、紧张、感人       |
| 画面类型    | 老照片、档案、纪录片、新闻画面 |
| 横竖屏     | 横屏/竖屏/方形        |
| 清晰度     | 宽高、分辨率          |
| 版权备注    | 用户手动填写          |
| 来源备注    | 用户手动填写          |
| 是否可用于项目 | 是/否             |
| 创建时间    | 上传时间            |

### 素材自动标签

上传素材后，系统自动生成标签：

```json
{
  "people": ["钱学森"],
  "scene": ["科学家", "会议室", "老照片"],
  "era": ["1950s", "1960s"],
  "emotion": ["庄重", "历史感"],
  "media_type": "photo",
  "visual_style": ["黑白", "纪实"],
  "keywords": ["钱学森", "中国科学家", "历史照片"]
}
```

### 素材人工编辑

用户可以手动修改：

```text
人物标签
场景标签
年代标签
情绪标签
关键词
版权备注
是否可用
```

---

## 6.3 页面三：二创文案确认页

### 页面目标

展示 AI 改写后的文案，允许用户编辑。

### 页面结构

```text
左侧：原始文案
右侧：AI 二创文案
```

### 二创文案输出结构

```json
{
  "title": "钱学森到底有多重要？",
  "hook": "上世纪60年代，中央曾为他设下一项特殊保护。",
  "rewritten_script": "完整二创文案……"
}
```

### 用户操作

```text
重新生成
编辑文案
保存当前版本
下一步：生成分镜
```

### 二创要求

```text
1. 原文第一句话作为固定开头，逐字保留，且不参与重复率与总体重构度计算
2. 提炼核心矛盾和母主题；资料卡负责筛选写作内容，凡进入资料卡的背景、事实、原因、结果、动作、代价和关键画面都不能压缩或省略
3. 同类事实只重点展开一到两个，其余可以合并交代但不能删除
4. 事实编辑先通读原文并提炼为按真实时间排序、语言中性的事实资料卡；写作阶段只依据资料卡独立创作，生成后再用原文恢复固定开头并检查重复率
5. 成稿有效字数不得低于原文的 75%；补足篇幅只能展开资料卡内容，不能填充空话或硬拆画面
6. 口语化，适合短视频旁白；表达要狠、有攻击性、有爽感，但必须自然且忠于事实
7. 固定开头单独保留，不要求它独立构成完整画面，也不得为了排版删改或拆碎；之后默认顺叙，只允许一次必要回溯，不硬性规定主人公姓名必须在第几句出现
8. 除固定开头外，每段对应一个完整画面
9. 不要编造具体年份、人物对话、地点，不要把不确定事实说得过于绝对
10. 成稿生成后必须对 material_cards 逐条做事实覆盖审查；事件、原因、结果、人物动作、代价或关键画面只写了一部分也算未通过，缺失项反馈给下一次重写
11. 成稿低于原文 75% 时直接判定本轮未通过并重试；篇幅、差异指标或事实覆盖未达标时最多生成三次，随后返回质量最好且内容最完整的一版并提示
12. 重试只传递指标、重复片段、结构问题摘要和缺失事实卡，不向写作模型提供上一版全文
13. 不要求为了差异推翻合理的真实时间线或叙事骨架；禁止使用“先说……再说……最后说……”“第一……第二……第三……”等提纲式表达
```

---

## 6.4 页面四：分镜确认页

### 页面目标

将二创文案拆成多个视频镜头。

### 分镜字段

| 字段    | 说明            |
| ----- | ------------- |
| 镜头编号  | 第几个镜头         |
| 镜头文案  | 当前镜头对应的旁白     |
| 预估时长  | 根据字数初步估算      |
| 画面需求  | 当前镜头需要什么画面    |
| 精准搜索词 | 用于查找具体人物/事件素材 |
| 替代搜索词 | 找不到精准素材时使用    |
| 氛围搜索词 | 用于找历史氛围素材     |
| 匹配状态  | 已匹配/待确认/需AI生成 |
| 素材来源  | 本地素材/AI生成     |
| 操作    | 编辑/重新匹配/AI生成  |

### 示例

| 镜头 | 文案          | 画面需求          | 精准搜索词     | 替代搜索词      | 氛围搜索词     |
| -- | ----------- | ------------- | --------- | ---------- | --------- |
| 1  | 钱学森到底有多重要？  | 钱学森或老科学家庄重开场  | 钱学森 老照片   | 中国科学家 老照片  | 历史档案 黑白照片 |
| 2  | 中央曾为他设置特殊保护 | 会议室/档案/国家保护氛围 | 钱学森 保护    | 1960年代 会议室 | 档案 文件 机密  |
| 3  | 特务潜入宿舍楼     | 夜晚宿舍楼/紧张氛围    | 中国科学院 宿舍楼 | 夜晚 建筑      | 悬疑 黑白 旧照片 |

### 分镜生成规则

```text
1. 每个镜头文案建议 15-35 个汉字
2. 每个镜头时长建议 3-6 秒
3. 开头钩子单独成镜头
4. 情绪转折处单独成镜头
5. 结尾升华单独成镜头
6. 每个镜头必须生成素材搜索意图
```

### 用户操作

V1 支持：

```text
编辑镜头文案
编辑画面需求
编辑搜索词
删除镜头
重新生成单个镜头搜索词
重新匹配素材
手动指定素材
```

---

## 6.5 页面五：素材匹配确认页

### 页面目标

展示每个分镜匹配到的真实素材，并允许用户确认或替换。

### 页面结构

每个镜头一个卡片：

```text
镜头 1
旁白：钱学森到底有多重要？
画面需求：钱学森或老科学家庄重开场

候选素材：
1. qian_xuesen_001.jpg，匹配分 92
2. scientist_old_photo_003.jpg，匹配分 78
3. archive_photo_011.jpg，匹配分 65

当前选择：qian_xuesen_001.jpg
操作：使用该素材 / 换一个 / AI 生成 / 手动上传
```

### 匹配结果状态

| 状态                | 说明              |
| ----------------- | --------------- |
| matched           | 已匹配到高分素材        |
| needs_review      | 匹配到中等素材，需要人工确认  |
| no_match          | 没有合适素材，建议 AI 生成 |
| ai_generated      | 已使用 AI 生成图      |
| manually_selected | 用户手动选择素材        |

### 匹配分数规则

V1 可以使用简单规则：

```text
匹配分 = 人物标签匹配 * 40%
       + 场景标签匹配 * 25%
       + 关键词匹配 * 20%
       + 年代/风格匹配 * 10%
       + 素材质量 * 5%
```

### 匹配阈值

```text
80 分以上：自动推荐使用
50-79 分：需要用户确认
50 分以下：建议 AI 生成
```

### 用户操作

```text
确认当前素材
查看其他候选素材
手动选择素材
重新匹配
使用 AI 生成补位图
上传新素材
```

---

## 6.6 页面六：AI 兜底生图页

### 页面目标

对没有合适真实素材的镜头生成 AI 配图。

### 触发条件

```text
1. 该镜头没有匹配到 50 分以上素材
2. 用户主动点击“使用 AI 生成”
3. 用户对匹配素材不满意
```

### 生图 Prompt 生成规则

根据当前镜头信息生成：

```json
{
  "shot_id": 3,
  "voice_text": "几名特务潜入中国科学院宿舍楼。",
  "visual_description_cn": "夜晚宿舍楼外，模糊人影，紧张悬疑氛围，黑白纪实风格",
  "image_prompt": "realistic historical documentary style, night outside an old dormitory building, blurry figures in the distance, tense atmosphere, black and white archival photo style, no text, no watermark, vertical composition"
}
```

### AI 生图要求

```text
1. 默认生成 9:16 竖图
2. 不要出现文字
3. 不要出现水印
4. 历史人物尽量使用背影、侧影、剪影，不生成高度相似正脸
5. 风格要接近真实历史影像
6. 色调克制，避免现代感过强
```

### 默认统一风格

```text
写实历史纪录片风格，老照片质感，电影感光影，色调克制，画面庄重，历史档案感，竖屏构图，不要文字，不要水印。
```

---

## 6.7 页面七：配音与字幕生成页

### 页面目标

生成 AI 配音和字幕文件。

### 配音输入

```json
{
  "script": "最终确认后的完整口播文案",
  "voice_style": "沉稳男声",
  "speed": 1.0,
  "emotion": "serious"
}
```

### 配音输出

```json
{
  "audio_url": "audio/main_voice.mp3",
  "duration_sec": 83.5
}
```

### 字幕生成方式

V1 使用简化方案：

```text
根据分镜文案和音频总时长，按比例生成 SRT 字幕。
```

后续 V2 再使用强制对齐，让字幕更精准。

### 字幕规则

```text
1. 每条字幕不超过 18 个汉字
2. 每条字幕尽量 1-2 行
3. 字幕时间不能重叠
4. 字幕总时长与配音总时长基本一致
5. 镜头文案和字幕内容保持一致
```

### SRT 示例

```srt
1
00:00:00,000 --> 00:00:04,000
钱学森到底有多重要？

2
00:00:04,000 --> 00:00:09,000
上世纪60年代，中央曾为他设下一项特殊保护。

3
00:00:09,000 --> 00:00:15,000
几名特务潜入中国科学院宿舍楼。
```

---

## 6.8 页面八：结果导出页

### 页面目标

展示最终内容并提供导出。

### 展示内容

```text
1. 最终口播文案
2. 分镜表
3. 每个镜头使用的素材
4. AI 生成补位图
5. 配音播放器
6. 字幕预览
7. 导出按钮
```

### 导出按钮

V1 必须支持：

```text
下载素材包 ZIP
下载字幕 SRT
下载分镜表 CSV
下载项目 JSON
```

V1.1 支持：

```text
下载剪映草稿 ZIP
```

---

# 7. 核心模块设计

## 7.1 文案二创模块

### 输入

```json
{
  "raw_script": "用户原始文案",
  "content_type": "国之脊梁",
  "rewrite_level": "medium",
  "script_style": "纪实故事型",
  "target_platform": "视频号"
}
```

### 输出

```json
{
  "title": "钱学森到底有多重要？",
  "hook": "上世纪60年代，中央曾为他设下一项特殊保护。",
  "rewritten_script": "完整二创文案……"
}
```

### 文案二创 Prompt

```text
你是一个短视频口播文案编导，擅长历史人物、国之脊梁、科学家故事类内容。

请根据用户提供的原始文案，改写成适合短视频口播的文案。

要求：
1. 原文第一句话作为固定开头逐字保留，固定开头不参与重复率和总体重构度计算
2. 事实编辑先通读原文并提炼成按时间排序的中性事实资料卡；写作模型只依据资料卡独立成文，生成后再用原文恢复固定开头并检查重复率
3. 围绕核心矛盾和母主题推进；资料卡负责筛选内容，凡进入资料卡的信息都不能压缩或省略
4. 成稿有效字数不得低于原文的 75%；只能通过展开资料卡中的事实和画面补足篇幅
5. 不要编造具体年份、地点、人物对话，不确定的事实不要写得过于绝对
6. 语言口语化、有节奏；表达要狠、有攻击性、有爽感，但必须自然
7. 固定开头单独保留，不要求它独立构成完整画面，也不得为了排版删改或拆碎；之后默认顺叙，只允许一次必要回溯，同时保留重要细节
8. 除固定开头外，每个自然段对应一个完整画面，不按字数硬拆
9. 成稿生成后逐条核验 material_cards；事件、原因、结果、动作、代价及关键画面有缺失就重写。低于原文 75% 同样判定未通过
10. 不要求推翻合理叙事骨架；不得出现“先说……再说……最后说……”或“第一……第二……第三……”等提纲式结构
11. 输出 JSON，不要输出多余解释

用户选择：
内容类型：{{content_type}}
改写强度：{{rewrite_level}}
文案风格：{{script_style}}

原始文案：
{{raw_script}}

请输出：
{
  "title": "",
  "hook": "",
  "rewritten_script": ""
}
```

---

## 7.2 分镜与搜索意图生成模块

### 输入

```json
{
  "rewritten_script": "最终确认后的二创文案",
  "video_ratio": "9:16",
  "avg_shot_duration": 4,
  "content_type": "国之脊梁"
}
```

### 输出

```json
{
  "shots": [
    {
      "shot_index": 1,
      "voice_text": "钱学森到底有多重要？",
      "duration_sec": 4,
      "visual_need": "钱学森或老科学家庄重开场",
      "exact_keywords": ["钱学森 老照片", "钱学森 真实影像"],
      "alternative_keywords": ["中国科学家 老照片", "老科学家 档案照片"],
      "atmosphere_keywords": ["历史档案", "黑白照片", "庄重开场"]
    }
  ]
}
```

### 分镜 Prompt

```text
你是一个短视频分镜导演和历史素材检索专家，擅长把历史人物口播文案拆成适合真实素材匹配的视频分镜。

请将下面文案拆成多个镜头，并为每个镜头生成素材搜索意图。

要求：
1. 每个镜头 15-35 个汉字左右
2. 每个镜头 3-6 秒
3. 开头钩子单独成镜头
4. 情绪转折单独成镜头
5. 结尾升华单独成镜头
6. 每个镜头必须包含画面需求
7. 每个镜头必须生成三类关键词：
   - 精准关键词：用于匹配具体人物或事件素材
   - 替代关键词：找不到精准素材时使用
   - 氛围关键词：用于匹配历史氛围、档案感、场景素材
8. 搜索词要适合匹配本地素材库，不要只复述原句
9. 输出 JSON，不要输出多余解释

文案：
{{rewritten_script}}

输出格式：
{
  "shots": [
    {
      "shot_index": 1,
      "voice_text": "",
      "duration_sec": 4,
      "visual_need": "",
      "exact_keywords": [],
      "alternative_keywords": [],
      "atmosphere_keywords": []
    }
  ]
}
```

---

## 7.3 素材库管理模块

### 素材上传输入

```json
{
  "file": "qian_xuesen_001.jpg",
  "project_id": null,
  "source_note": "用户本地上传",
  "copyright_note": "自用素材，来源待确认"
}
```

### 素材分析输出

```json
{
  "asset_id": "asset_uuid",
  "type": "image",
  "people": ["钱学森"],
  "scene": ["科学家", "会议", "老照片"],
  "era": ["1950s", "1960s"],
  "emotion": ["庄重", "历史感"],
  "visual_style": ["黑白", "纪实"],
  "keywords": ["钱学森", "中国科学家", "老照片", "历史档案"],
  "orientation": "landscape",
  "quality_score": 82
}
```

### 素材自动标签 Prompt

```text
你是一个历史短视频素材标注助手。

请根据这张图片或这段视频的内容，为素材生成标签。

要求：
1. 识别可能出现的人物，但不确定时不要强行判断
2. 描述画面场景
3. 判断画面年代感
4. 判断画面情绪
5. 判断是否适合历史人物/国之脊梁类短视频使用
6. 输出 JSON，不要输出多余解释

输出格式：
{
  "people": [],
  "scene": [],
  "era": [],
  "emotion": [],
  "visual_style": [],
  "keywords": [],
  "orientation": "",
  "quality_score": 0,
  "suitable_for": []
}
```

---

## 7.4 素材匹配模块

### 输入

```json
{
  "shot": {
    "voice_text": "他冲破重重阻挠，终于回到祖国。",
    "visual_need": "钱学森回国或人物归国氛围",
    "exact_keywords": ["钱学森 回国", "钱学森 1955"],
    "alternative_keywords": ["中国科学家 回国", "轮船 港口"],
    "atmosphere_keywords": ["归国", "老照片", "历史档案"]
  },
  "asset_library": []
}
```

### 输出

```json
{
  "shot_id": "shot_uuid",
  "candidates": [
    {
      "asset_id": "asset_001",
      "file_name": "qian_xuesen_return_001.jpg",
      "match_score": 93,
      "match_reason": "人物标签和回国关键词高度匹配",
      "recommendation": "use"
    },
    {
      "asset_id": "asset_002",
      "file_name": "old_scientist_photo_003.jpg",
      "match_score": 71,
      "match_reason": "科学家和老照片氛围匹配，但不是具体钱学森",
      "recommendation": "review"
    }
  ],
  "final_status": "matched"
}
```

### 匹配评分规则

V1 简化评分：

```text
人物标签匹配：40 分
场景标签匹配：25 分
关键词匹配：20 分
年代/氛围匹配：10 分
素材质量：5 分
```

### 匹配状态规则

```text
80-100 分：matched，推荐直接使用
50-79 分：needs_review，需要人工确认
0-49 分：no_match，建议 AI 生图
```

---

## 7.5 AI 生图兜底模块

### 输入

```json
{
  "shot_id": "shot_uuid",
  "voice_text": "几名特务潜入中国科学院宿舍楼。",
  "visual_need": "夜晚宿舍楼，紧张悬疑氛围",
  "style": "历史纪录片风格",
  "ratio": "9:16"
}
```

### 输出

```json
{
  "shot_id": "shot_uuid",
  "image_url": "generated/shot_003.png",
  "image_prompt": "realistic historical documentary style...",
  "status": "success"
}
```

### 生图 Prompt 模板

```text
请为下面短视频镜头生成一条 AI 生图提示词。

要求：
1. 写实历史纪录片风格
2. 老照片或历史档案质感
3. 竖屏构图
4. 不要文字，不要水印
5. 不要生成高度相似的真实历史人物正脸
6. 可以使用背影、侧影、剪影、场景、物件、档案文件来表达
7. 画面要适合国之脊梁/历史人物短视频

镜头文案：
{{voice_text}}

画面需求：
{{visual_need}}

请输出：
{
  "image_prompt": ""
}
```

---

## 7.6 配音模块

### 输入

```json
{
  "script": "最终确认后的完整口播文案",
  "voice_style": "沉稳男声",
  "speed": 1.0,
  "emotion": "serious"
}
```

### 输出

```json
{
  "audio_url": "audio/main_voice.mp3",
  "audio_path": "projects/{project_id}/audio/main_voice.mp3",
  "duration_sec": 83.5
}
```

### V1 配音要求

```text
1. 只做单人旁白
2. 默认 mp3 格式
3. 支持语速设置
4. 返回音频总时长
5. 字幕和镜头时长以配音总时长为基准重新计算
```

---

## 7.7 字幕模块

### 输入

```json
{
  "shots": [],
  "audio_duration_sec": 83.5
}
```

### 输出

```json
{
  "subtitle_url": "subtitles/subtitles.srt",
  "format": "srt"
}
```

### 字幕时间计算

V1 使用：

```text
每个镜头时长 = 当前镜头文案字数 / 全部镜头文案总字数 * 配音总时长
```

限制条件：

```text
每个镜头最短 2.5 秒
每个镜头最长 7 秒
如果总时长不一致，最后统一按比例修正
```

---

## 7.8 时间轴模块

### 输入

```json
{
  "shots": [
    {
      "shot_index": 1,
      "start_time": 0,
      "end_time": 4.2,
      "duration_sec": 4.2,
      "media_type": "image",
      "media_path": "assets/images/shot_001.jpg",
      "voice_text": "钱学森到底有多重要？"
    }
  ],
  "audio_path": "assets/audio/main_voice.mp3",
  "subtitle_path": "assets/subtitles/subtitles.srt"
}
```

### 输出

```json
{
  "timeline": [
    {
      "track": "visual",
      "shot_index": 1,
      "asset_path": "assets/images/shot_001.jpg",
      "start_time": 0,
      "end_time": 4.2
    },
    {
      "track": "audio",
      "asset_path": "assets/audio/main_voice.mp3",
      "start_time": 0
    },
    {
      "track": "subtitle",
      "asset_path": "assets/subtitles/subtitles.srt",
      "start_time": 0
    }
  ]
}
```

---

# 8. 导出规范

## 8.1 素材包 ZIP 结构

```text
project_name_assets.zip
  script/
    raw_script.txt
    rewritten_script.txt
  storyboard/
    storyboard.json
    storyboard.csv
  assets/
    images/
      shot_001.jpg
      shot_002.png
      shot_003.png
    videos/
      shot_004.mp4
    audio/
      main_voice.mp3
    subtitles/
      subtitles.srt
  timeline/
    timeline.json
  metadata/
    project.json
    asset_match_report.json
  readme.txt
```

## 8.2 storyboard.csv 字段

```csv
shot_index,start_time,end_time,duration,voice_text,visual_need,selected_asset,asset_source,match_score,status
1,00:00:00,00:00:04,4,钱学森到底有多重要？,钱学森或老科学家庄重开场,qian_xuesen_001.jpg,local,92,matched
2,00:00:04,00:00:09,5,中央曾为他设置特殊保护,会议室/档案/国家保护氛围,archive_room_003.jpg,local,76,needs_review
3,00:00:09,00:00:15,6,几名特务潜入宿舍楼,夜晚宿舍楼紧张氛围,shot_003_ai.png,ai_generated,0,ai_generated
```

## 8.3 asset_match_report.json

```json
{
  "project_id": "project_uuid",
  "total_shots": 20,
  "local_matched": 14,
  "needs_review": 3,
  "ai_generated": 3,
  "match_rate": 0.7,
  "shots": []
}
```

---

# 9. 数据库设计

## 9.1 projects 表

```sql
create table projects (
  id uuid primary key default gen_random_uuid(),
  name text,
  raw_script text,
  rewritten_script text,
  content_type text,
  rewrite_level text,
  script_style text,
  voice_style text,
  video_ratio text default '9:16',
  status text default 'created',
  created_at timestamp default now(),
  updated_at timestamp default now()
);
```

## 9.2 shots 表

```sql
create table shots (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references projects(id),
  shot_index int,
  voice_text text,
  duration_sec numeric,
  start_time numeric,
  end_time numeric,
  visual_need text,
  exact_keywords jsonb,
  alternative_keywords jsonb,
  atmosphere_keywords jsonb,
  selected_asset_id uuid,
  asset_source text,
  match_score numeric,
  status text,
  created_at timestamp default now(),
  updated_at timestamp default now()
);
```

## 9.3 media_assets 表

```sql
create table media_assets (
  id uuid primary key default gen_random_uuid(),
  file_name text,
  file_type text,
  file_url text,
  thumbnail_url text,
  local_path text,
  people jsonb,
  scene jsonb,
  era jsonb,
  emotion jsonb,
  visual_style jsonb,
  keywords jsonb,
  orientation text,
  width int,
  height int,
  duration_sec numeric,
  quality_score numeric,
  source_note text,
  copyright_note text,
  is_available boolean default true,
  created_at timestamp default now(),
  updated_at timestamp default now()
);
```

## 9.4 project_assets 表

```sql
create table project_assets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references projects(id),
  shot_id uuid references shots(id),
  asset_id uuid references media_assets(id),
  asset_source text,
  match_score numeric,
  match_reason text,
  is_selected boolean default false,
  created_at timestamp default now()
);
```

## 9.5 generated_assets 表

```sql
create table generated_assets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references projects(id),
  shot_id uuid references shots(id),
  type text,
  prompt text,
  file_url text,
  status text,
  created_at timestamp default now()
);
```

## 9.6 tasks 表

```sql
create table tasks (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references projects(id),
  type text,
  status text,
  progress int default 0,
  current_step text,
  error_message text,
  created_at timestamp default now(),
  updated_at timestamp default now()
);
```

---

# 10. API 设计

## 10.1 创建项目

```http
POST /api/projects
```

### Request

```json
{
  "name": "钱学森视频",
  "raw_script": "钱学森到底有多重要……",
  "content_type": "国之脊梁",
  "rewrite_level": "medium",
  "script_style": "纪实故事型",
  "voice_style": "沉稳男声",
  "video_ratio": "9:16"
}
```

### Response

```json
{
  "project_id": "project_uuid",
  "status": "created"
}
```

---

## 10.2 生成二创文案

```http
POST /api/projects/{project_id}/rewrite
```

### Response

```json
{
  "title": "钱学森到底有多重要？",
  "hook": "上世纪60年代，中央曾为他设下一项特殊保护。",
  "rewritten_script": "完整二创文案……"
}
```

---

## 10.3 更新二创文案

```http
PATCH /api/projects/{project_id}/script
```

### Request

```json
{
  "rewritten_script": "用户编辑后的最终文案"
}
```

---

## 10.4 生成分镜和素材搜索词

```http
POST /api/projects/{project_id}/shots
```

### Response

```json
{
  "shots": [
    {
      "shot_index": 1,
      "voice_text": "钱学森到底有多重要？",
      "duration_sec": 4,
      "visual_need": "钱学森或老科学家庄重开场",
      "exact_keywords": ["钱学森 老照片"],
      "alternative_keywords": ["中国科学家 老照片"],
      "atmosphere_keywords": ["历史档案", "黑白照片"]
    }
  ]
}
```

---

## 10.5 上传素材

```http
POST /api/assets/upload
```

### Request

```json
{
  "file": "multipart file",
  "source_note": "用户上传",
  "copyright_note": "自用素材"
}
```

### Response

```json
{
  "asset_id": "asset_uuid",
  "status": "uploaded"
}
```

---

## 10.6 分析素材标签

```http
POST /api/assets/{asset_id}/analyze
```

### Response

```json
{
  "asset_id": "asset_uuid",
  "people": ["钱学森"],
  "scene": ["科学家", "老照片"],
  "era": ["1950s"],
  "emotion": ["庄重"],
  "keywords": ["钱学森", "老照片", "中国科学家"]
}
```

---

## 10.7 匹配项目素材

```http
POST /api/projects/{project_id}/match-assets
```

### Response

```json
{
  "project_id": "project_uuid",
  "results": [
    {
      "shot_id": "shot_uuid",
      "status": "matched",
      "candidates": [
        {
          "asset_id": "asset_uuid",
          "match_score": 92,
          "match_reason": "人物和老照片标签匹配"
        }
      ]
    }
  ]
}
```

---

## 10.8 手动选择镜头素材

```http
PATCH /api/projects/{project_id}/shots/{shot_id}/asset
```

### Request

```json
{
  "asset_id": "asset_uuid",
  "asset_source": "local"
}
```

---

## 10.9 对单个镜头 AI 生图

```http
POST /api/projects/{project_id}/shots/{shot_id}/generate-image
```

### Response

```json
{
  "shot_id": "shot_uuid",
  "image_url": "generated/shot_003.png",
  "status": "success"
}
```

---

## 10.10 生成配音

```http
POST /api/projects/{project_id}/generate-voice
```

### Response

```json
{
  "audio_url": "audio/main_voice.mp3",
  "duration_sec": 83.5
}
```

---

## 10.11 生成字幕

```http
POST /api/projects/{project_id}/generate-subtitles
```

### Response

```json
{
  "subtitle_url": "subtitles/subtitles.srt"
}
```

---

## 10.12 导出素材包

```http
POST /api/projects/{project_id}/export/assets
```

### Response

```json
{
  "download_url": "exports/project_assets.zip"
}
```

---

# 11. 技术架构建议

## 11.1 前端

建议：

```text
Next.js
Tailwind CSS
shadcn/ui
```

页面：

```text
/projects/new
/assets
/projects/[id]/script
/projects/[id]/storyboard
/projects/[id]/match
/projects/[id]/generate
/projects/[id]/result
```

## 11.2 后端

建议：

```text
Python FastAPI
```

原因：

```text
1. 方便处理文件上传
2. 方便做素材标签分析
3. 方便生成字幕和素材包
4. 后续方便接 ffmpeg
5. 后续方便生成剪映草稿文件
```

## 11.3 数据库和存储

建议：

```text
Supabase PostgreSQL：保存项目、分镜、素材标签、任务状态
Supabase Storage / 本地 storage：保存图片、视频、音频、字幕、导出 ZIP
```

## 11.4 异步任务

MVP 初期：

```text
FastAPI BackgroundTasks
```

后续升级：

```text
Redis + Celery
```

---

# 12. 推荐目录结构

```text
real-material-video-draft/
  apps/
    web/
      app/
        projects/
        assets/
      components/
      lib/
  services/
    api/
      main.py
      routers/
        projects.py
        assets.py
        rewrite.py
        shots.py
        matching.py
        generation.py
        export.py
      services/
        llm_service.py
        material_analyze_service.py
        material_match_service.py
        image_generation_service.py
        tts_service.py
        subtitle_service.py
        timeline_service.py
        export_service.py
        draft_service.py
      models/
        project.py
        shot.py
        asset.py
        task.py
      utils/
        timecode.py
        file_utils.py
        zip_utils.py
  storage/
    assets/
    projects/
      project_id/
        images/
        videos/
        audio/
        subtitles/
        exports/
```

---

# 13. 任务状态设计

## 13.1 任务类型

```text
rewrite_script
generate_shots
analyze_asset
match_assets
generate_ai_image
generate_voice
generate_subtitles
export_assets
export_jianying_draft
```

## 13.2 任务状态

```text
pending
running
success
failed
cancelled
```

## 13.3 前端进度展示

```text
正在二创文案
正在生成分镜
正在分析素材标签
正在匹配真实素材
正在为未匹配镜头生成 AI 图片
正在生成配音
正在生成字幕
正在打包素材
生成完成
```

---

# 14. 错误处理

## 14.1 文案二创失败

提示：

```text
文案生成失败，请重试。原始文案已保存，不会丢失。
```

操作：

```text
重试
返回编辑
```

## 14.2 素材上传失败

提示：

```text
素材上传失败，请检查文件格式或文件大小。
```

操作：

```text
重新上传
跳过该素材
```

## 14.3 素材标签分析失败

提示：

```text
素材标签分析失败，你可以手动填写标签。
```

操作：

```text
重新分析
手动编辑标签
```

## 14.4 素材匹配失败

提示：

```text
素材匹配失败，请重试，或手动选择素材。
```

操作：

```text
重新匹配
手动选择素材
AI 生图
```

## 14.5 AI 生图失败

提示：

```text
第 {{shot_index}} 个镜头 AI 生图失败。
```

操作：

```text
重试该镜头
修改提示词
跳过该镜头
```

## 14.6 配音失败

提示：

```text
配音生成失败，请稍后重试。
```

操作：

```text
重试配音
更换配音风格
```

## 14.7 导出失败

提示：

```text
素材包导出失败，请重试。
```

操作：

```text
重新导出
查看错误日志
```

---

# 15. 合规与风险控制

## 15.1 素材版权提示

页面需要提示：

```text
请确保你上传或使用的素材拥有合法使用权。系统不会自动判断素材版权归属。
```

## 15.2 AI 生成内容提示

页面需要提示：

```text
AI 生成的文案、图片和标签可能存在错误，发布前请人工检查。
```

## 15.3 历史事实提示

页面需要提示：

```text
历史人物类内容建议发布前核对事实，避免 AI 改写造成事实偏差。
```

## 15.4 肖像风险控制

默认策略：

```text
1. AI 生图不生成高度相似的真实历史人物正脸
2. 优先生成背影、侧影、场景、档案、实验室、会议等画面
3. 用户上传真实人物素材时，版权和肖像风险由用户自行确认
```

---

# 16. V1 开发优先级

## P0：必须完成

```text
1. 创建项目
2. 输入原始文案
3. AI 二创文案
4. 用户编辑二创文案
5. 自动分镜
6. 每个分镜生成素材搜索词
7. 素材上传
8. 素材自动打标签
9. 本地素材匹配
10. 用户确认匹配结果
11. 未匹配镜头 AI 生图
12. 生成配音
13. 生成 SRT 字幕
14. 导出素材包 ZIP
```

## P1：尽量完成

```text
1. 单个镜头重新匹配
2. 手动指定镜头素材
3. 分镜表 CSV 导出
4. 匹配报告 JSON
5. 批量素材标签编辑
6. 时间轴 JSON 导出
```

## P2：后续版本

```text
1. 剪映草稿导出
2. 接入 Pexels / Pixabay 等通用素材 API
3. 接入 Wikimedia 等开放素材源
4. 视频片段自动截取
5. 自动裁剪 9:16
6. 图片轻微推拉动画
7. BGM 和音效
8. 字幕样式模板
9. 素材库智能去重
10. 多项目素材复用
```

---

# 17. 开发顺序建议

## 第一阶段：打通文案和分镜

```text
创建项目
输入原始文案
AI 二创文案
用户确认
AI 生成分镜和搜索词
```

验收标准：

```text
输入一段 800 字文案，可以生成 15-30 个分镜，每个分镜有画面需求和关键词。
```

## 第二阶段：打通本地素材库

```text
上传素材
生成缩略图
自动打标签
标签可编辑
素材列表可搜索
```

验收标准：

```text
上传 100 个图片/视频素材后，可以按人物、场景、关键词搜索。
```

## 第三阶段：打通素材匹配

```text
根据分镜搜索词匹配素材库
生成候选素材
计算匹配分数
用户确认素材
```

验收标准：

```text
钱学森相关分镜能够优先匹配钱学森素材；
没有具体人物素材时，能匹配老照片、实验室、档案等替代素材。
```

## 第四阶段：打通 AI 生图兜底

```text
无匹配素材的镜头
自动生成 AI 生图提示词
生成补位图片
加入时间轴
```

验收标准：

```text
未匹配镜头能生成风格统一的历史纪实风 AI 图片。
```

## 第五阶段：配音、字幕和导出

```text
生成配音
根据配音时长计算字幕
导出 SRT
导出素材包 ZIP
```

验收标准：

```text
素材包包含图片/视频、配音、字幕、分镜表、项目 JSON，可以手动导入剪映继续编辑。
```

---

# 18. MVP 验收标准

## 18.1 输入验收

输入一段 600-1000 字的历史人物文案，系统应能生成：

```text
1. 一篇二创口播稿
2. 15-30 个分镜
3. 每个分镜的画面需求
4. 每个分镜的精准关键词、替代关键词、氛围关键词
```

## 18.2 素材库验收

上传 50-100 个本地素材后，系统应能：

```text
1. 自动生成标签
2. 支持人工编辑标签
3. 支持按关键词搜索素材
4. 支持素材预览
5. 支持查看素材版权备注
```

## 18.3 匹配验收

对于一个 20 镜头项目：

```text
1. 有明确人物关键词的镜头，优先匹配人物素材
2. 没有人物素材的镜头，匹配场景或氛围素材
3. 匹配分数低于 50 的镜头，自动标记为建议 AI 生成
4. 用户可以手动替换素材
```

## 18.4 生成验收

系统应能生成：

```text
1. AI 补位图片
2. AI 配音 mp3
3. SRT 字幕
4. 时间轴 JSON
5. 素材包 ZIP
```

## 18.5 导出验收

素材包 ZIP 中至少包含：

```text
1. raw_script.txt
2. rewritten_script.txt
3. storyboard.json
4. storyboard.csv
5. 所有选中的真实素材
6. 所有 AI 生成补位图
7. main_voice.mp3
8. subtitles.srt
9. timeline.json
10. asset_match_report.json
```

---

# 19. 给 Codex / Hermes 的开发任务说明

```text
请开发一个“真实素材优先的 AI 短视频草稿生成器”。

核心目标：
用户输入历史人物/国之脊梁类文案后，系统自动二创、分镜，并优先从用户本地素材库中匹配真实素材；匹配不到时再用 AI 生图兜底；最后生成配音、字幕和素材包。

技术栈建议：
- 前端：Next.js + Tailwind CSS + shadcn/ui
- 后端：FastAPI
- 数据库：Supabase PostgreSQL
- 存储：Supabase Storage 或本地 storage
- 异步任务：MVP 先用 FastAPI BackgroundTasks
- 导出：zipfile
- 字幕：生成 SRT
- 后续剪映草稿：先预留 draft_service.py

P0 功能：
1. 创建项目
2. 输入原始文案
3. AI 二创文案
4. 编辑二创文案
5. 生成分镜和素材搜索词
6. 上传本地图片/视频素材
7. 自动分析素材标签
8. 根据分镜匹配素材库
9. 用户确认或替换匹配素材
10. 未匹配镜头使用 AI 生图
11. 生成配音
12. 生成字幕
13. 导出素材包 ZIP

注意：
1. V1 不做全网爬取
2. V1 不抓抖音/B站/纪录片视频
3. V1 不自动判断版权，只允许用户填写版权备注
4. V1 的核心是“本地素材库匹配 + AI 生图兜底”
5. 剪映草稿导出先作为 V1.1，不要阻塞 P0 功能上线
```

---

# 20. 项目判断

这个 V1 比“纯 AI 生图成片”更适合历史人物内容。

原因：

```text
1. 真实素材更有可信度
2. 更适合国之脊梁、科学家、历史人物题材
3. 用户自己有素材库时，效率提升明显
4. AI 生图只做兜底，画面不会全是假图
5. 不依赖爬虫，稳定性更高
6. 后续可以逐步扩展图库 API、剪映草稿、视频素材截取
```

第一版不要追求全自动成片。

第一版真正要解决的是：

```text
我有一段文案，也有一堆素材，但我不知道每句话该配哪张图/哪段视频。
```

所以 V1 的核心价值应该是：

> **自动把文案拆成分镜，并帮我从素材库里找到最合适的真实画面。**

这才是这个工具最值得先做的地方。
