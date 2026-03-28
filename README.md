# iReDev Framework

<p align="center">
  <strong>AI-Driven Multi-Agent Requirements Development Platform</strong>
</p>

<p align="center">
  <em>Automate the entire requirements engineering process — from customer interviews to SRS delivery</em>
</p>

<p align="center">
  <a href="README_zh.md">中文文档</a>
</p>

---

## 📖 Overview

**iReDev** (intelligent Requirements Development) is a multi-agent collaboration platform powered by Large Language Models (LLMs) that simulates a real-world requirements engineering team workflow. The system orchestrates 7 specialized agents through an event-driven 9-step pipeline, automating the full process from customer interviews and requirements analysis to final SRS document delivery.

### Key Features

- **Multi-Agent Collaboration**: 7 specialized role-based agents working together autonomously
- **Event-Driven Pipeline**: Publish-subscribe architecture built on a Git artifact pool — each step's output automatically triggers the next
- **Human-in-the-Loop**: Supports human review and intervention at critical checkpoints to ensure document quality
- **Multi-LLM Support**: Compatible with OpenAI / Claude / Gemini / HuggingFace (local models)
- **Bilingual (EN/ZH)**: All templates and outputs support both English and Chinese
- **Dual Interaction Modes**: Web UI (real-time WebSocket) and CLI
- **Quality Feedback Loop**: Built-in Reviewer with multi-dimensional automated review + iterative revision

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (Web UI)                     │
│            HTML / CSS / JS + WebSocket Real-time Comm        │
└─────────────────────┬───────────────────────────────────────┘
                      │ WebSocket / REST API
┌─────────────────────▼───────────────────────────────────────┐
│                    Backend (FastAPI Server)                   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                iReqDevTeam (Orchestrator)              │   │
│  │                                                       │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐   │   │
│  │  │Interviewer│ │ Analyst │ │Archivist│ │ Reviewer │   │   │
│  │  └────┬────┘ └─────────┘ └─────────┘ └──────────┘   │   │
│  │       │                                               │   │
│  │  ┌────▼────┐ ┌─────────┐ ┌──────────────────────┐   │   │
│  │  │Customer │ │ EndUser │ │HumanREngineer (opt.) │   │   │
│  │  └─────────┘ └─────────┘ └──────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                  │
│  ┌────────────────────────▼─────────────────────────────┐   │
│  │             GitArtifactPool (Artifact Pool)            │   │
│  │       Git Repo · File Watching · Event Pub/Sub         │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 🤖 Agent Roles

| Agent | Role | Responsibility |
|:------|:-----|:---------------|
| **Interviewer** | Requirements Interviewer | Core hub. Conducts customer interviews, generates BRD, identifies user roles, conducts end-user interviews, writes UserRD |
| **Customer** | Business Customer | Human-in-the-Loop role. Receives questions → LLM generates candidate answers for human selection/editing |
| **EndUser** | End User | LLM-simulated user roles. Answers interviews based on role descriptions from UserList + project context |
| **Analyst** | Requirements Analyst | Requirements modeling (use case diagrams) + analysis (extracts and classifies system requirements, writes SyRS) |
| **Archivist** | Document Archivist | Integrates upstream documents to write SRS; performs targeted revisions based on review reports |
| **Reviewer** | Quality Reviewer | Multi-dimensional quality review of SRS (completeness, consistency, verifiability, etc.); outputs structured review reports |
| **HumanREngineer** | RE Engineer (optional) | Human-in-the-loop review of key artifacts; feedback is executed by the corresponding agent |

---

## 🔄 Pipeline (9 Steps)

```
 ① Project Description ──→ ② Customer Interview ──→ ③ Generate BRD ──→ ④ Identify User Roles
                                                                              │
      ┌───────────────────────────────────────────────────────────────────────┘
      ▼
 ⑤ End-User Interviews ──→ ⑥ Write UserRD ──→ ⑦ Requirements Modeling (Use Cases)
                                                        │
      ┌─────────────────────────────────────────────────┘
      ▼
 ⑧ Requirements Analysis (SyRS) ──→ ⑨ Write SRS + Review-Revision Loop
                                            │
                                      ┌─────▼─────┐
                                      │  Reviewer  │──→ APPROVED? ──→ Done ✓
                                      │  Review    │        │
                                      └───────────┘    No ──▼
                                            ▲         Archivist
                                            │         Revises SRS
                                            └──────────┘
```

