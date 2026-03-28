/**
 * iReDev Frontend — 主应用逻辑
 * 
 * 管理：用户认证、WebSocket 连接、会话管理、消息渲染、制品展示、设置
 */

// ═══════════════════════════════════════════════════════════
// 配置
// ═══════════════════════════════════════════════════════════

const API_BASE = window.location.origin;
const WS_BASE  = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;

// Agent 元数据（图标、颜色、显示名）
const AGENT_META = {
  system:      { emoji: '⚙️', color: '#6b7280', label: 'System' },
  interviewer: { emoji: '🎙', color: '#3b82f6', label: 'Interviewer' },
  customer:    { emoji: '🏢', color: '#10b981', label: 'Customer' },
  enduser:     { emoji: '👤', color: '#06b6d4', label: 'End User' },
  analyst:     { emoji: '📊', color: '#8b5cf6', label: 'Analyst' },
  archivist:   { emoji: '📜', color: '#f59e0b', label: 'Archivist' },
  reviewer:    { emoji: '🔍', color: '#ef4444', label: 'Reviewer' },
  human_re:    { emoji: '👷', color: '#ec4899', label: 'RE Engineer' },
  user:        { emoji: '💬', color: '#6366f1', label: 'You' },
};

// 流水线制品顺序 ── 用于进度计算
const PIPELINE_ORDER = [
  'customer_project_description.md',
  'customer_dialogue.md',
  'BRD.md',
  'UserList.md',
  'context_diagram.puml',
  'enduser_dialogue.md',
  'UserRD.md',
  'use_case_diagram.puml',
  'SyRS.md',
  'SRS.md',
];

// ═══════════════════════════════════════════════════════════
// 应用状态
// ═══════════════════════════════════════════════════════════

const state = {
  // 认证
  token: localStorage.getItem('iredev_token') || null,
  username: localStorage.getItem('iredev_username') || null,
  // 会话
  sessions: [],
  activeSessionId: null,
  ws: null,
  artifacts: [],
  messages: [],
  waitingInput: false,
  candidates: [],
};

// ═══════════════════════════════════════════════════════════
// DOM 引用
// ═══════════════════════════════════════════════════════════

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
  // 认证弹窗
  authModal:          $('#authModal'),
  appLayout:         $('#app'),
  loginForm:         $('#loginForm'),
  registerForm:      $('#registerForm'),
  loginUsername:      $('#loginUsername'),
  loginPassword:      $('#loginPassword'),
  loginError:        $('#loginError'),
  regUsername:        $('#regUsername'),
  regPassword:        $('#regPassword'),
  regPasswordConfirm:$('#regPasswordConfirm'),
  registerError:     $('#registerError'),
  sidebarUsername:    $('#sidebarUsername'),
  userAvatar:        $('#userAvatar'),
  // 欢迎页元素
  welcomeTopbar:     $('#welcomeTopbar'),
  welcomeAuthActions:$('#welcomeAuthActions'),
  // 主界面
  sidebar:           $('#sidebar'),
  sessionList:       $('#sessionList'),
  welcomeScreen:     $('#welcomeScreen'),
  chatContainer:     $('#chatContainer'),
  chatProjectName:   $('#chatProjectName'),
  statusBadge:       $('#statusBadge'),
  messagesContainer: $('#messagesContainer'),
  messagesList:      $('#messagesList'),
  inputArea:         $('#inputArea'),
  messageInput:      $('#messageInput'),
  sendBtn:           $('#sendBtn'),
  candidatesBar:     $('#candidatesBar'),
  candidatesList:    $('#candidatesList'),
  artifactPanel:     $('#artifactPanel'),
  artifactList:      $('#artifactList'),
  docArtifacts:      $('#docArtifacts'),
  modelArtifacts:    $('#modelArtifacts'),
  reviewArtifacts:   $('#reviewArtifacts'),
  progressFill:      $('#progressFill'),
  progressPercent:   $('#progressPercent'),
  artifactCountBadge:$('#artifactCountBadge'),
  // 模态框
  newProjectModal:   $('#newProjectModal'),
  projectNameInput:  $('#projectNameInput'),
  settingsModal:     $('#settingsModal'),
  settingsApiKey:    $('#settingsApiKey'),
  settingsBaseUrl:   $('#settingsBaseUrl'),
  settingsModel:     $('#settingsModel'),
  // 查看器
  artifactViewerOverlay: $('#artifactViewerOverlay'),
  artifactViewerTitle:   $('#artifactViewerTitle'),
  artifactViewerContent: $('#artifactViewerContent'),
};

