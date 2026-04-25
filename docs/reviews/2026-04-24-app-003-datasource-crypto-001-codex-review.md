# APP-003-DATASOURCE-CRYPTO-001 Review

Date: 2026-04-24
Reviewer: Codex
Task: `APP-003-DATASOURCE-CRYPTO-001`
Owner: `Codex2`
Disposition: changes_requested

## Scope Reviewed

- `services/execution/kraken_adapter.py`
- `services/execution/lean_runtime/symbol_parser.py`
- `services/execution/test_kraken_adapter.py`
- `services/research/adapters/coingecko_client.py`
- `services/research/adapters/test_adapters.py`
- `services/data-plane/crypto_reference.py`
- `services/data-plane/tests/test_data_plane_schemas.py`
- `services/data-plane/README.md`
- `DATA_SOURCE_SCOPE_MATRIX.md`

## Findings

1. The original adapter-to-join drop-price bug is fixed, but `KrakenAdapter.normalize_quote()` still collapses distinct `last` and `close` inputs into the same value, so the normalized payload is now lossy instead of missing.

- `join_kraken_quote_with_reference()` now consumes the normalized adapter payload correctly through `quote.get("close")` in `services/data-plane/crypto_reference.py:125-155`, and the schema regression now pipes `KrakenAdapter.normalize_quote(...).to_dict()` into that join in `services/data-plane/tests/test_data_plane_schemas.py:619-648`.
- The remaining issue is in `services/execution/kraken_adapter.py:202-213`: `normalize_quote()` computes one `last_price` from `payload["last"] or payload["close"] or payload["price"]` and writes that same value into both `last` and `close`.
- That means a payload carrying both values loses its original `close`. Example: an input with `last=64321.1` and `close=64320.4` normalizes to `last=64321.1` and `close=64321.1`, and the joined row also reports `quote_close=64321.1`.
- The current adapter and join tests only cover payloads where `close` is the only price-like input, so they do not catch this lossy normalization in `services/execution/test_kraken_adapter.py:49-68` and `services/data-plane/tests/test_data_plane_schemas.py:619-648`.
- Reproduced locally with:

```bash
python3 -c "from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig; import importlib.util, pathlib; path=pathlib.Path('services/data-plane/crypto_reference.py'); spec=importlib.util.spec_from_file_location('crypto_reference', path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); from services.research.adapters.coingecko_client import CoinGeckoClient; adapter=KrakenAdapter(KrakenConfig(api_key='k', api_secret='s')); quote=adapter.normalize_quote({'ts':'2026-04-24T16:00:00Z','last':'64321.1','close':'64320.4','bid':'64320.1','ask':'64321.0','volume':'128.55'}, 'BTCUSD.KRAKEN').to_dict(); metadata=CoinGeckoClient(rate_limit_delay=0).normalize_asset({'id':'bitcoin','symbol':'btc','name':'Bitcoin','market_cap_rank':1}).to_dict(); joined=mod.join_kraken_quote_with_reference([quote],[metadata]); print({'quote_last': quote['last'], 'quote_close': quote['close'], 'joined_quote_close': joined[0]['quote_close']})"
```

- Result:

```text
{'quote_last': 64321.1, 'quote_close': 64321.1, 'joined_quote_close': 64321.1}
```

- Required fix: preserve `last` and `close` separately when both are present, while still falling back between them when only one is provided. Then add regression coverage at both the adapter level and the adapter-to-join boundary using distinct `last` / `close` values.

## Verification

Executed locally:

```bash
python3 -m unittest services.execution.test_kraken_adapter services.research.adapters.test_adapters services.data-plane.tests.test_data_plane_schemas -v
python3 services/data-plane/smoke_test.py
python3 -c "from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig; import importlib.util, pathlib; path=pathlib.Path('services/data-plane/crypto_reference.py'); spec=importlib.util.spec_from_file_location('crypto_reference', path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); from services.research.adapters.coingecko_client import CoinGeckoClient; adapter=KrakenAdapter(KrakenConfig(api_key='k', api_secret='s')); quote=adapter.normalize_quote({'ts':'2026-04-24T16:00:00Z','close':'64320.4','bid':'64320.1','ask':'64321.0','volume':'128.55'}, 'BTCUSD.KRAKEN').to_dict(); metadata=CoinGeckoClient(rate_limit_delay=0).normalize_asset({'id':'bitcoin','symbol':'btc','name':'Bitcoin','market_cap_rank':1}).to_dict(); joined=mod.join_kraken_quote_with_reference([quote],[metadata]); print({'quote_last': quote['last'], 'quote_close': quote['close'], 'joined_quote_close': joined[0]['quote_close']})"
python3 -c "from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig; import importlib.util, pathlib; path=pathlib.Path('services/data-plane/crypto_reference.py'); spec=importlib.util.spec_from_file_location('crypto_reference', path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); from services.research.adapters.coingecko_client import CoinGeckoClient; adapter=KrakenAdapter(KrakenConfig(api_key='k', api_secret='s')); quote=adapter.normalize_quote({'ts':'2026-04-24T16:00:00Z','last':'64321.1','close':'64320.4','bid':'64320.1','ask':'64321.0','volume':'128.55'}, 'BTCUSD.KRAKEN').to_dict(); metadata=CoinGeckoClient(rate_limit_delay=0).normalize_asset({'id':'bitcoin','symbol':'btc','name':'Bitcoin','market_cap_rank':1}).to_dict(); joined=mod.join_kraken_quote_with_reference([quote],[metadata]); print({'quote_last': quote['last'], 'quote_close': quote['close'], 'joined_quote_close': joined[0]['quote_close']})"
```

Result:

- `services.execution.test_kraken_adapter`, `services.research.adapters.test_adapters`, and `services.data-plane.tests.test_data_plane_schemas`: passed (`78` tests total)
- `services/data-plane/smoke_test.py`: passed (`47 / 47` checks)
- The adapter-to-join boundary now carries the normalized price through correctly when only `close` is present (`{'quote_last': 64320.4, 'quote_close': 64320.4, 'joined_quote_close': 64320.4}`), which confirms the previous drop-price bug is fixed.
- The distinct-value reproduction still fails semantically (`{'quote_last': 64321.1, 'quote_close': 64321.1, 'joined_quote_close': 64321.1}` even though the input carried `close=64320.4`), which blocks approval because the landed adapter still rewrites a payload-provided close value.
