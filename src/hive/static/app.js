const sessionList = document.getElementById('session-list');
const terminalHost = document.getElementById('terminal-host');
const terminalEmpty = document.getElementById('terminal-empty');
const newBtn = document.getElementById('new-session-btn');
const dialog = document.getElementById('new-session-dialog');
const dialogForm = dialog.querySelector('form');

const STATE_ICON = {
  bootstrapping: '◌',
  working: '◐',
  waiting: '●',
  dead: '✕',
};

const sessions = new Map();   // id -> {id, name, state, ...}
let activeSessionId = null;
let activeTerm = null;
let activeFit = null;
let activePtySocket = null;

function renderList() {
  sessionList.innerHTML = '';
  for (const s of sessions.values()) {
    const li = document.createElement('li');
    li.className = `state-${s.state}` + (s.id === activeSessionId ? ' active' : '');
    li.dataset.id = s.id;
    li.innerHTML = `
      <span class="icon">${STATE_ICON[s.state] ?? '?'}</span>
      <span class="name">${s.name}</span>
      <button class="kill" title="Kill">✕</button>
    `;
    li.querySelector('.kill').addEventListener('click', (e) => {
      e.stopPropagation();
      killSession(s.id);
    });
    li.addEventListener('click', () => attachToSession(s.id));
    sessionList.appendChild(li);
  }
}

async function killSession(id) {
  if (!confirm(`Kill ${sessions.get(id)?.name ?? id}?`)) return;
  await fetch(`/sessions/${id}`, { method: 'DELETE' });
  sessions.delete(id);
  if (activeSessionId === id) detachTerminal();
  renderList();
}

function detachTerminal() {
  if (activePtySocket) { activePtySocket.close(); activePtySocket = null; }
  if (activeTerm) { activeTerm.dispose(); activeTerm = null; activeFit = null; }
  terminalHost.replaceChildren();
  terminalHost.hidden = true;
  terminalEmpty.hidden = false;
  activeSessionId = null;
}

function attachToSession(id) {
  if (activeSessionId === id) return;
  detachTerminal();

  activeSessionId = id;
  terminalEmpty.hidden = true;
  terminalHost.hidden = false;

  activeTerm = new Terminal({
    convertEol: true,
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'monospace',
  });
  activeFit = new FitAddon.FitAddon();
  activeTerm.loadAddon(activeFit);
  activeTerm.open(terminalHost);
  activeFit.fit();

  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${wsProto}//${location.host}/ws/pty/${id}`);
  ws.binaryType = 'arraybuffer';

  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      activeTerm.write(ev.data);
    } else {
      const u8 = new Uint8Array(ev.data);
      activeTerm.write(u8);
    }
  };

  activeTerm.onData((d) => {
    ws.send(new TextEncoder().encode(d));
  });

  activePtySocket = ws;
  renderList();
}

window.addEventListener('resize', () => {
  if (activeFit) activeFit.fit();
});

const projectSelect = document.getElementById('project-select');
const customToggle = document.getElementById('custom-path-toggle');
const customLabel = document.getElementById('custom-path-label');
const customInput = customLabel.querySelector('input[name="custom_path"]');

customToggle.addEventListener('change', () => {
  customLabel.hidden = !customToggle.checked;
  if (customToggle.checked) {
    projectSelect.removeAttribute('required');
    customInput.setAttribute('required', '');
  } else {
    projectSelect.setAttribute('required', '');
    customInput.removeAttribute('required');
  }
});

async function refreshProjectList() {
  projectSelect.innerHTML = '<option value="">Loading…</option>';
  try {
    const r = await fetch('/projects');
    const data = await r.json();
    projectSelect.innerHTML = '';
    if (data.projects.length === 0) {
      projectSelect.innerHTML = '<option value="">(no projects in ~/Projects)</option>';
    } else {
      for (const p of data.projects) {
        const opt = document.createElement('option');
        opt.value = p.path;
        opt.textContent = p.name;
        projectSelect.appendChild(opt);
      }
    }
  } catch (e) {
    projectSelect.innerHTML = '<option value="">(failed to load)</option>';
  }
}

newBtn.addEventListener('click', async () => {
  await refreshProjectList();
  customToggle.checked = false;
  customLabel.hidden = true;
  projectSelect.setAttribute('required', '');
  customInput.removeAttribute('required');
  dialog.showModal();
});

dialog.addEventListener('close', async () => {
  if (dialog.returnValue !== 'ok') return;
  const fd = new FormData(dialogForm);
  const projectPath = customToggle.checked
    ? fd.get('custom_path')
    : fd.get('project_path');
  const r = await fetch('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_path: projectPath,
      name: fd.get('name'),
    }),
  });
  dialogForm.reset();
  if (!r.ok) {
    alert(`Create failed: ${await r.text()}`);
    return;
  }
  const created = await r.json();
  sessions.set(created.id, created);
  renderList();
});

async function loadInitial() {
  const r = await fetch('/sessions');
  const data = await r.json();
  for (const s of data.sessions) sessions.set(s.id, s);
  renderList();
}

function connectStateSocket() {
  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${wsProto}//${location.host}/ws/state`);
  ws.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    const existing = sessions.get(evt.session_id);
    if (existing) {
      existing.state = evt.state;
      renderList();
    } else {
      // Session unknown to us — refetch list
      loadInitial();
    }
  };
  ws.onclose = () => setTimeout(connectStateSocket, 1000);
}

loadInitial().then(connectStateSocket);