// ═══════════════════════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  initMarkdown();
  bindAuthEvents();
  checkAuthState();
});

function initMarkdown() {
  marked.setOptions({
    highlight: (code, lang) => {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
    breaks: true,
    gfm: true,
  });
}

// ═══════════════════════════════════════════════════════════
// 认证
// ═══════════════════════════════════════════════════════════

function showAuthModal(tab = 'login') {
  dom.authModal.style.display = 'flex';
  switchAuthTab(tab);
  if (tab === 'login') {
    setTimeout(() => dom.loginUsername.focus(), 100);
  } else {
    setTimeout(() => dom.regUsername.focus(), 100);
  }
}

function hideAuthModal() {
  dom.authModal.style.display = 'none';
  dom.loginError.textContent = '';
  dom.registerError.textContent = '';
}

function switchAuthTab(tab) {
  const tabLogin = $('#tabLogin');
  const tabRegister = $('#tabRegister');
  if (tab === 'login') {
    tabLogin.classList.add('active');
    tabRegister.classList.remove('active');
    dom.loginForm.classList.add('active');
    dom.registerForm.classList.remove('active');
    dom.registerError.textContent = '';
  } else {
    tabLogin.classList.remove('active');
    tabRegister.classList.add('active');
    dom.loginForm.classList.remove('active');
    dom.registerForm.classList.add('active');
    dom.loginError.textContent = '';
  }
}

function bindAuthEvents() {
  // Tab 切换
  $('#tabLogin').addEventListener('click', () => switchAuthTab('login'));
  $('#tabRegister').addEventListener('click', () => switchAuthTab('register'));

  // 切换登录/注册链接
  $('#showRegister').addEventListener('click', (e) => {
    e.preventDefault();
    switchAuthTab('register');
  });
  $('#showLogin').addEventListener('click', (e) => {
    e.preventDefault();
    switchAuthTab('login');
  });

  // 关闭弹窗
  $('#closeAuthModal').addEventListener('click', () => hideAuthModal());
  dom.authModal.addEventListener('click', (e) => {
    if (e.target === dom.authModal) hideAuthModal();
  });

  // 欢迎页按钮
  $('#topLoginBtn').addEventListener('click', () => showAuthModal('login'));
  $('#topRegisterBtn').addEventListener('click', () => showAuthModal('register'));
  $('#welcomeLoginBtn').addEventListener('click', () => showAuthModal('login'));
  $('#welcomeRegisterBtn').addEventListener('click', () => showAuthModal('register'));

  // 登录
  $('#loginBtn').addEventListener('click', () => doLogin());
  dom.loginPassword.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  dom.loginUsername.addEventListener('keydown', (e) => { if (e.key === 'Enter') dom.loginPassword.focus(); });

  // 注册
  $('#registerBtn').addEventListener('click', () => doRegister());
  dom.regPasswordConfirm.addEventListener('keydown', (e) => { if (e.key === 'Enter') doRegister(); });
}

async function checkAuthState() {
  if (!state.token) {
    showGuestUI();
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/auth/me?token=${state.token}`);
    if (res.ok) {
      const data = await res.json();
      state.username = data.username;
      showLoggedInUI();
    } else {
      clearAuth();
      showGuestUI();
    }
  } catch {
    showGuestUI();
  }
}

function showGuestUI() {
  // 未登录：隐藏侧边栏和制品面板，显示欢迎页的登录/注册按钮
  document.body.classList.remove('logged-in');
  dom.appLayout.style.display = 'flex';
  dom.sidebar.style.display = 'none';
  dom.artifactPanel.style.display = 'none';
  dom.welcomeTopbar.classList.add('show');
  dom.welcomeAuthActions.classList.add('show');
  dom.welcomeScreen.style.display = 'flex';
  dom.chatContainer.style.display = 'none';
}

function showLoggedInUI() {
  hideAuthModal();
  document.body.classList.add('logged-in');
  dom.appLayout.style.display = 'flex';
  dom.sidebar.style.display = 'flex';
  dom.artifactPanel.style.display = '';
  dom.welcomeTopbar.classList.remove('show');
  dom.welcomeAuthActions.classList.remove('show');
  // 设置用户信息
  dom.sidebarUsername.textContent = state.username || '用户';
  dom.userAvatar.textContent = (state.username || 'U')[0].toUpperCase();
  // 绑定主应用事件（只绑定一次）
  if (!state._appEventsBound) {
    bindAppEvents();
    state._appEventsBound = true;
  }
  loadSessions();
}

function clearAuth() {
  state.token = null;
  state.username = null;
  localStorage.removeItem('iredev_token');
  localStorage.removeItem('iredev_username');
}

function saveAuth(token, username) {
  state.token = token;
  state.username = username;
  localStorage.setItem('iredev_token', token);
  localStorage.setItem('iredev_username', username);
}

async function doLogin() {
  const username = dom.loginUsername.value.trim();
  const password = dom.loginPassword.value.trim();
  dom.loginError.textContent = '';

  if (!username || !password) {
    dom.loginError.textContent = '请输入用户名和密码';
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      dom.loginError.textContent = data.detail || '登录失败';
      return;
    }
    saveAuth(data.token, data.username);
    showLoggedInUI();
  } catch (err) {
    dom.loginError.textContent = '连接服务器失败，请确保后端已启动';
  }
}

async function doRegister() {
  const username = dom.regUsername.value.trim();
  const password = dom.regPassword.value.trim();
  const confirm  = dom.regPasswordConfirm.value.trim();
  dom.registerError.textContent = '';

  if (!username || !password) {
    dom.registerError.textContent = '请填写所有字段';
    return;
  }
  if (password !== confirm) {
    dom.registerError.textContent = '两次密码输入不一致';
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      dom.registerError.textContent = data.detail || '注册失败';
      return;
    }
    saveAuth(data.token, data.username);
    showLoggedInUI();
  } catch (err) {
    dom.registerError.textContent = '连接服务器失败，请确保后端已启动';
  }
}

function logout() {
  if (state.ws) { state.ws.close(); state.ws = null; }
  clearAuth();
  state.sessions = [];
  state.activeSessionId = null;
  state.messages = [];
  state.artifacts = [];
  showGuestUI();
  // 重置登录表单
  dom.loginUsername.value = '';
  dom.loginPassword.value = '';
  dom.loginError.textContent = '';
  switchAuthTab('login');
}

// ═══════════════════════════════════════════════════════════
// 事件绑定（主应用）
// ═══════════════════════════════════════════════════════════

function bindAppEvents() {
  // 新建项目
  $('#newProjectBtn').addEventListener('click', () => showNewProjectModal());
  $('#cancelNewProject').addEventListener('click', () => hideNewProjectModal());
  $('#confirmNewProject').addEventListener('click', () => createNewProject());
  // 控制模式切换提示
  document.querySelectorAll('input[name="controlMode"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const isStep = radio.value === 'step';
      document.getElementById('hintPost').style.display = isStep ? 'none' : '';
      document.getElementById('hintStep').style.display = isStep ? '' : 'none';
    });
  });
  dom.projectNameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') createNewProject();
  });
  dom.newProjectModal.addEventListener('click', (e) => {
    if (e.target === dom.newProjectModal) hideNewProjectModal();
  });

  // 发送消息
  dom.sendBtn.addEventListener('click', () => sendMessage());
  dom.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  dom.messageInput.addEventListener('input', autoResize);

  // 切换面板
  $('#sidebarToggle').addEventListener('click', () => {
    dom.sidebar.classList.toggle('collapsed');
  });
  $('#artifactToggle').addEventListener('click', () => {
    dom.artifactPanel.classList.toggle('collapsed');
  });
  $('#closeArtifactPanel').addEventListener('click', () => {
    dom.artifactPanel.classList.add('collapsed');
  });

  // 制品查看器关闭
  $('#closeViewerBtn').addEventListener('click', closeArtifactViewer);
  dom.artifactViewerOverlay.addEventListener('click', (e) => {
    if (e.target === dom.artifactViewerOverlay) closeArtifactViewer();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeArtifactViewer();
      hideNewProjectModal();
      hideSettingsModal();
      hideAuthModal();
    }
  });

  // 设置
  $('#settingsBtn').addEventListener('click', () => showSettingsModal());
  $('#cancelSettings').addEventListener('click', () => hideSettingsModal());
  $('#saveSettings').addEventListener('click', () => saveSettings());
  dom.settingsModal.addEventListener('click', (e) => {
    if (e.target === dom.settingsModal) hideSettingsModal();
  });
  $('#toggleApiKeyVisibility').addEventListener('click', () => {
    const input = dom.settingsApiKey;
    input.type = input.type === 'password' ? 'text' : 'password';
  });

  // 退出登录
  $('#logoutBtn').addEventListener('click', () => logout());
}

function autoResize() {
  dom.messageInput.style.height = 'auto';
  dom.messageInput.style.height = Math.min(dom.messageInput.scrollHeight, 120) + 'px';
}

// ═══════════════════════════════════════════════════════════
// 会话管理
// ═══════════════════════════════════════════════════════════

async function loadSessions() {
  try {
    const res = await fetch(`${API_BASE}/api/sessions`);
    state.sessions = await res.json();
    renderSessionList();
  } catch (err) {
    console.warn('Failed to load sessions:', err);
  }
}

function renderSessionList() {
  if (state.sessions.length === 0) {
    dom.sessionList.innerHTML = '<div class="empty-state">暂无会话记录</div>';
    return;
  }

  dom.sessionList.innerHTML = state.sessions.map(s => `
    <div class="session-item ${s.id === state.activeSessionId ? 'active' : ''}" 
         data-id="${s.id}" onclick="switchSession('${s.id}')">
      <div class="session-dot ${s.status}"></div>
      <div class="session-info">
        <div class="session-name">${escapeHtml(s.project_name)}</div>
        <div class="session-date">${formatTime(s.created_at)}</div>
      </div>
    </div>
  `).join('');
}

async function createNewProject() {
  const name = dom.projectNameInput.value.trim();
  if (!name) {
    dom.projectNameInput.focus();
    return;
  }

  // 获取选择的语言
  const languageRadio = document.querySelector('input[name="language"]:checked');
  const language = languageRadio ? languageRadio.value : 'zh';

  // 获取控制模式
  const controlRadio = document.querySelector('input[name="controlMode"]:checked');
  const controlMode = controlRadio ? controlRadio.value : 'post';

  try {
    const res = await fetch(`${API_BASE}/api/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_name: name, language: language, control_mode: controlMode, token: state.token || '' }),
    });
    const session = await res.json();
    state.sessions.unshift(session);
    hideNewProjectModal();
    switchSession(session.id);
  } catch (err) {
    console.error('Failed to create session:', err);
    alert('创建会话失败，请确保后端服务已启动。');
  }
}

