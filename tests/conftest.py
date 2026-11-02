import pytest
from helpers import TODAY

from jobengine.config import CONFIG_DIR, Config, load_yaml
from jobengine.filters.location import LocationFilter
from jobengine.filters.season import SeasonFilter
from jobengine.filters.titles import TitleFilter


@pytest.fixture(scope="session")
def cfg() -> Config:
    config = Config.load(CONFIG_DIR)  # the real titles.yml and sources.yml
    # Always test against the example profile, never your private one.
    config.profile = load_yaml(CONFIG_DIR / "profile.example.yml")
    return config


@pytest.fixture
def titles(cfg):
    return TitleFilter(cfg.titles)


@pytest.fixture
def season(cfg):
    return SeasonFilter(cfg.profile, today=TODAY)


@pytest.fixture
def location(cfg, titles):
    return LocationFilter(cfg.sources, titles.location_rule)
