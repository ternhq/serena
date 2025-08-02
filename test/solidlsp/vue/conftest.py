import logging
from pathlib import Path

import pytest

from serena.constants import SERENA_MANAGED_DIR_IN_HOME
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.ls_logger import LanguageServerLogger
from solidlsp.settings import SolidLSPSettings


@pytest.fixture(scope="session")
def vue_language_server():
    """Create a TypeScript language server configured for Vue testing."""
    repo_path = Path(__file__).parent.parent.parent / "resources" / "repos" / "vue" / "test_repo"
    
    config = LanguageServerConfig(
        code_language=Language.TYPESCRIPT,
        ignored_paths=[],
        trace_lsp_communication=False
    )
    logger = LanguageServerLogger(log_level=logging.ERROR)
    
    server = SolidLanguageServer.create(
        config,
        logger,
        str(repo_path),
        solidlsp_settings=SolidLSPSettings(solidlsp_dir=SERENA_MANAGED_DIR_IN_HOME)
    )
    
    server.start()
    try:
        yield server
    finally:
        server.stop()


# Override the language_server fixture for Vue tests
@pytest.fixture(scope="session")
def language_server(request):
    """Override language_server fixture to use Vue test repo for TypeScript language."""
    if hasattr(request, "param") and request.param == Language.TYPESCRIPT:
        # Use Vue-specific fixture
        repo_path = Path(__file__).parent.parent.parent / "resources" / "repos" / "vue" / "test_repo"
        
        config = LanguageServerConfig(
            code_language=Language.TYPESCRIPT,
            ignored_paths=[],
            trace_lsp_communication=False
        )
        logger = LanguageServerLogger(log_level=logging.ERROR)
        
        server = SolidLanguageServer.create(
            config,
            logger,
            str(repo_path),
            solidlsp_settings=SolidLSPSettings(solidlsp_dir=SERENA_MANAGED_DIR_IN_HOME)
        )
        
        server.start()
        try:
            yield server
        finally:
            server.stop()
    else:
        raise ValueError("This fixture is for Vue tests only")