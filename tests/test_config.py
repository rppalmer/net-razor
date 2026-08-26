def test_operator_data_resolves_to_the_home_directory_not_the_checkout() -> None:
    """An MCP host picks the working directory; the checkout may not be near it.

    Credentials and operator data resolved from the checkout are only found when
    something happens to launch from the right place, and put secrets in a
    directory under version control.
    """
    from pathlib import Path

    from net_razor.config import Settings

    settings = Settings(_env_file=None)
    home = Path.home() / ".net-razor"

    assert settings.database_path.is_relative_to(home)
    assert settings.podcasts_file.is_relative_to(home)
    assert not settings.database_path.is_relative_to(settings.repo_root)


def test_a_relative_override_resolves_against_home_too() -> None:
    """Otherwise DATABASE_PATH=data/x.db silently writes back into the checkout."""
    from pathlib import Path

    from net_razor.config import Settings

    settings = Settings(_env_file=None, database_path=Path("scratch.db"))

    assert settings.database_path == Path.home() / ".net-razor" / "scratch.db"


def test_the_checkout_is_still_used_for_code() -> None:
    """`repo_root` locates the vendored X backend, so it must stay the checkout."""
    from net_razor.config import Settings

    settings = Settings(_env_file=None)

    assert (settings.repo_root / "pyproject.toml").exists()
