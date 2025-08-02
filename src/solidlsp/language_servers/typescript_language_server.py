"""
Provides TypeScript specific instantiation of the LanguageServer class. Contains various configurations and settings specific to TypeScript.
"""

import logging
import os
import pathlib
import shutil
import threading
from contextlib import contextmanager
from pathlib import PurePath
from time import sleep

from overrides import override
from sensai.util.logging import LogTime

from solidlsp.ls import LSPFileBuffer, SolidLanguageServer
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_logger import LanguageServerLogger
from solidlsp.ls_utils import FileUtils, PlatformId, PlatformUtils
from solidlsp.lsp_protocol_handler.lsp_constants import LSPConstants
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo
from solidlsp.settings import SolidLSPSettings

from .common import RuntimeDependency, RuntimeDependencyCollection

# Platform-specific imports
if os.name != "nt":  # Unix-like systems
    import pwd
else:
    # Dummy pwd module for Windows
    class pwd:
        @staticmethod
        def getpwuid(uid):
            return type("obj", (), {"pw_name": os.environ.get("USERNAME", "unknown")})()


# Conditionally import pwd module (Unix-only)
if not PlatformUtils.get_platform_id().value.startswith("win"):
    pass


class TypeScriptLanguageServer(SolidLanguageServer):
    """
    Provides TypeScript specific instantiation of the LanguageServer class. Contains various configurations and settings specific to TypeScript.
    """

    def __init__(
        self, config: LanguageServerConfig, logger: LanguageServerLogger, repository_root_path: str, solidlsp_settings: SolidLSPSettings
    ):
        """
        Creates a TypeScriptLanguageServer instance. This class is not meant to be instantiated directly. Use LanguageServer.create() instead.
        """
        ts_lsp_executable_path = self._setup_runtime_dependencies(logger, config, solidlsp_settings)
        super().__init__(
            config,
            logger,
            repository_root_path,
            ProcessLaunchInfo(cmd=ts_lsp_executable_path, cwd=repository_root_path),
            "typescript",
            solidlsp_settings,
        )
        self.server_ready = threading.Event()
        self.initialize_searcher_command_available = threading.Event()
    
    def _get_language_id_for_file(self, file_path: str) -> str:
        """
        Get the appropriate language ID for a given file path.
        Vue files are treated as typescript since they contain TS in script blocks.
        """
        if file_path.endswith('.vue'):
            # Use 'typescript' for Vue files to avoid language ID errors
            # The Vue plugin will handle the Vue-specific parsing
            return 'typescript'
        elif file_path.endswith(('.js', '.jsx', '.mjs', '.cjs')):
            return 'javascript'
        else:  # .ts, .tsx, etc.
            return 'typescript'
    
    @contextmanager
    def open_file(self, relative_file_path: str):
        """
        Override open_file to use the correct language ID for Vue files.
        """
        if not self.server_started:
            self.logger.log(
                "open_file called before Language Server started",
                logging.ERROR,
            )
            raise SolidLSPException("Language Server not started")

        absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
        uri = pathlib.Path(absolute_file_path).as_uri()

        if uri in self.open_file_buffers:
            assert self.open_file_buffers[uri].uri == uri
            assert self.open_file_buffers[uri].ref_count >= 1

            self.open_file_buffers[uri].ref_count += 1
            yield self.open_file_buffers[uri]
            self.open_file_buffers[uri].ref_count -= 1
        else:
            contents = FileUtils.read_file(self.logger, absolute_file_path)

            version = 0
            # Use the correct language ID based on file extension
            language_id = self._get_language_id_for_file(relative_file_path)
            self.open_file_buffers[uri] = LSPFileBuffer(uri, contents, version, language_id, 1)

            self.server.notify.did_open_text_document(
                {
                    LSPConstants.TEXT_DOCUMENT: {
                        LSPConstants.URI: uri,
                        LSPConstants.LANGUAGE_ID: language_id,
                        LSPConstants.VERSION: 0,
                        LSPConstants.TEXT: contents,
                    }
                }
            )
            yield self.open_file_buffers[uri]
            self.open_file_buffers[uri].ref_count -= 1

        if uri in self.open_file_buffers and self.open_file_buffers[uri].ref_count == 0:
            self.server.notify.did_close_text_document(
                {
                    LSPConstants.TEXT_DOCUMENT: {
                        LSPConstants.URI: uri,
                    }
                }
            )
            del self.open_file_buffers[uri]

    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        return super().is_ignored_dirname(dirname) or dirname in [
            "node_modules",
            "dist",
            "build",
            "coverage",
        ]

    @classmethod
    def _setup_runtime_dependencies(
        cls, logger: LanguageServerLogger, config: LanguageServerConfig, solidlsp_settings: SolidLSPSettings
    ) -> list[str]:
        """
        Setup runtime dependencies for TypeScript Language Server and return the command to start the server.
        """
        platform_id = PlatformUtils.get_platform_id()

        valid_platforms = [
            PlatformId.LINUX_x64,
            PlatformId.LINUX_arm64,
            PlatformId.OSX,
            PlatformId.OSX_x64,
            PlatformId.OSX_arm64,
            PlatformId.WIN_x64,
            PlatformId.WIN_arm64,
        ]
        assert platform_id in valid_platforms, f"Platform {platform_id} is not supported for multilspy javascript/typescript at the moment"

        deps = RuntimeDependencyCollection(
            [
                RuntimeDependency(
                    id="typescript",
                    description="typescript package",
                    command=["npm", "install", "--prefix", "./", "typescript@5.5.4"],
                    platform_id="any",
                ),
                RuntimeDependency(
                    id="typescript-language-server",
                    description="typescript-language-server package",
                    command=["npm", "install", "--prefix", "./", "typescript-language-server@4.3.3"],
                    platform_id="any",
                ),
            ]
        )

        # Verify both node and npm are installed
        is_node_installed = shutil.which("node") is not None
        assert is_node_installed, "node is not installed or isn't in PATH. Please install NodeJS and try again."
        is_npm_installed = shutil.which("npm") is not None
        assert is_npm_installed, "npm is not installed or isn't in PATH. Please install npm and try again."

        # Verify both node and npm are installed
        is_node_installed = shutil.which("node") is not None
        assert is_node_installed, "node is not installed or isn't in PATH. Please install NodeJS and try again."
        is_npm_installed = shutil.which("npm") is not None
        assert is_npm_installed, "npm is not installed or isn't in PATH. Please install npm and try again."

        # Install typescript and typescript-language-server if not already installed
        tsserver_ls_dir = os.path.join(cls.ls_resources_dir(solidlsp_settings), "ts-lsp")
        tsserver_executable_path = os.path.join(tsserver_ls_dir, "node_modules", ".bin", "typescript-language-server")
        if not os.path.exists(tsserver_executable_path):
            logger.log(f"Typescript Language Server executable not found at {tsserver_executable_path}. Installing...", logging.INFO)
            with LogTime("Installation of TypeScript language server dependencies", logger=logger.logger):
                deps.install(logger, tsserver_ls_dir)

        if not os.path.exists(tsserver_executable_path):
            raise FileNotFoundError(
                f"typescript-language-server executable not found at {tsserver_executable_path}, something went wrong with the installation."
            )
        return [tsserver_executable_path, "--stdio"]

    @staticmethod
    def _get_initialize_params(repository_absolute_path: str) -> InitializeParams:
        """
        Returns the initialize params for the TypeScript Language Server.
        """
        root_uri = pathlib.Path(repository_absolute_path).as_uri()
        
        # Check if @vue/typescript-plugin is available
        vue_plugin_path = None
        potential_paths = [
            os.path.join(repository_absolute_path, "node_modules", "@vue", "typescript-plugin"),
            os.path.join(repository_absolute_path, "..", "node_modules", "@vue", "typescript-plugin"),
            os.path.join(os.path.dirname(repository_absolute_path), "node_modules", "@vue", "typescript-plugin"),
        ]
        
        for path in potential_paths:
            if os.path.exists(path):
                vue_plugin_path = path
                break
        
        initialization_options = {}
        if vue_plugin_path:
            initialization_options["plugins"] = [
                {
                    "name": "@vue/typescript-plugin",
                    "location": vue_plugin_path,
                    "languages": ["vue"]
                }
            ]
        
        initialize_params = {
            "locale": "en",
            "initializationOptions": initialization_options,
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didSave": True, "dynamicRegistration": True},
                    "completion": {"dynamicRegistration": True, "completionItem": {"snippetSupport": True}},
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                    },
                    "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                    "signatureHelp": {"dynamicRegistration": True},
                    "codeAction": {"dynamicRegistration": True},
                },
                "workspace": {
                    "workspaceFolders": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "symbol": {"dynamicRegistration": True},
                },
            },
            "processId": os.getpid(),
            "rootPath": repository_absolute_path,
            "rootUri": root_uri,
            "workspaceFolders": [
                {
                    "uri": root_uri,
                    "name": os.path.basename(repository_absolute_path),
                }
            ],
        }
        return initialize_params

    def _start_server(self):
        """
        Starts the TypeScript Language Server, waits for the server to be ready and yields the LanguageServer instance.

        Usage:
        ```
        async with lsp.start_server():
            # LanguageServer has been initialized and ready to serve requests
            await lsp.request_definition(...)
            await lsp.request_references(...)
            # Shutdown the LanguageServer on exit from scope
        # LanguageServer has been shutdown
        """

        def register_capability_handler(params):
            assert "registrations" in params
            for registration in params["registrations"]:
                if registration["method"] == "workspace/executeCommand":
                    self.initialize_searcher_command_available.set()
                    # TypeScript doesn't have a direct equivalent to resolve_main_method
                    # You might want to set a different flag or remove this line
                    # self.resolve_main_method_available.set()
            return

        def execute_client_command_handler(params):
            return []

        def do_nothing(params):
            return

        def window_log_message(msg):
            self.logger.log(f"LSP: window/logMessage: {msg}", logging.INFO)

        def check_experimental_status(params):
            """
            Also listen for experimental/serverStatus as a backup signal
            """
            if params.get("quiescent") == True:
                self.server_ready.set()
                self.completions_available.set()

        self.server.on_request("client/registerCapability", register_capability_handler)
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_request("workspace/executeClientCommand", execute_client_command_handler)
        self.server.on_notification("$/progress", do_nothing)
        self.server.on_notification("textDocument/publishDiagnostics", do_nothing)
        self.server.on_notification("experimental/serverStatus", check_experimental_status)

        self.logger.log("Starting TypeScript server process", logging.INFO)
        self.server.start()
        initialize_params = self._get_initialize_params(self.repository_root_path)

        self.logger.log(
            "Sending initialize request from LSP client to LSP server and awaiting response",
            logging.INFO,
        )
        init_response = self.server.send.initialize(initialize_params)

        # TypeScript-specific capability checks
        assert init_response["capabilities"]["textDocumentSync"] == 2
        assert "completionProvider" in init_response["capabilities"]
        assert init_response["capabilities"]["completionProvider"] == {
            "triggerCharacters": [".", '"', "'", "/", "@", "<"],
            "resolveProvider": True,
        }

        self.server.notify.initialized({})
        if self.server_ready.wait(timeout=1.0):
            self.logger.log("TypeScript server is ready", logging.INFO)
        else:
            self.logger.log("Timeout waiting for TypeScript server to become ready, proceeding anyway", logging.INFO)
            # Fallback: assume server is ready after timeout
            self.server_ready.set()
        self.completions_available.set()

    @override
    # For some reason, the LS may need longer to process this, so we just retry
    def _send_references_request(self, relative_file_path: str, line: int, column: int):
        # TODO: The LS doesn't return references contained in other files if it doesn't sleep. This is
        #   despite the LS having processed requests already. I don't know what causes this, but sleeping
        #   one second helps. It may be that sleeping only once is enough but that's hard to reliably test.
        #   It may be that even this 1sec is not enough in larger TS projects, at some point we should find what
        #   causes this and solve it.
        sleep(1)
        return super()._send_references_request(relative_file_path, line, column)
