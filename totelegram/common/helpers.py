import sys
from typing import Any, Iterable, List

if sys.version_info >= (3, 12):
    from itertools import batched
else:

    def batched(iterable: List[Any], n: int) -> Iterable[Any]:
        """Fallback para Python < 3.12."""
        import itertools

        it = iter(iterable)
        while batch := list(itertools.islice(it, n)):
            yield batch
