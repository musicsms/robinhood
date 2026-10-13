// Robin-CTI Threat Intelligence & Workflow Replay Controller
// Zero-Build Vanilla JavaScript Interface

class RobinViewer {
  constructor() {
    this.trace = null;
    this.currentStep = 0;
    this.isPlaying = false;
    this.playTimer = null;
    this.playbackSpeed = 1.0;
    this.ws = null;
    this.wsReconnectTimer = null;
    this.wsPingTimer = null;
    this.activeRunId = null;
    this.chatHistory = [];
    this.streamingTokens = "";

    this.initElements();
    this.initListeners();
    this.fetchStatus();
    this.fetchRuns();
    this.connectLive();
  }

  initElements() {
    // Header
    this.elTargetName = document.getElementById("target-name");
    this.elStatusTor = document.getElementById("status-tor");
    this.elStatusCti = document.getElementById("status-cti");
    this.elStatusModel = document.getElementById("status-model");
    this.elStatusBadge = document.getElementById("status-badge");
    this.elRunDuration = document.getElementById("run-duration");
    this.elResultsCount = document.getElementById("results-count");
    this.elRunSelect = document.getElementById("run-select");
    this.elBtnNewInvestigation = document.getElementById("btn-new-investigation");

    // Launch Form
    this.elInvestigationCard = document.getElementById("investigation-card");
    this.elFormToggleBtn = document.getElementById("form-toggle-btn");
    this.elInvestigationForm = document.getElementById("investigation-form");
    this.elQueryInput = document.getElementById("query-input");
    this.elPresetSelect = document.getElementById("preset-select");
    this.elIterationsInput = document.getElementById("iterations-input");
    this.elModelSelect = document.getElementById("model-select");
    this.elBtnStartInvestigation = document.getElementById("btn-start-investigation");
    this.elBtnAbortInvestigation = document.getElementById("btn-abort-investigation");

    // Stage Timeline
    this.elTimelineProgress = document.getElementById("timeline-progress-badge");
    this.stageCards = {
      refine: document.getElementById("stage-refine"),
      cti_lookup: document.getElementById("stage-cti_lookup"),
      search: document.getElementById("stage-search"),
      filter: document.getElementById("stage-filter"),
      scrape: document.getElementById("stage-scrape"),
      eval_gap: document.getElementById("stage-eval_gap"),
      summarize: document.getElementById("stage-summarize"),
    };

    // Right Pane Tabs & Panels
    this.tabButtons = document.querySelectorAll(".tab-btn");
    this.tabContents = document.querySelectorAll(".tab-content");

    // Tab 1: Terminal
    this.elTerminalTitle = document.getElementById("terminal-title");
    this.elTerminalPrompt = document.getElementById("terminal-prompt");
    this.elTerminalOutput = document.getElementById("terminal-output");

    // Tab 2: Reasoning
    this.elThoughtContainer = document.getElementById("thought-container");
    this.elThoughtText = document.getElementById("thought-text");
    this.elReasoningTokens = document.getElementById("reasoning-tokens");

    // Tab 3: Findings
    this.elStatSources = document.getElementById("stat-sources");
    this.elStatCtiHits = document.getElementById("stat-cti-hits");
    this.elStatScraped = document.getElementById("stat-scraped");
    this.elFindingsList = document.getElementById("findings-list");

    // Tab 4: Report
    this.elReportTitle = document.getElementById("report-title");
    this.elReportBody = document.getElementById("report-body");
    this.elReportPivots = document.getElementById("report-pivots");
    this.elReportPivotsBox = document.getElementById("report-pivots-box");
    this.elBtnCopyReport = document.getElementById("btn-copy-report");

    // Tab 5: Followup Chat
    this.elChatHistory = document.getElementById("chat-history");
    this.elChatForm = document.getElementById("chat-form");
    this.elChatInput = document.getElementById("chat-input");
    this.elBtnChatSend = document.getElementById("btn-chat-send");

    // Tab 6: Raw JSON
    this.elRawJson = document.getElementById("raw-json");
    this.elBtnCopyJson = document.getElementById("btn-copy-json");

    // Bottom Playback Bar
    this.elSlider = document.getElementById("timeline-slider");
    this.elStepCounter = document.getElementById("step-counter");
    this.elBtnFirst = document.getElementById("btn-first");
    this.elBtnPrev = document.getElementById("btn-prev");
    this.elBtnPlay = document.getElementById("btn-play-pause");
    this.elBtnNext = document.getElementById("btn-next");
    this.elBtnLast = document.getElementById("btn-last");
    this.elSpeedSelect = document.getElementById("speed-select");
    this.elPlaybackStatus = document.getElementById("playback-status");
  }

