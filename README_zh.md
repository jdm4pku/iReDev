# iReDev Framework

<p align="center">
  <strong>AI-Driven Multi-Agent Requirements Development Platform</strong>
</p>

<p align="center">
  <em>从客户访谈到 SRS 交付，自动化完成需求工程全流程</em>
</p>

---

## 📖 概述

**iReDev**（intelligent Requirements Development）是一个基于大语言模型（LLM）的多智能体协作平台，模拟真实需求工程团队的完整工作流程。系统由 7 个专业 Agent 协同工作，通过事件驱动的 9 步流水线，自动完成从客户访谈、需求分析到最终 SRS 文档交付的全过程。

### 核心特性

- **多Agent协作**：7 个专业角色 Agent，各司其职，自动协作
- **事件驱动流水线**：基于 Git 制品池的发布-订阅架构，每一步产出自动触发下一步
- **Human-in-the-Loop**：关键节点支持人工审查与干预，保障文档质量
- **多 LLM 支持**：兼容 OpenAI / Claude / Gemini / HuggingFace（本地模型）
- **中英双语**：所有模板和输出均支持中文、英文
- **双模式交互**：Web UI（实时 WebSocket）和 CLI 两种运行方式
- **质量闭环**：内置 Reviewer 多维度自动审查 + 迭代修订机制

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (Web UI)                     │
│              HTML / CSS / JS + WebSocket 实时通信              │
└─────────────────────┬───────────────────────────────────────┘
                      │ WebSocket / REST API
┌─────────────────────▼───────────────────────────────────────┐
│                    Backend (FastAPI Server)                   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  iReqDevTeam (编排器)                   │   │
│  │                                                       │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐   │   │
│  │  │Interviewer│ │ Analyst │ │Archivist│ │ Reviewer │   │   │
│  │  └────┬────┘ └─────────┘ └─────────┘ └──────────┘   │   │
│  │       │                                               │   │
│  │  ┌────▼────┐ ┌─────────┐ ┌──────────────────────┐   │   │
│  │  │Customer │ │ EndUser │ │ HumanREngineer(可选) │   │   │
│  │  └─────────┘ └─────────┘ └──────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                  │
│  ┌────────────────────────▼─────────────────────────────┐   │
│  │             GitArtifactPool (制品池)                    │   │
│  │        Git 仓库 · 文件监测 · 事件订阅/分发              │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 🤖 Agent 角色

| Agent | 角色 | 职责 |
|:------|:-----|:-----|
| **Interviewer** | 需求访谈员 | 核心枢纽。负责客户访谈、生成 BRD、识别用户角色、终端用户访谈、撰写 UserRD |
| **Customer** | 业务客户 | Human-in-the-Loop 角色。接收问题 → LLM 生成候选回答供真人选择/修改 |
| **EndUser** | 终端用户 | LLM 模拟的用户角色。基于 UserList 中的角色描述 + 项目上下文回答访谈 |
| **Analyst** | 需求分析师 | 需求建模（生成用例图）+ 需求分析（抽取分类系统需求，撰写 SyRS） |
| **Archivist** | 文档归档员 | 整合上游文档撰写 SRS，根据审查报告执行定点修订 |
| **Reviewer** | 质量审查员 | 对 SRS 执行多维度质量审查（完整性、一致性、可验证性等），输出结构化审查报告 |
| **HumanREngineer** | 需求工程师（可选） | 人在回路审查关键制品，反馈修改意见由对应 Agent 执行修订 |

---

## 🔄 流水线（9 步）

```
 ① 项目描述 ──→ ② 客户访谈 ──→ ③ 生成 BRD ──→ ④ 识别用户角色
                                                      │
      ┌───────────────────────────────────────────────┘
      ▼
 ⑤ 终端用户访谈 ──→ ⑥ 撰写 UserRD ──→ ⑦ 需求建模(用例图)
                                              │
      ┌───────────────────────────────────────┘
      ▼
 ⑧ 需求分析(SyRS) ──→ ⑨ 撰写 SRS + 审查修订循环
                              │
                        ┌─────▼─────┐
                        │  Reviewer  │──→ APPROVED? ──→ 完成 ✓
                        │   审查     │        │
                        └───────────┘    No ──▼
                              ▲         Archivist
                              │          修订 SRS
                              └──────────┘
```