| Step | Trigger Artifact | Output Artifact | Executing Agent |
|:-----|:-----------------|:----------------|:----------------|
| ① Collect Project Description | — | `customer_project_description.md` | User Input |
| ② Customer Interview | `customer_project_description.md` | `customer_dialogue.md` | Interviewer + Customer |
| ③ Generate BRD | `customer_dialogue.md` | `BRD.md` | Interviewer |
| ④ Identify User Roles | `BRD.md` | `UserList.md` + `context_diagram.puml` | Interviewer |
| ⑤ End-User Interviews | `UserList.md` | `enduser_dialogue.md` | Interviewer + EndUser×N |
| ⑥ Write UserRD | `enduser_dialogue.md` | `UserRD.md` | Interviewer |
| ⑦ Requirements Modeling | `UserRD.md` | `use_case_diagram.puml` + `.png` | Analyst |
| ⑧ Requirements Analysis | `use_case_diagram.png` | `SyRS.md` | Analyst |
| ⑨ SRS + Review | `SyRS.md` | `SRS.md` + `issue_*.md` | Archivist + Reviewer |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- Git (the artifact pool relies on Git for version management)

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd iReDev

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install anthropic tiktoken google-generativeai openai pyyaml fastapi uvicorn
```

### Configure LLM

Edit `backend/config/config.yaml` to select your LLM provider and enter your API key:

```yaml
llm:
  # OpenAI / Compatible API
  type: "openai"
  api_key: "your-api-key"
  base_url: "https://api.openai.com/v1"  # Can be replaced with a compatible endpoint
  model: "gpt-4o-mini"
  temperature: 0.0
  max_output_tokens: 4096

  # Or use Claude
  # type: "claude"
  # api_key: "your-anthropic-api-key"
  # model: "claude-3-5-haiku-latest"

  # Or use Gemini
  # type: "gemini"
  # api_key: "your-gemini-api-key"
  # model: "gemini-1.5-pro"
```

### Running

#### Option 1: Web UI (Recommended)

```bash
python backend/server.py
```

Open your browser and navigate to `http://localhost:8000`. Register an account to get started.

#### Option 2: CLI Mode

```bash
# Quick demo (Chinese output, no human intervention)
python run_demo_cli.py

# Full parameters
python -m backend.run_iReqDev \
  --project_name "my_project" \
  --workspace "output" \
  --language en \
  --human_in_loop  # Enable human review
```

---

## 📂 Project Structure

```
iReDev/
├── backend/
│   ├── iReqDev.py              # Core orchestrator (pipeline control)
│   ├── server.py               # FastAPI web server
│   ├── run_iReqDev.py          # CLI entry point
│   ├── agent/                  # Agent implementations
│   │   ├── base.py             # BaseAgent base class
│   │   ├── interviewer.py      # Interview agent (core hub)
│   │   ├── analyst.py          # Requirements analysis agent
│   │   ├── archivist.py        # Document archival agent
│   │   ├── reviewer.py         # Quality review agent
│   │   ├── enduser.py          # LLM-simulated end user
│   │   ├── human_customer.py   # Human-in-the-loop customer
│   │   ├── human_REngineer.py  # Human-in-the-loop RE engineer
│   │   └── human.py            # Human interaction base class
│   ├── config/
│   │   └── config.yaml         # LLM and rate limit configuration
│   ├── knowledge/              # Document templates (EN/ZH bilingual)
│   │   ├── BRD_template[_zh].md
│   │   ├── SRS_template[_zh].md
│   │   ├── SyRS_template[_zh].md
│   │   ├── UserRD_template[_zh].md
│   │   └── UserList_template[_zh].md
│   ├── llm/                    # LLM abstraction layer
│   │   ├── base.py             # BaseLLM interface
│   │   ├── factory.py          # LLM factory method
│   │   ├── openai_llm.py       # OpenAI implementation
│   │   ├── claude_llm.py       # Claude implementation
│   │   ├── gemini_llm.py       # Gemini implementation
│   │   ├── huggingface_llm.py  # HuggingFace implementation
│   │   └── rate_limiter.py     # Rate limiter
│   ├── pool/
│   │   └── git_artifact_pool.py # Git artifact pool (event-driven core)
│   ├── prompt/                 # System prompts for each agent
│   └── utils/
│       └── artifact_saver.py   # Artifact writer utility
├── frontend/                   # Web frontend
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── output/                     # Project output artifacts
├── data/                       # User data
├── run_demo_cli.py             # CLI quick demo script
└── requirements.txt            # Python dependencies
```