  initListeners() {
    // Tab switching
    this.tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const tabId = btn.getAttribute("data-tab");
        this.switchTab(tabId);
      });
    });

    // Form toggle
    if (this.elFormToggleBtn && this.elInvestigationCard) {
      this.elFormToggleBtn.addEventListener("click", () => {
        const form = this.elInvestigationForm;
        if (form.style.display === "none") {
          form.style.display = "flex";
          this.elFormToggleBtn.textContent = "▾";
        } else {
          form.style.display = "none";
          this.elFormToggleBtn.textContent = "▸";
        }
      });
    }

    if (this.elBtnNewInvestigation) {
      this.elBtnNewInvestigation.addEventListener("click", () => {
        if (this.elInvestigationForm) {
          this.elInvestigationForm.style.display = "flex";
          if (this.elFormToggleBtn) this.elFormToggleBtn.textContent = "▾";
        }
        if (this.elQueryInput) {
          this.elQueryInput.focus();
        }
      });
    }

    // Launch Form Submit
    if (this.elInvestigationForm) {
      this.elInvestigationForm.addEventListener("submit", (e) => {
        e.preventDefault();
        this.submitInvestigation();
      });
    }

    // Abort button
    if (this.elBtnAbortInvestigation) {
      this.elBtnAbortInvestigation.addEventListener("click", () => {
        this.abortActiveInvestigation();
      });
    }

    // Run selector change
    if (this.elRunSelect) {
      this.elRunSelect.addEventListener("change", (e) => {
        const runId = e.target.value;
        if (runId) {
          this.fetchRunDetail(runId);
        }
      });
    }

    // Replay Slider
    if (this.elSlider) {
      this.elSlider.addEventListener("input", (e) => {
        this.goToStep(parseInt(e.target.value, 10));
      });
    }

    // Playback Buttons
    if (this.elBtnPlay) this.elBtnPlay.addEventListener("click", () => this.togglePlay());
    if (this.elBtnPrev) this.elBtnPrev.addEventListener("click", () => this.stepPrev());
    if (this.elBtnNext) this.elBtnNext.addEventListener("click", () => this.stepNext());
    if (this.elBtnFirst) this.elBtnFirst.addEventListener("click", () => this.goToStep(0));
    if (this.elBtnLast) {
      this.elBtnLast.addEventListener("click", () => {
        if (this.trace && this.trace.turns && this.trace.turns.length > 0) {
          this.goToStep(this.trace.turns.length - 1);
        }
      });
    }

    // Playback Speed
    if (this.elSpeedSelect) {
      this.elSpeedSelect.addEventListener("change", (e) => {
        this.playbackSpeed = parseFloat(e.target.value);
        if (this.isPlaying) {
          this.pause();
          this.play();
        }
      });
    }

    // Followup Chat Form
    if (this.elChatForm) {
      this.elChatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        this.submitFollowup();
      });
    }

    // Copy Buttons
    if (this.elBtnCopyReport) {
      this.elBtnCopyReport.addEventListener("click", () => {
        const text = (this.trace && this.trace.summary) || (this.elReportBody && this.elReportBody.innerText) || "";
        navigator.clipboard.writeText(text).then(() => {
          this.elBtnCopyReport.textContent = "✓ Copied!";
          setTimeout(() => (this.elBtnCopyReport.textContent = "📋 Copy Markdown"), 2000);
        });
      });
    }

    if (this.elBtnCopyJson) {
      this.elBtnCopyJson.addEventListener("click", () => {
        const jsonText = (this.elRawJson && this.elRawJson.textContent) || "";
        navigator.clipboard.writeText(jsonText).then(() => {
          this.elBtnCopyJson.textContent = "✓ Copied!";
          setTimeout(() => (this.elBtnCopyJson.textContent = "📋 Copy JSON"), 2000);
        });
      });
    }

    // Keyboard Shortcuts (Arrow navigation, Space for play/pause)
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") {
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        this.stepPrev();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        this.stepNext();
      } else if (e.key === " ") {
        e.preventDefault();
        this.togglePlay();
      }
    });

    // Stage card click triggers scrub to that stage
    Object.entries(this.stageCards).forEach(([stageName, card]) => {
      if (card) {
        card.addEventListener("click", () => {
          this.jumpToStage(stageName);
        });
      }
    });
  }

  switchTab(tabId) {
    this.tabButtons.forEach((b) => b.classList.remove("active"));
    this.tabContents.forEach((c) => c.classList.remove("active"));

    const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
    if (btn) btn.classList.add("active");

    const content = document.getElementById(tabId);
    if (content) content.classList.add("active");
  }

  // --- REST API CALLS ---

  async fetchStatus() {
    try {
      const resp = await fetch("/api/status");
      if (!resp.ok) return;
      const data = await resp.json();

      // Tor indicator
      if (this.elStatusTor) {
        const torOk = data.tor && data.tor.status === "ok";
        this.elStatusTor.innerHTML = `<span class="pulse-dot ${torOk ? '' : 'down'}"></span> Tor: ${torOk ? 'OK' : 'DOWN'}`;
      }

      // CTI indexed count
      if (this.elStatusCti) {
        const count = data.cti_indexed_count || 0;
        this.elStatusCti.textContent = `CTI: ${count.toLocaleString()} records`;
      }

      // Model info
      if (data.models && data.models.length > 0) {
        if (this.elStatusModel) {
          this.elStatusModel.textContent = `Model: ${data.models[0]}`;
        }
        if (this.elModelSelect) {
          const currentVal = this.elModelSelect.value;
          this.elModelSelect.innerHTML = `<option value="">Default (${data.models[0]})</option>`;
          data.models.forEach((m) => {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = m;
            this.elModelSelect.appendChild(opt);
          });
          if (currentVal) this.elModelSelect.value = currentVal;
        }
      }
    } catch (err) {
      console.warn("Could not fetch /api/status:", err);
    }
  }

  async fetchRuns() {
    try {
      const resp = await fetch("/api/runs");
      if (!resp.ok) return;
      const runs = await resp.json();
      if (!this.elRunSelect || !runs || runs.length === 0) return;

      this.elRunSelect.innerHTML = "";
      runs.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r.id;
        const q = (r.target_query || r.id).slice(0, 32);
        opt.textContent = `${q} (${r.status || 'DONE'})`;
        this.elRunSelect.appendChild(opt);
      });

      // Load first run if not in active live session
      if (!this.activeRunId && runs.length > 0) {
        this.fetchRunDetail(runs[0].id);
      }
    } catch (err) {
      console.warn("Could not fetch /api/runs:", err);
    }
  }

  async fetchRunDetail(runId) {
    try {
      this.setPlaybackStatus(`Loading run ${runId}...`);
      const resp = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      if (!resp.ok) {
        this.setPlaybackStatus(`Run ${runId} not found`);
        return;
      }
      const bundle = await resp.json();
      this.loadTrace(bundle);
      this.setPlaybackStatus(`Loaded run ${runId}`);
    } catch (err) {
      console.error("Failed to load run detail:", err);
      this.setPlaybackStatus(`Error loading ${runId}`);
    }
  }

  async submitInvestigation() {
    const query = this.elQueryInput ? this.elQueryInput.value.trim() : "";
    if (!query) return;

    // Get selected scope radio
    const scopeEl = document.querySelector('input[name="scope"]:checked');
    const scope = scopeEl ? scopeEl.value : "darkweb";
    const preset = this.elPresetSelect ? this.elPresetSelect.value : "threat_intel";
    const iterations = this.elIterationsInput ? parseInt(this.elIterationsInput.value, 10) : 1;
    const model = this.elModelSelect ? this.elModelSelect.value : "";

    const payload = {
      query,
      search_scope: scope,
      preset,
      max_iterations: iterations,
      model,
    };

    try {
      this.setExecutionState("RUNNING");
      if (this.elBtnStartInvestigation) this.elBtnStartInvestigation.disabled = true;
      if (this.elBtnAbortInvestigation) this.elBtnAbortInvestigation.disabled = false;

      const resp = await fetch("/api/investigate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        const errData = await resp.json();
        alert(`Failed to start investigation: ${errData.error || resp.statusText}`);
        this.setExecutionState("FAILED");
        this.resetFormButtons();
        return;
      }

      const res = await resp.json();
      this.activeRunId = res.run_id;

      // Initialize empty active trace
      this.trace = {
        metadata: {
          id: res.run_id,
          target_query: query,
          status: "IN_PROGRESS",
          search_scope: scope,
          preset,
          model,
        },
        turns: [],
        events: [],
        findings: [],
        summary: "",
        pivots: [],
      };

      this.resetTimelineCards();
      this.clearOutputs();
      this.updateHeaderMeta(this.trace.metadata);
      this.setPlaybackStatus(`Live investigation active: ${res.run_id}`);

      // Ensure WS is connected
      this.connectLive();
    } catch (err) {
      console.error("Error submitting investigation:", err);
      this.resetFormButtons();
    }
  }

  async abortActiveInvestigation() {
    if (!this.activeRunId) return;
    try {
      this.setPlaybackStatus(`Aborting run ${this.activeRunId}...`);
      const resp = await fetch(`/api/investigate/${encodeURIComponent(this.activeRunId)}/abort`, {
        method: "POST",
      });
      if (resp.ok) {
        this.setExecutionState("ABORTED");
        this.setPlaybackStatus(`Investigation ${this.activeRunId} aborted`);
      }
    } catch (err) {
      console.error("Failed to abort investigation:", err);
    } finally {
      this.resetFormButtons();
    }
  }

  async submitFollowup() {
    const question = this.elChatInput ? this.elChatInput.value.trim() : "";
    if (!question) return;

    this.appendChatMessage("user", question);
    this.elChatInput.value = "";
    if (this.elBtnChatSend) this.elBtnChatSend.disabled = true;

    // Loading message
    const tempMsgId = "loading-analyst-response";
    this.appendChatMessage("assistant", "Thinking & correlating threat telemetry...", tempMsgId);

    const payload = {
      question,
      run_id: (this.trace && this.trace.metadata && this.trace.metadata.id) || this.activeRunId,
      summary: (this.trace && this.trace.summary) || "",
      query: (this.trace && this.trace.metadata && this.trace.metadata.target_query) || "",
      history: this.chatHistory,
    };

    try {
      const resp = await fetch("/api/followup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const tempEl = document.getElementById(tempMsgId);
      if (tempEl) tempEl.remove();

      if (!resp.ok) {
        const err = await resp.json();
        this.appendChatMessage("assistant", `❌ Error: ${err.error || resp.statusText}`);
        return;
      }

      const res = await resp.json();
      this.appendChatMessage("assistant", res.answer);
    } catch (err) {
      console.error("Followup request error:", err);
      const tempEl = document.getElementById(tempMsgId);
      if (tempEl) tempEl.remove();
      this.appendChatMessage("assistant", `❌ Network or server error: ${err.message}`);
    } finally {
      if (this.elBtnChatSend) this.elBtnChatSend.disabled = false;
    }
  }

  appendChatMessage(role, text, elementId = null) {
    if (!this.elChatHistory) return;

    const msg = document.createElement("div");
    msg.className = `chat-message ${role}`;
    if (elementId) msg.id = elementId;

    const senderBadge = document.createElement("div");
    senderBadge.className = "sender-badge";
    senderBadge.textContent = role === "user" ? "Security Analyst" : "Robin CTI Analyst";

    const content = document.createElement("div");
    content.className = "message-content";
    content.innerHTML = this.renderMarkdown(text);

    msg.appendChild(senderBadge);
    msg.appendChild(content);
    this.elChatHistory.appendChild(msg);
    this.elChatHistory.scrollTop = this.elChatHistory.scrollHeight;

    if (!elementId) {
      this.chatHistory.push({ role, content: text });
    }
  }

  // --- WEBSOCKET LIVE STREAMING ---

  connectLive() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws/live`;

    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.warn("WebSocket creation failed:", err);
      this.scheduleWsReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log("WebSocket connected to /ws/live");
      if (this.wsReconnectTimer) {
        clearTimeout(this.wsReconnectTimer);
        this.wsReconnectTimer = null;
      }
      this.startWsPing();
    };

    this.ws.onmessage = (event) => {
      this.handleWsMessage(event.data);
    };

    this.ws.onclose = () => {
      console.log("WebSocket closed, scheduling reconnect...");
      this.stopWsPing();
      this.scheduleWsReconnect();
    };

    this.ws.onerror = (err) => {
      console.warn("WebSocket error:", err);
      if (this.ws) this.ws.close();
    };
  }

  startWsPing() {
    this.stopWsPing();
    this.wsPingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 25000);
  }

  stopWsPing() {
    if (this.wsPingTimer) {
      clearInterval(this.wsPingTimer);
      this.wsPingTimer = null;
    }
  }

  scheduleWsReconnect() {
    if (this.wsReconnectTimer) return;
    this.wsReconnectTimer = setTimeout(() => {
      this.wsReconnectTimer = null;
      this.connectLive();
    }, 3000);
  }

  handleWsMessage(raw) {
    if (raw === "pong") return;
    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return;
    }

    if (data.type === "pong") return;

    // Initialize trace if absent
    if (!this.trace) {
      this.trace = {
        metadata: { id: "live_run", target_query: "Live Session", status: "IN_PROGRESS" },
        turns: [],
        events: [],
        findings: [],
        summary: "",
        pivots: [],
      };
    }

    const evType = data.event_type;
    const stage = data.stage;

    if (evType === "stage_start") {
      this.setStageState(stage, "active");
      if (this.elTimelineProgress) {
        this.elTimelineProgress.textContent = `Stage: ${stage}`;
      }
      const turnIdx = (data.data && data.data.turn_idx) || this.trace.turns.length + 1;
      const newTurn = {
        turn_idx: turnIdx,
        stage: stage,
        description: `Running ${stage}`,
        inputs: data.data || {},
        outputs: {},
      };
      this.trace.turns.push(newTurn);
      this.updateSliderBounds();
      this.goToStep(this.trace.turns.length - 1);
      this.appendTerminalLog(`[STAGE_START] Transitioning to pipeline stage: ${stage}`);
    } else if (evType === "stage_done") {
      this.setStageState(stage, "completed");
      const dur = (data.data && data.data.duration) || 0;
      this.appendTerminalLog(`[STAGE_DONE] Completed stage ${stage} in ${dur}s`);
    } else if (evType === "token") {
      const token = (data.data && data.data.token) || "";
      this.streamingTokens += token;
      if (this.elReasoningTokens) {
        this.elReasoningTokens.textContent = this.streamingTokens;
        this.elReasoningTokens.scrollTop = this.elReasoningTokens.scrollHeight;
      }
    } else if (evType === "thought") {
      const thought = (data.data && data.data.thought) || "";
      if (this.elThoughtText) {
        this.elThoughtText.textContent = thought;
      }
    } else if (evType === "log") {
      const msg = (data.data && data.data.message) || "";
      const lvl = (data.data && data.data.level) || "info";
      this.appendTerminalLog(`[${lvl.toUpperCase()}] ${msg}`);
    } else if (evType === "cti_match") {
      const match = data.data && data.data.match;
      if (match) {
        this.addFindingCard({
          title: `CTI Match: ${match.threat_name || match.actor || 'Threat Record'}`,
          source: "cti_db",
          snippet: match.description || JSON.stringify(match),
        });
      }
    } else if (evType === "finding") {
      const finding = data.data && data.data.finding;
      if (finding) {
        this.trace.findings.push(finding);
        this.addFindingCard(finding);
        this.updateStats();
      }
    } else if (evType === "report") {
      const summary = (data.data && data.data.summary) || "";
      const pivots = (data.data && data.data.pivots) || [];
      this.trace.summary = summary;
      this.trace.pivots = pivots;
      this.renderReport(summary, pivots);
      this.setExecutionState("COMPLETED");
      this.resetFormButtons();
      this.fetchRuns();
    } else if (evType === "error") {
      const err = (data.data && data.data.error) || "Pipeline error";
      this.appendTerminalLog(`[ERROR] ${err}`);
      this.setExecutionState("FAILED");
      this.resetFormButtons();
    }

    // Keep Raw JSON updated
    if (this.elRawJson) {
      this.elRawJson.textContent = JSON.stringify(this.trace, null, 2);
    }
  }

  // --- TRACE LOADING & SCRUBBING ---

  loadTrace(bundle) {
    this.trace = bundle;
    this.pause();
    this.currentStep = 0;
    this.streamingTokens = "";

    const meta = bundle.metadata || {};
    this.updateHeaderMeta(meta);
    this.resetTimelineCards();

    // Mark stages based on turns in bundle
    const stagesInTrace = new Set();
    (bundle.turns || []).forEach((t) => {
      if (t.stage) stagesInTrace.add(t.stage);
    });
    stagesInTrace.forEach((s) => this.setStageState(s, "completed"));

    // Render findings
    this.renderFindings(bundle.findings || []);

    // Render report
    this.renderReport(bundle.summary || "", bundle.pivots || []);

    // Update Raw JSON
    if (this.elRawJson) {
      this.elRawJson.textContent = JSON.stringify(bundle, null, 2);
    }

    // Update Slider
    this.updateSliderBounds();

    if (bundle.turns && bundle.turns.length > 0) {
      this.goToStep(0);
    } else {
      this.renderEmptyStep();
    }
  }

  updateHeaderMeta(meta) {
    if (this.elTargetName) {
      this.elTargetName.textContent = meta.target_query || meta.id || "Investigation";
    }
    if (this.elRunDuration) {
      this.elRunDuration.textContent = `${Math.round(meta.duration_sec || 0)}s`;
    }
    if (this.elResultsCount) {
      this.elResultsCount.textContent = `${meta.results_count || (this.trace.findings && this.trace.findings.length) || 0}`;
    }
    if (this.elStatusBadge) {
      this.setExecutionState(meta.status || "COMPLETED");
    }
  }

  setExecutionState(status) {
    if (!this.elStatusBadge) return;
    this.elStatusBadge.textContent = status;
    this.elStatusBadge.className = "pill";

    if (status === "COMPLETED" || status === "SOLVED") {
      this.elStatusBadge.classList.add("status-completed");
    } else if (status === "RUNNING" || status === "IN_PROGRESS") {
      this.elStatusBadge.classList.add("status-running");
    } else if (status === "FAILED" || status === "ABORTED" || status === "ERROR") {
      this.elStatusBadge.classList.add("status-failed");
    }
  }

  resetFormButtons() {
    if (this.elBtnStartInvestigation) this.elBtnStartInvestigation.disabled = false;
    if (this.elBtnAbortInvestigation) this.elBtnAbortInvestigation.disabled = true;
    this.activeRunId = null;
  }

  resetTimelineCards() {
    Object.values(this.stageCards).forEach((card) => {
      if (card) {
        card.className = "stage-card";
        const badge = card.querySelector(".stage-badge");
        if (badge) badge.textContent = "pending";
      }
    });
  }

  setStageState(stageName, state) {
    const card = this.stageCards[stageName];
    if (!card) return;

    card.classList.remove("active", "completed", "error");
    const badge = card.querySelector(".stage-badge");

    if (state === "active") {
      card.classList.add("active");
      if (badge) badge.textContent = "running";
    } else if (state === "completed") {
      card.classList.add("completed");
      if (badge) badge.textContent = "done";
    } else if (state === "error") {
      card.classList.add("error");
      if (badge) badge.textContent = "error";
    }
  }

  jumpToStage(stageName) {
    if (!this.trace || !this.trace.turns) return;
    const idx = this.trace.turns.findIndex((t) => t.stage === stageName);
    if (idx !== -1) {
      this.goToStep(idx);
    }
  }

  updateSliderBounds() {
    const turnsCount = (this.trace && this.trace.turns && this.trace.turns.length) || 0;
    if (this.elSlider) {
      this.elSlider.min = 0;
      this.elSlider.max = Math.max(0, turnsCount - 1);
    }
  }

  goToStep(stepIdx) {
    if (!this.trace || !this.trace.turns || this.trace.turns.length === 0) return;
    this.currentStep = Math.max(0, Math.min(stepIdx, this.trace.turns.length - 1));
    const turn = this.trace.turns[this.currentStep];

    if (this.elSlider) this.elSlider.value = this.currentStep;
    if (this.elStepCounter) {
      this.elStepCounter.textContent = `Turn ${this.currentStep + 1} / ${this.trace.turns.length}`;
    }

    // Highlight active stage card
    Object.entries(this.stageCards).forEach(([stageName, card]) => {
      if (card) {
        if (stageName === turn.stage) {
          card.classList.add("active");
          card.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } else {
          card.classList.remove("active");
        }
      }
    });

    // Update Terminal view
    if (this.elTerminalPrompt) {
      this.elTerminalPrompt.textContent = `[turn ${turn.turn_idx}] stage: ${turn.stage || 'exec'}`;
    }
    if (this.elTerminalOutput) {
      let turnLog = `--- TURN ${turn.turn_idx}: ${turn.description || turn.stage} ---\n`;
      turnLog += `Duration: ${turn.duration_sec || 0}s\n`;
      if (turn.inputs && Object.keys(turn.inputs).length > 0) {
        turnLog += `Inputs:\n${JSON.stringify(turn.inputs, null, 2)}\n`;
      }
      if (turn.outputs && Object.keys(turn.outputs).length > 0) {
        turnLog += `Outputs:\n${JSON.stringify(turn.outputs, null, 2)}\n`;
      }
      this.elTerminalOutput.innerHTML = this.renderAnsi(turnLog);
    }

    // Update Thought
    if (this.elThoughtText) {
      const thoughtText = turn.inputs?.refined_query || turn.inputs?.query || turn.description || "";
      this.elThoughtText.textContent = thoughtText ? `Refined Target: ${thoughtText}` : "Stage Execution Step";
    }

    this.setPlaybackStatus(`Inspecting Turn ${this.currentStep + 1} (${turn.stage})`);
  }

  stepNext() {
    if (!this.trace || !this.trace.turns) return;
    if (this.currentStep < this.trace.turns.length - 1) {
      this.goToStep(this.currentStep + 1);
    } else {
      this.pause();
    }
  }

  stepPrev() {
    if (this.currentStep > 0) {
      this.goToStep(this.currentStep - 1);
    }
  }

  togglePlay() {
    if (this.isPlaying) this.pause();
    else this.play();
  }

  play() {
    if (!this.trace || !this.trace.turns || this.trace.turns.length === 0) return;
    this.isPlaying = true;
    if (this.elBtnPlay) this.elBtnPlay.textContent = "⏸ Pause";

    if (this.currentStep >= this.trace.turns.length - 1) {
      this.goToStep(0);
    }

    const interval = Math.max(200, 1500 / this.playbackSpeed);
    this.playTimer = setInterval(() => {
      if (this.currentStep >= this.trace.turns.length - 1) {
        this.pause();
      } else {
        this.stepNext();
      }
    }, interval);
  }

  pause() {
    this.isPlaying = false;
    if (this.elBtnPlay) this.elBtnPlay.textContent = "▶ Play";
    if (this.playTimer) {
      clearInterval(this.playTimer);
      this.playTimer = null;
    }
  }

  setPlaybackStatus(msg) {
    if (this.elPlaybackStatus) {
      this.elPlaybackStatus.textContent = msg;
    }
  }

  // --- OUTPUT RENDERING ---

  clearOutputs() {
    if (this.elTerminalOutput) this.elTerminalOutput.textContent = "";
    if (this.elReasoningTokens) this.elReasoningTokens.textContent = "";
    if (this.elFindingsList) this.elFindingsList.innerHTML = '<p class="empty-state">No threat sources recorded yet.</p>';
    if (this.elReportBody) this.elReportBody.innerHTML = '<p class="empty-state">Synthesizing intelligence...</p>';
    if (this.elReportPivotsBox) this.elReportPivotsBox.style.display = "none";
    this.streamingTokens = "";
  }

  appendTerminalLog(line) {
    if (!this.elTerminalOutput) return;
    const time = new Date().toLocaleTimeString();
    const formatted = `[${time}] ${line}\n`;
    this.elTerminalOutput.appendChild(document.createTextNode(formatted));
    this.elTerminalOutput.scrollTop = this.elTerminalOutput.scrollHeight;
  }

  renderFindings(findings) {
    if (!this.elFindingsList) return;
    this.elFindingsList.innerHTML = "";

    if (!findings || findings.length === 0) {
      this.elFindingsList.innerHTML = '<p class="empty-state">No threat sources recorded yet.</p>';
      return;
    }

    findings.forEach((f) => this.addFindingCard(f));
    this.updateStats();
  }

  addFindingCard(finding) {
    if (!this.elFindingsList) return;

    // Remove empty state message if present
    const empty = this.elFindingsList.querySelector(".empty-state");
    if (empty) empty.remove();

    const card = document.createElement("div");
    card.className = "finding-card";

    const titleText = finding.title || finding.link || "Threat Source";
    const linkHref = finding.link || "#";
    const src = finding.source || "web";
    const snippet = finding.snippet || finding.description || "";

    card.innerHTML = `
      <div class="finding-header">
        <span class="finding-title"><a href="${this.escapeHtml(linkHref)}" target="_blank" rel="noopener">${this.escapeHtml(titleText)}</a></span>
        <span class="finding-source">${this.escapeHtml(src)}</span>
      </div>
      <div class="finding-snippet">${this.escapeHtml(snippet)}</div>
    `;

    this.elFindingsList.appendChild(card);
    this.updateStats();
  }

  updateStats() {
    const findingsCount = (this.trace && this.trace.findings && this.trace.findings.length) || 0;
    if (this.elStatSources) this.elStatSources.textContent = findingsCount;
    if (this.elResultsCount) this.elResultsCount.textContent = findingsCount;
    if (this.elStatCtiHits) {
      const ctiHits = (this.trace && this.trace.metadata && this.trace.metadata.cti_matches_count) || 0;
      this.elStatCtiHits.textContent = ctiHits;
    }
    if (this.elStatScraped) {
      const scraped = (this.trace && this.trace.metadata && this.trace.metadata.scraped_count) || 0;
      this.elStatScraped.textContent = scraped;
    }
  }

  renderReport(summary, pivots) {
    if (this.elReportBody) {
      if (summary) {
        this.elReportBody.innerHTML = this.renderMarkdown(summary);
      } else {
        this.elReportBody.innerHTML = '<p class="empty-state">No report available for this trace.</p>';
      }
    }

    if (this.elReportPivots && this.elReportPivotsBox) {
      if (pivots && pivots.length > 0) {
        this.elReportPivotsBox.style.display = "flex";
        this.elReportPivots.innerHTML = "";
        pivots.forEach((p) => {
          const tag = document.createElement("span");
          tag.className = "pivot-tag";
          tag.textContent = p;
          this.elReportPivots.appendChild(tag);
        });
      } else {
        this.elReportPivotsBox.style.display = "none";
      }
    }
  }

  renderEmptyStep() {
    if (this.elTerminalOutput) {
      this.elTerminalOutput.textContent = "No turns recorded for this investigation.";
    }
    if (this.elTerminalPrompt) {
      this.elTerminalPrompt.textContent = "[robin-cti] $";
    }
    if (this.elStepCounter) {
      this.elStepCounter.textContent = "Turn 0 / 0";
    }
  }

  // --- TEXT PARSERS (MARKDOWN & ANSI) ---

  renderMarkdown(text) {
    if (!text) return "";
    let html = this.escapeHtml(text);

    // Code blocks ```code```
    html = html.replace(/```([\s\S]*?)```/g, (match, p1) => {
      return `<pre><code>${p1.trim()}</code></pre>`;
    });

    // Inline code `code`
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Headers
    html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');

    // Bold **text**
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Bullet lists - item
    html = html.replace(/^\s*[-*]\s+(.*$)/gim, '<li>$1</li>');

    // Markdown Links [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Line breaks to <p>
    html = html.replace(/\n\n+/g, '</p><p>');
    html = `<p>${html}</p>`;

    return html;
  }

  renderAnsi(text) {
    if (!text) return "";
    let clean = this.escapeHtml(text);

    clean = clean.replace(/\x1b\[1m/g, '<span class="ansi-bold">');
    clean = clean.replace(/\x1b\[2m/g, '<span class="ansi-dim">');
    clean = clean.replace(/\x1b\[31m/g, '<span class="ansi-red">');
    clean = clean.replace(/\x1b\[32m/g, '<span class="ansi-green">');
    clean = clean.replace(/\x1b\[33m/g, '<span class="ansi-yellow">');
    clean = clean.replace(/\x1b\[34m/g, '<span class="ansi-blue">');
    clean = clean.replace(/\x1b\[35m/g, '<span class="ansi-magenta">');
    clean = clean.replace(/\x1b\[36m/g, '<span class="ansi-cyan">');
    clean = clean.replace(/\x1b\[37m/g, '<span class="ansi-white">');
    clean = clean.replace(/\x1b\[0?m/g, '</span>');

    // Remove any remaining control codes
    clean = clean.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
    return clean;
  }

  escapeHtml(str) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(str).replace(/[&<>"']/g, (m) => map[m]);
  }
}

// Global initialization
document.addEventListener("DOMContentLoaded", () => {
  window.viewer = new RobinViewer();
});
