"""OTel provider setup: TracerProvider, span processor, HTTP/DB/file instrumentation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.instrumentation.interceptors._runtime import configure as configure_hooks
from openbox.instrumentation.interceptors._runtime import reset as reset_hooks
from openbox.instrumentation.interceptors.file_io import (
    setup_file_io_instrumentation,
    teardown_file_io_instrumentation,
)
from openbox.instrumentation.interceptors.httpx_client import (
    httpx_async_request_hook,
    httpx_async_response_hook,
    httpx_request_hook,
    httpx_response_hook,
    setup_httpx_body_capture,
    teardown_httpx_body_capture,
)
from openbox.instrumentation.interceptors.requests import (
    requests_request_hook,
    requests_response_hook,
)
from openbox.instrumentation.interceptors.urllib3 import (
    urllib3_request_hook,
    urllib3_response_hook,
)
from openbox.instrumentation.span_processor import GovernanceSpanProcessor

if TYPE_CHECKING:
    from openbox.engine import OpenBoxEngine

logger = logging.getLogger("openbox")

_instrumented_libraries: set[str] = set()


@dataclass(frozen=True, slots=True)
class InstrumentationSettings:
    instrument_databases: bool
    db_libraries: frozenset[str] | None
    instrument_file_io: bool


def build_instrumentation_settings(config: GovernanceConfig) -> InstrumentationSettings:
    return InstrumentationSettings(
        instrument_databases=config.instrument_databases,
        db_libraries=frozenset(config.db_libraries) if config.db_libraries is not None else None,
        instrument_file_io=config.instrument_file_io,
    )


def start_engine_instrumentation(
    engine: OpenBoxEngine,
    settings: InstrumentationSettings,
) -> GovernanceSpanProcessor:
    span_processor = GovernanceSpanProcessor(engine=engine)

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(span_processor)

    configure_hooks(engine=engine)

    _instrument_requests()
    _instrument_httpx()
    _instrument_urllib3()

    if settings.instrument_databases:
        _instrument_databases(set(settings.db_libraries) if settings.db_libraries else None)

    if settings.instrument_file_io:
        setup_file_io_instrumentation()
        _instrumented_libraries.add("file_io")

    return span_processor


def setup_otel_governance(
    governance_client: GovernanceClient,
    config: GovernanceConfig,
    api_url: str,
) -> GovernanceSpanProcessor:
    ignored_prefixes = {api_url.rstrip("/")}

    span_processor = GovernanceSpanProcessor(
        governance_client=governance_client,
        config=config,
        ignored_url_prefixes=ignored_prefixes,
    )

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(span_processor)

    configure_hooks(
        span_processor=span_processor,
        governance_client=governance_client,
        config=config,
        ignored_url_prefixes=ignored_prefixes,
    )

    _instrument_requests()
    _instrument_httpx()
    _instrument_urllib3()

    if config.instrument_databases:
        _instrument_databases(config.db_libraries)

    if config.instrument_file_io:
        setup_file_io_instrumentation()
        _instrumented_libraries.add("file_io")

    return span_processor


def teardown_otel_governance() -> None:
    if "requests" in _instrumented_libraries:
        _uninstrument_requests()
    if "httpx" in _instrumented_libraries:
        _uninstrument_httpx()
    if "urllib3" in _instrumented_libraries:
        _uninstrument_urllib3()
    if "file_io" in _instrumented_libraries:
        teardown_file_io_instrumentation()

    db_libs_instrumented = any(
        lib.startswith("db_") for lib in _instrumented_libraries
    )
    for lib in list(_instrumented_libraries):
        if lib.startswith("db_"):
            _uninstrument_db(lib.removeprefix("db_"))
    if db_libs_instrumented:
        from openbox.instrumentation.interceptors import db as _db_gov

        _db_gov.uninstrument_all()

    _instrumented_libraries.clear()
    reset_hooks()


def _instrument_requests() -> None:
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        instrumentor = RequestsInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(
                request_hook=requests_request_hook,
                response_hook=requests_response_hook,
            )
        _instrumented_libraries.add("requests")
        logger.debug("Instrumented requests library")
    except ImportError:
        logger.debug("requests instrumentation not available")
    except Exception:
        logger.exception("Error instrumenting requests")


def _uninstrument_requests() -> None:
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().uninstrument()
    except Exception:
        logger.debug("Error uninstrumenting requests")


def _instrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        instrumentor = HTTPXClientInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(
                request_hook=httpx_request_hook,
                response_hook=httpx_response_hook,
                async_request_hook=httpx_async_request_hook,
                async_response_hook=httpx_async_response_hook,
            )

        setup_httpx_body_capture()
        _instrumented_libraries.add("httpx")
        logger.debug("Instrumented httpx library")
    except ImportError:
        logger.debug("httpx instrumentation not available")
    except Exception:
        logger.exception("Error instrumenting httpx")


def _uninstrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().uninstrument()
        teardown_httpx_body_capture()
    except Exception:
        logger.debug("Error uninstrumenting httpx")


def _instrument_urllib3() -> None:
    try:
        from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor

        instrumentor = URLLib3Instrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(
                request_hook=urllib3_request_hook,
                response_hook=urllib3_response_hook,
            )
        _instrumented_libraries.add("urllib3")
        logger.debug("Instrumented urllib3 library")
    except ImportError:
        logger.debug("urllib3 instrumentation not available")
    except Exception:
        logger.exception("Error instrumenting urllib3")


def _uninstrument_urllib3() -> None:
    try:
        from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor

        URLLib3Instrumentor().uninstrument()
    except Exception:
        logger.debug("Error uninstrumenting urllib3")


# Value is (module_path, class_name) of the upstream OTel instrumentor, or
# None for drivers governed entirely through the wrapt-based hooks below
# (no OTel instrumentor exists for them).
_DB_INSTRUMENTORS: dict[str, tuple[str, str] | None] = {
    "psycopg2": ("opentelemetry.instrumentation.psycopg2", "Psycopg2Instrumentor"),
    "asyncpg": ("opentelemetry.instrumentation.asyncpg", "AsyncPGInstrumentor"),
    "mysql": ("opentelemetry.instrumentation.mysql", "MySQLInstrumentor"),
    "pymysql": ("opentelemetry.instrumentation.pymysql", "PyMySQLInstrumentor"),
    "pymongo": ("opentelemetry.instrumentation.pymongo", "PymongoInstrumentor"),
    "redis": ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
    "sqlalchemy": ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
    "aiopg": None,
    "psycopg3": None,
}


def _instrument_databases(db_libraries: set[str] | None) -> None:
    targets = db_libraries if db_libraries else set(_DB_INSTRUMENTORS.keys())

    from openbox.instrumentation.interceptors import db as _db_gov

    for lib_name in targets:
        if lib_name not in _DB_INSTRUMENTORS:
            logger.warning("Unknown DB library: %s", lib_name)
            continue
        spec = _DB_INSTRUMENTORS[lib_name]
        if spec is None:
            continue
        module_path, class_name = spec
        try:
            import importlib

            module = importlib.import_module(module_path)
            instrumentor_class = getattr(module, class_name)
            instrumentor = instrumentor_class()
            if not instrumentor.is_instrumented_by_opentelemetry:
                if lib_name == "redis":
                    req_hook, resp_hook = _db_gov.setup_redis_hooks()
                    instrumentor.instrument(
                        request_hook=req_hook, response_hook=resp_hook
                    )
                else:
                    instrumentor.instrument()
            _instrumented_libraries.add(f"db_{lib_name}")
            logger.debug("Instrumented %s", lib_name)
        except ImportError:
            logger.debug("%s instrumentation not available", lib_name)
        except Exception:
            logger.debug("Error instrumenting %s", lib_name, exc_info=True)

    _db_gov.install_cursor_tracer_hooks()
    if "psycopg2" in targets:
        _db_gov.install_psycopg2_hooks()

    if "asyncpg" in targets:
        _db_gov.install_asyncpg_hooks()

    if "pymongo" in targets:
        _db_gov.setup_pymongo_hooks()

    if "aiopg" in targets:
        if _db_gov.install_aiopg_hooks():
            _instrumented_libraries.add("db_aiopg")

    if "psycopg3" in targets:
        if _db_gov.install_psycopg3_async_hooks():
            _instrumented_libraries.add("db_psycopg3")


def _uninstrument_db(lib_name: str) -> None:
    spec = _DB_INSTRUMENTORS.get(lib_name)
    if spec is None:
        return

    module_path, class_name = spec
    try:
        import importlib

        module = importlib.import_module(module_path)
        instrumentor_class = getattr(module, class_name)
        instrumentor_class().uninstrument()
    except Exception:
        logger.debug("Error uninstrumenting %s", lib_name)