---

## 📄 Output Artifacts

After each project run, the following artifacts are generated under `output/<project_name>/`:

| Category | File | Description |
|:---------|:-----|:------------|
| 📌 Project Description | `customer_project_description.md` | Original project description from the user |
| 💬 Customer Interview | `customer_dialogue.md` | Full dialogue between Interviewer and Customer |
| 📑 BRD | `BRD.md` | Business Requirements Document |
| 👥 User List | `UserList.md` | End-user role list |
| 📐 Context Diagram | `context_diagram.puml` | PlantUML system context diagram |
| 💬 End-User Interviews | `enduser_dialogue.md` | Simulated interview records for each end-user role |
| 📝 UserRD | `UserRD.md` | User Requirements Document |
| 📐 Use Case Diagram | `use_case_diagram.puml` / `.png` | PlantUML use case diagram + PNG rendering |
| 📊 SyRS | `SyRS.md` | System Requirements Specification |
| 📜 SRS | `SRS.md` | Software Requirements Specification (final deliverable) |
| 📋 Review Reports | `issue_1.md`, `issue_2.md`, ... | Structured quality report for each review round |

---

## ⚙️ Configuration

### LLM Providers

Configure in `backend/config/config.yaml`:

| Provider | `type` Value | Notes |
|:---------|:-------------|:------|
| OpenAI | `openai` | Supports custom `base_url` for third-party compatible APIs |
| Claude | `claude` | Anthropic Claude model family |
| Gemini | `gemini` | Google Gemini model family |
| HuggingFace | `huggingface` | Locally deployed models |

### Rate Limiting

Each provider can have independent rate and token consumption limits:

```yaml
rate_limits:
  openai:
    requests_per_minute: 500
    input_tokens_per_minute: 200000
    output_tokens_per_minute: 100000
```

### Runtime Parameters

| Parameter | Description | Default |
|:----------|:------------|:--------|
| `project_name` | Project name | — |
| `language` | Output language (`zh` / `en`) | `en` |
| `human_in_loop` | Enable human review | `False` |
| `max_review_rounds` | Maximum review-revision rounds | `3` |

---

## 🌐 Web UI Features

- **User System**: Login / Registration + Token-based authentication
- **LLM Settings**: Customize API Key / Base URL / Model from the UI
- **Real-Time Interaction**: Bidirectional WebSocket communication with instant interview Q&A display
- **Candidate Answers**: AI-generated candidate answers during customer interviews — select or customize
- **Artifact Panel**: Real-time artifact display on the right panel with full-text viewing (Markdown rendering)
- **Progress Tracking**: 9-step progress bar with stage indicators
- **Multi-Session Management**: Maintain multiple project sessions simultaneously

---

## 🔧 制品池机制

iReDev 使用基于 **Git 的制品池**（`GitArtifactPool`）作为事件驱动的核心：

1. 项目输出目录被初始化为 Git 仓库
2. 后台线程定时轮询 `git status`，检测文件变更
3. 变更事件按 **文件名模式** 和 **变更类型** 过滤后分发给订阅者
4. 每个流水线步骤通过订阅特定制品的 `created` 事件来触发
5. 所有变更自动提交，保留完整版本历史

这种设计使得流水线各步骤完全解耦，新增步骤只需注册新的订阅回调。

---

## 📝 License

MIT
