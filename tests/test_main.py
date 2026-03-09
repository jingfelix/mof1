from mof1.main import F1TimingApp


def test_refresh_interval_label() -> None:
    assert F1TimingApp._refresh_interval_label(0) == "manual only"
    assert F1TimingApp._refresh_interval_label(30) == "every 30 seconds"


def test_refresh_settings_text_mentions_history_loading() -> None:
    text = F1TimingApp._refresh_settings_text(90, team_display_mode="colors")

    assert "every 90 seconds" in text
    assert "color swatches" in text
    assert "History stays manual" in text
