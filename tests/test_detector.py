from hive.detector import SessionState, detect_state, detect_model, detect_urls, probe_url


class TestDetectState:
    def test_waiting_prompt_box(self):
        pane = "some output\n\n╭─────────────────────╮\n│ > _                 │\n╰─────────────────────╯\n"
        assert detect_state(pane) == SessionState.WAITING

    def test_waiting_arrow_prompt(self):
        pane = "some output\n❯ \n"
        assert detect_state(pane) == SessionState.WAITING

    def test_working_spinner(self):
        pane = "some output\n⠋ Thinking...\n"
        assert detect_state(pane) == SessionState.WORKING

    def test_working_tool_call(self):
        pane = "some output\n  Running: pytest tests/ -v\n"
        assert detect_state(pane) == SessionState.WORKING

    def test_exited_empty(self):
        pane = ""
        assert detect_state(pane) == SessionState.EXITED

    def test_default_unknown(self):
        pane = "some random output\nno known pattern\n"
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
