from hive.detector import SessionState, detect_state, detect_model, detect_urls, probe_url


class TestDetectState:
    def test_waiting_prompt_box(self):
        pane = "Claude Code v2.1\nsome output\n\n╭─────────────────────╮\n│ > _                 │\n╰─────────────────────╯\n"
        assert detect_state(pane) == SessionState.WAITING

    def test_waiting_arrow_prompt(self):
        pane = "Claude Code v2.1\nsome output\n❯ \n"
        assert detect_state(pane) == SessionState.WAITING

    def test_waiting_permission_mode(self):
        pane = "Claude Code v2.1\nsome output\n❯❯ bypass permissions on (shift+tab to cycle)\n"
        assert detect_state(pane) == SessionState.WAITING

    def test_working_spinner(self):
        pane = "Claude Code v2.1\nsome output\n⠋ Thinking...\n"
        assert detect_state(pane) == SessionState.WORKING

    def test_working_tool_call(self):
        pane = "Claude Code v2.1\nsome output\n  Running: pytest tests/ -v\n"
        assert detect_state(pane) == SessionState.WORKING

    def test_working_v2_status_line_with_visible_prompt(self):
        pane = (
            "Claude Code v2.1.114\n"
            "Opus 4.7\n\n"
            "❯ investigate the production alerts\n\n"
            "● Calling proxy-mcp 3 times…\n\n"
            "✢ Precipitating… (16s · ↓ 76 tokens · thinking with high effort)\n\n"
            "──── free-002 ──\n"
            "❯ \n"
            "───────────────\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WORKING

    def test_working_v2_esc_to_interrupt(self):
        pane = (
            "Claude Code v2.1\nsome output\n"
            "✻ Cogitating… (4s · ↑ 1.2k tokens · esc to interrupt)\n"
            "❯ \n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WORKING

    def test_empty_pane(self):
        pane = ""
        assert detect_state(pane) == SessionState.BOOTSTRAPPING

    def test_bootstrapping_no_banner(self):
        pane = "Loading MCP servers...\n"
        assert detect_state(pane) == SessionState.BOOTSTRAPPING

    def test_default_working(self):
        pane = "Claude Code v2.1\nsome random output\nno known pattern\n"
        assert detect_state(pane) == SessionState.WORKING


class TestDetectModel:
    def test_opus_model(self):
        pane = "some stuff\n Opus 4.6 (1M context)\nmore stuff\n"
        model, context_str = detect_model(pane)
        assert model == "opus-4.6"
        assert context_str == "1M"

    def test_sonnet_model(self):
        pane = "Sonnet 4.6\n"
        model, context_str = detect_model(pane)
        assert model == "sonnet-4.6"
        assert context_str is None

    def test_haiku_model(self):
        pane = "Haiku 4.5\n"
        model, context_str = detect_model(pane)
        assert model == "haiku-4.5"
        assert context_str is None

    def test_no_model(self):
        pane = "no model info here\n"
        model, context_str = detect_model(pane)
        assert model is None
        assert context_str is None


class TestDetectUrls:
    def test_localhost_urls(self):
        scrollback = "Started server on http://localhost:3000\nAlso running http://localhost:8080/api\nDone.\n"
        urls = detect_urls(scrollback)
        assert "localhost:3000" in urls
        assert "localhost:8080" in urls

    def test_127_urls(self):
        scrollback = "http://127.0.0.1:5173\n"
        urls = detect_urls(scrollback)
        assert "localhost:5173" in urls

    def test_no_urls(self):
        scrollback = "no urls here\n"
        urls = detect_urls(scrollback)
        assert urls == []

    def test_dedup(self):
        scrollback = "http://localhost:3000\nhttp://localhost:3000\n"
        urls = detect_urls(scrollback)
        assert len(urls) == 1