function switchSession(sessionId) {
  if (state.activeSessionId === sessionId) return;

  // 关闭旧 WebSocket
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }

  state.activeSessionId = sessionId;
  state.messages = [];
  state.artifacts = [];
  state.waitingInput = false;
  state.candidates = [];
  removeAgentActivity(false);

  const session = state.sessions.find(s => s.id === sessionId);
  if (!session) return;

  // 更新 UI
  dom.welcomeScreen.style.display = 'none';
  dom.chatContainer.style.display = 'flex';
  dom.chatProjectName.textContent = session.project_name;
  dom.messagesList.innerHTML = '';
  clearArtifactList();
  updateStatus(session.status || 'idle');
  renderSessionList();

  // 连接 WebSocket
  connectWebSocket(sessionId);
}

// ═══════════════════════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════════════════════

function connectWebSocket(sessionId) {
  const wsUrl = `${WS_BASE}/ws/${sessionId}`;
  console.log(`[WS] Connecting to ${wsUrl}`);
  
  const ws = new WebSocket(wsUrl);
  state.ws = ws;

  ws.onopen = () => {
    console.log('[WS] Connected');
    // 开始 Ping 心跳
    ws._pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWSEvent(data);
    } catch (err) {
      console.warn('[WS] Parse error:', err);
    }
  };

  ws.onclose = () => {
    console.log('[WS] Disconnected');
    clearInterval(ws._pingInterval);
  };

  ws.onerror = (err) => {
    console.error('[WS] Error:', err);
  };
}

