"""What a recipe is: a named generator that turns a few parameters into explicit layers and a chain.

A parameter may vary between a sound's variants. How it varies is part of its definition:
  cents  multiplied by 2^(u * amount / 1200)    (frequencies)
  pct    multiplied by (1 + u * amount / 100)    (times)
  abs    plus u * amount                          (amounts between 0 and 1)
  db     plus u * amount                          (levels)
with u drawn evenly from -1 to 1 for each variant; the first variant keeps the written values. `amount` is the
default; a project can change it per sound. Every value is kept within the parameter's range."""
from dataclasses import dataclass, field

VARY_MODES = ('cents', 'pct', 'abs', 'db')


@dataclass(frozen=True)
class Param:
    name: str
    default: float
    lo: float
    hi: float
    unit: str = ''
    vary: str = None
    amount: float = 0.0
    help: str = ''

    def __post_init__(self):
        if self.vary is not None and self.vary not in VARY_MODES:
            raise ValueError(f'{self.name}: vary must be one of {VARY_MODES}')
        if not self.lo <= self.default <= self.hi:
            raise ValueError(f'{self.name}: default {self.default} outside {self.lo}..{self.hi}')


@dataclass(frozen=True)
class Recipe:
    name: str
    category: str
    summary: str
    params: tuple
    build: object                      # callable(dict of parameter values) -> {'layers': [...], 'chain': {...}}
    trim_db: float = 0.0               # the sound's default level against the rest of the set
    rules: tuple = field(default=())   # (rule as written into the recipe, its source)
    use: str = 'one_shot'              # 'loop': build also receives 'seconds', the loop's length, and makes beds of it
    crackle: bool = False              # its sound crackles on purpose (debris, sparks): isolated clicks are designed

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        return None

    def defaults(self):
        return {p.name: float(p.default) for p in self.params}
