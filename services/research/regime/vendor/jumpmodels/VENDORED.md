# Vendored libraries

## jumpmodels 0.1.1

- Upstream: https://github.com/Yizhan-Oliver-Shu/jump-models (PyPI `jumpmodels` 0.1.1, uploaded 2024-10-04)
- Licence: Apache-2.0 (`LICENSE` beside this file, copied unchanged from the sdist)
- Source: PyPI sdist `jumpmodels-0.1.1.tar.gz`, sha256 `4d4ec6ceb8877a4fb1a148193dd6e2c6eaf761d26d613bd9ca64074cbb0d75ff`
- Pinned at: 0.1.1. Only change from the sdist: `plot.py` is not vendored, because it is the only
  module that imports matplotlib and the research image does not install matplotlib. Every other
  file is byte-identical.
- Loaded through `services.research.regime.load_jumpmodels()`; the vendored copy is never
  installed into site-packages and no regime code imports a different copy.
- Why vendored (design K1): one maintainer and no release since 2024. Our own tests under
  `services/research/regime/tests/` cover the behaviour we depend on (continuous model, online prediction).