function handleWSEvent(event) {
  switch (event.type) {
    case 'agent_message':
      addMessage(event);
      break;
    case 'agent_activity':
      if (event.status === 'started') {
        showAgentActivity(event.agent, event.activity);
      } else {
        removeAgentActivity(true);
      }
      break;
    case 'input_request':
      removeAgentActivity(true);
      showInputRequest(event);
      break;
    case 'artifact_update':
      addArtifact(event);
      break;
    case 'pipeline_complete':
      removeAgentActivity(false);
      updateStatus('completed');
      updateSessionStatus('completed');
      setInputEnabled(false);
      break;
    case 'error':
      removeAgentActivity(false);
      updateStatus('error');
      addMessage({ agent: 'system', content: `❌ ${event.message}`, msg_type: 'error' });
      break;
    case 'pong':
      break;
    default:
      console.log('[WS] Unknown event:', event.type);
  }
}

// ═══════════════════════════════════════════════════════════
// 消息渲染
// ═══════════════════════════════════════════════════════════

function addMessage(msg) {
  removeAgentActivity(true);
  state.messages.push(msg);
  const el = createMessageElement(msg);
  dom.messagesList.appendChild(el);
  scrollToBottom();
}

function createMessageElement(msg) {
  const agent = msg.agent || 'system';
  const meta = AGENT_META[agent] || AGENT_META.system;
  const isSystem = agent === 'system';
  const isUser = agent === 'user';

  const div = document.createElement('div');
  div.className = `message ${isSystem ? 'system-message' : ''} ${isUser ? 'user-message' : ''}`;

  const renderedContent = renderMarkdown(msg.content || '');
  const time = msg.timestamp ? formatTimeShort(msg.timestamp) : '';

  div.innerHTML = `
    <div class="message-avatar" style="background: ${meta.color}">
      ${meta.emoji}
    </div>
    <div class="message-body">
      <div class="message-header">
        <span class="message-agent-name" style="color: ${meta.color}">${meta.label}</span>
        <span class="message-time">${time}</span>
      </div>
      <div class="message-content">${renderedContent}</div>
    </div>
  `;

  return div;
}

