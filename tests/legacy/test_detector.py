from hive_legacy.detector import SessionState, detect_state, detect_model, detect_urls, probe_url


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

    def test_empty_pane(self):
        pane = ""
        assert detect_state(pane) == SessionState.BOOTSTRAPPING

    def test_bootstrapping_no_banner(self):
        pane = "Loading MCP servers...\n"
        assert detect_state(pane) == SessionState.BOOTSTRAPPING

    def test_working_nesting(self):
        pane = "Claude Code v2.1\nsome output\n✻ Nesting… (1m 2s · ↓ 1.5k tokens)\n❯❯ bypass permissions on (shift+tab to cycle)\n"
        assert detect_state(pane) == SessionState.WORKING

    def test_working_spinner_v2_active(self):
        pane = (
            "Claude Code v2.1\n"
            "some response text\n"
            "✻ Sautéing… (7s · ↑ 2.4k tokens · esc to interrupt)\n"
            "❯\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WORKING

    def test_working_spinner_v2_unicode_verb(self):
        pane = (
            "Claude Code v2.1\n"
            "some output\n"
            "✻ Pondering… (12s · ↑ 2.4k tokens)\n"
            "❯\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WORKING

    def test_working_spinner_above_long_response(self):
        long_response = "\n".join(f"line {i} of a long Claude response with hotel descriptions and URLs" for i in range(40))
        pane = (
            "Claude Code v2.1\n"
            f"{long_response}\n"
            "✻ Swooping… (39s · ↓ 221 tokens · thought for 3s)\n"
            "  ⎿  Tip: Send messages to Claude while it works\n"
            "\n"
            "─────────────────── apollon-hotel-001 ──\n"
            "❯\n"
            "──────────────────────────────────────────\n"
            "  apollon-hotel-001 | Opus 4.6 | ctx:░░░░ 3% (↑30k/↓0k) | main\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
            + "\n" * 17
        )
        assert detect_state(pane) == SessionState.WORKING

    def test_working_dilly_dallying(self):
        pane = (
            "Claude Code v2.1\n"
            "❯ sleep 15\n"
            "  Bash(sleep 15)\n"
            "  ⎿  Running…\n"
            "✢ Dilly-dallying… (2s · ↓ 92 tokens)\n"
            "❯\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WORKING

    def test_working_status_bar_not_false_positive(self):
        pane = (
            "Claude Code v2.1\n"
            "some response\n"
            "✻ Cogitated for 5s\n"
            "❯\n"
            "session-001 | Opus 4.7 | ctx:░░░░░░░░░░ 4% (↑40k/↓0k) | main\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WAITING

    def test_waiting_lingering_completion(self):
        pane = (
            "Claude Code v2.1\n"
            "some response text\n"
            "✻ Sautéed for 7s\n"
            "❯\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )
        assert detect_state(pane) == SessionState.WAITING

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
