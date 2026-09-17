# Makes tests/image_pipeline importable as a package, so its conftest resolves
# as image_pipeline.conftest and never shadows the bare `conftest` module the
# sibling suites (tests/image_gen_alibaba) import from tests/conftest.py.