function renderMarkdown(text) {
  try {
    return marked.parse(text);
  } catch {
    return escapeHtml(text);
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    dom.messagesContainer.scrollTop = dom.messagesContainer.scrollHeight;
  });
}

// ═══════════════════════════════════════════════════════════
// 输入处理
// ═══════════════════════════════════════════════════════════

function showInputRequest(event) {
  state.waitingInput = true;
  state.candidates = event.candidates || [];
  
  updateStatus('waiting_input');
  updateSessionStatus('waiting_input');
  setInputEnabled(true);

  // 显示候选回答
  if (state.candidates.length > 0) {
    dom.candidatesBar.style.display = 'block';
    dom.candidatesList.innerHTML = state.candidates
      .filter(c => c && c.trim())
      .map((c, i) => `
        <button class="candidate-option" onclick="selectCandidate(${i})">
          <span class="candidate-num">${i + 1}</span>
          ${escapeHtml(c)}
        </button>
      `).join('');
  } else {
    dom.candidatesBar.style.display = 'none';
  }

  dom.messageInput.focus();
}

function selectCandidate(index) {
  const text = state.candidates[index];
  if (text) {
    dom.messageInput.value = text;
    autoResize();
    sendMessage();
  }
}

// 在全局注册 selectCandidate
window.selectCandidate = selectCandidate;
window.switchSession = switchSession;