| 步骤 | 触发制品 | 产出制品 | 执行 Agent |
|:-----|:---------|:---------|:-----------|
| ① 收集项目描述 | — | `customer_project_description.md` | 用户输入 |
| ② 客户访谈 | `customer_project_description.md` | `customer_dialogue.md` | Interviewer + Customer |
| ③ 生成 BRD | `customer_dialogue.md` | `BRD.md` | Interviewer |
| ④ 识别用户角色 | `BRD.md` | `UserList.md` + `context_diagram.puml` | Interviewer |
| ⑤ 终端用户访谈 | `UserList.md` | `enduser_dialogue.md` | Interviewer + EndUser×N |
| ⑥ 撰写 UserRD | `enduser_dialogue.md` | `UserRD.md` | Interviewer |
| ⑦ 需求建模 | `UserRD.md` | `use_case_diagram.puml` + `.png` | Analyst |
| ⑧ 需求分析 | `use_case_diagram.png` | `SyRS.md` | Analyst |
| ⑨ SRS + 审查 | `SyRS.md` | `SRS.md` + `issue_*.md` | Archivist + Reviewer |

---

## 🚀 快速开始

### 环境要求

- Python 3.9+
- Git（制品池依赖 Git 进行版本管理）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd iReDev

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install anthropic tiktoken google-generativeai openai pyyaml fastapi uvicorn
```

### 配置 LLM

编辑 `backend/config/config.yaml`，选择你的 LLM 提供商并填入 API Key：

```yaml
llm:
  # OpenAI / 兼容 API
  type: "openai"
  api_key: "your-api-key"
  base_url: "https://api.openai.com/v1"  # 可替换为兼容端点
  model: "gpt-4o-mini"
  temperature: 0.0
  max_output_tokens: 4096

  # 或使用 Claude
  # type: "claude"
  # api_key: "your-anthropic-api-key"
  # model: "claude-3-5-haiku-latest"

  # 或使用 Gemini
  # type: "gemini"
  # api_key: "your-gemini-api-key"
  # model: "gemini-1.5-pro"
```

### 运行方式

#### 方式一：Web UI（推荐）

```bash
python backend/server.py
```

打开浏览器访问 `http://localhost:8000`，注册账号后即可开始项目。

#### 方式二：CLI 模式

```bash
# 快速演示（中文，无人工干预）
python run_demo_cli.py

# 完整参数
python -m backend.run_iReqDev \
  --project_name "my_project" \
  --workspace "output" \
  --language zh \
  --human_in_loop  # 启用人工审查
```

---

## 📂 项目结构

