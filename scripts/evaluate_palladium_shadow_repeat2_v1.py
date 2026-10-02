"""Read-only evaluator entrypoint for Palladium frozen-v1 coverage repeat2."""
from pathlib import Path

import palladium_shadow_repeat2_v1 as repeat

repeat.configure_core()

import evaluate_palladium_shadow as evaluator  # noqa: E402

evaluator.OUTPUT = Path("research/palladium-shadow-repeat2-v1-evaluation.json")
evaluator.MARKDOWN = Path("research/palladium-shadow-repeat2-v1-evaluation.md")
evaluator.SOURCE_FILES = tuple(evaluator.SOURCE_FILES) + (
    repeat.MANIFEST,
    Path(repeat.__file__),
)


if __name__ == "__main__":
    evaluator.main()