function sendMessage() {
  const text = dom.messageInput.value.trim();
  if (!text || !state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  // 发送到服务器
  state.ws.send(JSON.stringify({ type: 'user_input', content: text }));

  // 清理输入
  dom.messageInput.value = '';
  dom.messageInput.style.height = 'auto';
  dom.candidatesBar.style.display = 'none';
  state.candidates = [];
  state.waitingInput = false;

  setInputEnabled(false);
  updateStatus('running');
  updateSessionStatus('running');
}

function setInputEnabled(enabled) {
  dom.messageInput.disabled = !enabled;
  dom.sendBtn.disabled = !enabled;
  if (enabled) {
    dom.messageInput.placeholder = '输入您的回复...';
  } else if (state.waitingInput) {
    dom.messageInput.placeholder = '输入您的回复...';
  } else {
    dom.messageInput.placeholder = '等待 AI 处理中...';
  }
}

// ═══════════════════════════════════════════════════════════
// 制品管理
// ═══════════════════════════════════════════════════════════

function addArtifact(artifact) {
  const name = artifact.name;
  
  // 去重
  if (state.artifacts.find(a => a.name === name)) return;
  
  state.artifacts.push(artifact);
  renderArtifactItem(artifact);
  updateProgress();
  updateArtifactCount();
}

function renderArtifactItem(artifact) {
  const name = artifact.name;
  const category = getArtifactCategory(name);
  const icon = getArtifactIcon(name);
  const time = artifact.timestamp ? formatTimeShort(artifact.timestamp) : '';

  const container = {
    doc: dom.docArtifacts,
    model: dom.modelArtifacts,
    review: dom.reviewArtifacts,
  }[category];

  if (!container) return;

  // 清空占位符
  const empty = container.querySelector('.empty-artifact');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = 'artifact-item';
  el.onclick = () => openArtifactViewer(name);
  el.innerHTML = `
    <div class="artifact-icon ${category}">
      ${icon}
    </div>
    <div class="artifact-info">
      <div class="artifact-name">${escapeHtml(name)}</div>
      <div class="artifact-time">${time}</div>
    </div>
    <span class="artifact-status-icon">✅</span>
  `;

  container.appendChild(el);
}

function getArtifactCategory(name) {
  if (name.startsWith('issue_')) return 'review';
  if (name.endsWith('.puml') || name.endsWith('.png') || name.endsWith('.svg')) return 'model';
  return 'doc';
}

function getArtifactIcon(name) {
  if (name.startsWith('issue_')) return '📋';
  if (name.endsWith('.puml')) return '📐';
  if (name.endsWith('.png')) return '🖼️';
  if (name.includes('BRD')) return '📑';
  if (name.includes('SRS')) return '📜';
  if (name.includes('SyRS')) return '📊';
  if (name.includes('UserRD')) return '📝';
  if (name.includes('UserList')) return '👥';
  if (name.includes('dialogue')) return '💬';
  if (name.includes('description')) return '📌';
  return '📄';
}

function clearArtifactList() {
  dom.docArtifacts.innerHTML = '<div class="empty-artifact">尚无文档制品</div>';
  dom.modelArtifacts.innerHTML = '<div class="empty-artifact">尚无模型制品</div>';
  dom.reviewArtifacts.innerHTML = '<div class="empty-artifact">尚无审查报告</div>';
  dom.progressFill.style.width = '0%';
  dom.progressPercent.textContent = '0%';
  dom.artifactCountBadge.style.display = 'none';
}

function updateProgress() {
  const existing = new Set(state.artifacts.map(a => a.name));
  let count = 0;
  for (const name of PIPELINE_ORDER) {
    if (existing.has(name)) count++;
  }
  const pct = Math.round((count / PIPELINE_ORDER.length) * 100);
  dom.progressFill.style.width = pct + '%';
  dom.progressPercent.textContent = pct + '%';
}

function updateArtifactCount() {
  const count = state.artifacts.length;
  if (count > 0) {
    dom.artifactCountBadge.textContent = count;
    dom.artifactCountBadge.style.display = 'flex';
  } else {
    dom.artifactCountBadge.style.display = 'none';
  }
}

// ── 制品查看器 ────────────────────────────────────────────

async function openArtifactViewer(name) {
  dom.artifactViewerOverlay.style.display = 'flex';
  dom.artifactViewerTitle.textContent = name;
  dom.artifactViewerContent.innerHTML = '<div class="loading-spinner"></div>';

  try {
    const res = await fetch(`${API_BASE}/api/sessions/${state.activeSessionId}/artifacts/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.type === 'image') {
      dom.artifactViewerContent.innerHTML = `
        <div style="text-align: center;">
          <img src="data:${data.mime};base64,${data.content}" alt="${escapeHtml(name)}" style="max-width: 100%;" />
        </div>
      `;
    } else {
      const html = renderMarkdown(data.content);
      dom.artifactViewerContent.innerHTML = html;
      // 代码高亮
      dom.artifactViewerContent.querySelectorAll('pre code').forEach(block => {
        hljs.highlightElement(block);
      });
    }
  } catch (err) {
    dom.artifactViewerContent.innerHTML = `
      <div style="text-align: center; color: #ef4444; padding: 40px;">
        <p>无法加载制品内容</p>
        <p style="font-size: 12px; color: #999;">${escapeHtml(err.message)}</p>
      </div>
    `;
  }
}

function closeArtifactViewer() {
  dom.artifactViewerOverlay.style.display = 'none';
}

// ═══════════════════════════════════════════════════════════
// 状态管理
// ═══════════════════════════════════════════════════════════

function updateStatus(status) {
  const badge = dom.statusBadge;
  badge.className = 'status-badge ' + status;
  
  const textMap = {
    idle: '就绪',
    running: '运行中',
    waiting_input: '等待输入',
    completed: '已完成',
    error: '出错',
  };
  
  badge.querySelector('.status-text').textContent = textMap[status] || status;
}

function updateSessionStatus(status) {
  const session = state.sessions.find(s => s.id === state.activeSessionId);
  if (session) {
    session.status = status;
    renderSessionList();
  }
}

// ═══════════════════════════════════════════════════════════
// 模态框
// ═══════════════════════════════════════════════════════════

function showNewProjectModal() {
  dom.newProjectModal.style.display = 'flex';
  dom.projectNameInput.value = '';
  // 重置控制模式为默认
  const modePost = document.getElementById('modePost');
  if (modePost) modePost.checked = true;
  document.getElementById('hintPost').style.display = '';
  document.getElementById('hintStep').style.display = 'none';
  setTimeout(() => dom.projectNameInput.focus(), 100);
}

function hideNewProjectModal() {
  dom.newProjectModal.style.display = 'none';
}

// ═══════════════════════════════════════════════════════════
// 设置模态框
// ═══════════════════════════════════════════════════════════

async function showSettingsModal() {
  dom.settingsModal.style.display = 'flex';
  // 加载当前设置
  dom.settingsApiKey.value = '';
  dom.settingsBaseUrl.value = '';
  dom.settingsModel.value = '';
  try {
    const res = await fetch(`${API_BASE}/api/settings?token=${state.token || ''}`);
    if (res.ok) {
      const data = await res.json();
      dom.settingsApiKey.placeholder = data.api_key || 'sk-...';
      dom.settingsBaseUrl.value = data.base_url || '';
      dom.settingsModel.value = data.model || '';
    }
  } catch (err) {
    console.warn('Failed to load settings:', err);
  }
}

function hideSettingsModal() {
  dom.settingsModal.style.display = 'none';
}

async function saveSettings() {
  const apiKey  = dom.settingsApiKey.value.trim();
  const baseUrl = dom.settingsBaseUrl.value.trim();
  const model   = dom.settingsModel.value.trim();

  const body = {};
  if (apiKey)  body.api_key  = apiKey;
  if (baseUrl) body.base_url = baseUrl;
  if (model)   body.model    = model;

  try {
    const res = await fetch(`${API_BASE}/api/settings?token=${state.token || ''}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      hideSettingsModal();
      // 简单提示
      const toast = document.createElement('div');
      toast.className = 'toast-message';
      toast.textContent = '✅ 设置已保存，将在下次创建项目时生效';
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 3000);
    } else {
      const data = await res.json();
      alert(data.detail || '保存失败');
    }
  } catch (err) {
    alert('连接服务器失败');
  }
}