```
iReDev/
├── backend/
│   ├── iReqDev.py              # 核心编排器（流水线控制）
│   ├── server.py               # FastAPI Web 服务器
│   ├── run_iReqDev.py          # CLI 入口
│   ├── agent/                  # Agent 实现
│   │   ├── base.py             # BaseAgent 基类
│   │   ├── interviewer.py      # 访谈代理（核心枢纽）
│   │   ├── analyst.py          # 需求分析代理
│   │   ├── archivist.py        # 文档归档代理
│   │   ├── reviewer.py         # 质量审查代理
│   │   ├── enduser.py          # LLM 模拟终端用户
│   │   ├── human_customer.py   # 人在回路客户
│   │   ├── human_REngineer.py  # 人在回路需求工程师
│   │   └── human.py            # 人类交互基类
│   ├── config/
│   │   └── config.yaml         # LLM 及速率限制配置
│   ├── knowledge/              # 文档模板（中英双语）
│   │   ├── BRD_template[_zh].md
│   │   ├── SRS_template[_zh].md
│   │   ├── SyRS_template[_zh].md
│   │   ├── UserRD_template[_zh].md
│   │   └── UserList_template[_zh].md
│   ├── llm/                    # LLM 抽象层
│   │   ├── base.py             # BaseLLM 接口
│   │   ├── factory.py          # LLM 工厂方法
│   │   ├── openai_llm.py       # OpenAI 实现
│   │   ├── claude_llm.py       # Claude 实现
│   │   ├── gemini_llm.py       # Gemini 实现
│   │   ├── huggingface_llm.py  # HuggingFace 实现
│   │   └── rate_limiter.py     # 速率限制器
│   ├── pool/
│   │   └── git_artifact_pool.py # Git 制品池（事件驱动核心）
│   ├── prompt/                 # 各 Agent 的 System Prompt
│   └── utils/
│       └── artifact_saver.py   # 制品写入工具
├── frontend/                   # Web 前端
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── output/                     # 项目输出制品
├── data/                       # 用户数据
├── run_demo_cli.py             # CLI 快速演示脚本
└── requirements.txt            # Python 依赖
```

---

## 📄 产出制品

每个项目运行后在 `output/<项目名>/` 下生成以下制品：

| 类型 | 文件 | 说明 |
|:-----|:-----|:-----|
| 📌 项目描述 | `customer_project_description.md` | 用户原始项目描述 |
| 💬 客户访谈 | `customer_dialogue.md` | Interviewer 与客户的完整对话记录 |
| 📑 BRD | `BRD.md` | 业务需求文档 |
| 👥 用户列表 | `UserList.md` | 终端用户角色列表 |
| 📐 上下文图 | `context_diagram.puml` | PlantUML 系统上下文图 |
| 💬 用户访谈 | `enduser_dialogue.md` | 各终端用户角色的模拟访谈记录 |
| 📝 UserRD | `UserRD.md` | 用户需求文档 |
| 📐 用例图 | `use_case_diagram.puml` / `.png` | PlantUML 用例图 + PNG 渲染 |
| 📊 SyRS | `SyRS.md` | 系统需求规格说明 |
| 📜 SRS | `SRS.md` | 软件需求规格说明（最终交付物） |
| 📋 审查报告 | `issue_1.md`, `issue_2.md`, ... | 每轮审查的结构化质量报告 |

---

## ⚙️ 配置说明

### LLM 提供商

在 `backend/config/config.yaml` 中配置：

| 提供商 | type 值 | 特点 |
|:-------|:--------|:-----|
| OpenAI | `openai` | 支持 `base_url` 自定义，兼容第三方 API |
| Claude | `claude` | Anthropic Claude 系列模型 |
| Gemini | `gemini` | Google Gemini 系列模型 |
| HuggingFace | `huggingface` | 本地部署模型 |

### 速率限制

每个提供商可独立配置请求频率和 Token 消耗限制：

```yaml
rate_limits:
  openai:
    requests_per_minute: 500
    input_tokens_per_minute: 200000
    output_tokens_per_minute: 100000
```

### 运行参数

| 参数 | 说明 | 默认值 |
|:-----|:-----|:------|
| `project_name` | 项目名称 | — |
| `language` | 输出语言（`zh` / `en`） | `en` |
| `human_in_loop` | 是否启用人工审查 | `False` |
| `max_review_rounds` | 最大审查-修订轮次 | `3` |

---

## 🌐 Web UI 功能

- **用户系统**：登录 / 注册 + Token 认证
- **LLM 设置**：可在界面上自定义 API Key / Base URL / Model
- **实时交互**：WebSocket 双向通信，访谈问答即时呈现
- **候选回答**：客户访谈阶段展示 AI 候选答案，支持一键选择或自定义输入
- **制品池面板**：右侧实时展示所有产出制品，点击查看全文（支持 Markdown 渲染）
- **流程进度**：9 步进度条 + 阶段提示信息
- **多会话管理**：支持同时维护多个项目会话
