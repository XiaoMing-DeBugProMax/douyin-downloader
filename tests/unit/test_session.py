from douyin_downloader.session import SessionManager


def test_launch_token_is_single_use() -> None:
    manager = SessionManager()
    token = manager.issue_launch_token()

    assert manager.consume_launch_token(token) is True
    assert manager.consume_launch_token(token) is False


def test_cookie_comparison_accepts_only_current_session() -> None:
    manager = SessionManager()

    assert manager.valid_cookie(manager.cookie_token)
    assert not manager.valid_cookie("wrong")