// ═══════════════════════════════════════════════════════════
// Agent 活动指示器
// ═══════════════════════════════════════════════════════════

let _activityEl = null;
let _activityTimer = null;
let _activityStartTime = null;

function showAgentActivity(agent, activity) {
  removeAgentActivity(false);

  const meta = AGENT_META[agent] || AGENT_META.system;
  _activityStartTime = Date.now();

  const el = document.createElement('div');
  el.className = 'agent-activity-indicator';
  el.style.setProperty('--activity-color', meta.color);
  el.innerHTML = `
    <div class="activity-glow" style="background: ${meta.color}"></div>
    <div class="activity-avatar" style="background: ${meta.color}">
      ${meta.emoji}
    </div>
    <div class="activity-body">
      <div class="activity-agent-name" style="color: ${meta.color}">${meta.label}</div>
      <div class="activity-text">${escapeHtml(activity)}</div>
      <div class="activity-progress">
        <div class="activity-dots">
          <span class="activity-dot"></span>
          <span class="activity-dot"></span>
          <span class="activity-dot"></span>
        </div>
        <span class="activity-elapsed">0s</span>
      </div>
    </div>
  `;

  dom.messagesList.appendChild(el);
  _activityEl = el;

  _activityTimer = setInterval(() => {
    if (_activityEl && _activityStartTime) {
      const elapsed = Math.floor((Date.now() - _activityStartTime) / 1000);
      const elapsedEl = _activityEl.querySelector('.activity-elapsed');
      if (elapsedEl) {
        if (elapsed < 60) {
          elapsedEl.textContent = `${elapsed}s`;
        } else {
          const min = Math.floor(elapsed / 60);
          const sec = elapsed % 60;
          elapsedEl.textContent = `${min}m ${sec}s`;
        }
      }
    }
  }, 1000);

  scrollToBottom();
}

function removeAgentActivity(animate) {
  if (_activityTimer) {
    clearInterval(_activityTimer);
    _activityTimer = null;
  }
  _activityStartTime = null;

  if (_activityEl) {
    if (animate) {
      _activityEl.classList.add('fade-out');
      const el = _activityEl;
      _activityEl = null;
      setTimeout(() => {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 300);
    } else {
      if (_activityEl.parentNode) _activityEl.parentNode.removeChild(_activityEl);
      _activityEl = null;
    }
  }
}

// ═══════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════

function escapeHtml(text) {
  const el = document.createElement('div');
  el.textContent = text;
  return el.innerHTML;
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  } catch {
    return isoStr;
  }
}

function formatTimeShort(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}
